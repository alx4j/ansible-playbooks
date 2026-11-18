#!/usr/bin/python3
"""Validate a proposed SSH drop-in and check the installed effective policy."""

import fnmatch
import glob
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


def include_paths(pattern, target):
    matches = set(glob.glob(str(pattern)))
    # glob only returns existing files. Insert a new candidate at the position
    # it would have after installation, without letting a wildcard cross '/'.
    if (not target.exists() and fnmatch.fnmatchcase(target.name, pattern.name)
            and (not target.name.startswith(".") or pattern.name.startswith("."))):
        for directory in glob.glob(str(pattern.parent)):
            if Path(directory).resolve() == target.parent:
                matches.add(str(Path(directory) / target.name))
    return sorted(matches)


def stage_config(path, target, candidate, ssh_directory, staging, stack=()):
    """Substitute the candidate while keeping a separate file for each Include."""
    path = path.resolve()
    if path in stack:
        raise ValueError("Recursive SSH Include detected")
    content = candidate if path == target else path.read_text()
    found = path == target
    with tempfile.NamedTemporaryFile(mode="w", dir=staging, delete=False) as staged:
        for line in content.splitlines(keepends=True):
            directive = re.match(r"^\s*include(?:\s*=\s*|\s+)(.*)$", line, re.IGNORECASE)
            if not directive:
                staged.write(line)
                continue
            patterns = shlex.split(directive[1], comments=True)
            if not patterns:
                raise ValueError("SSH Include requires a path")
            for pattern in patterns:
                for source in include_paths(ssh_directory / pattern, target):
                    child, included = stage_config(
                        Path(source), target, candidate, ssh_directory, staging, (*stack, path)
                    )
                    staged.write(f'Include "{child}"\n')
                    found |= included
    return Path(staged.name), found


def check_policy(expected, config, sshd):
    result = subprocess.run([sshd, "-T", "-f", str(config)], check=True, capture_output=True, text=True)
    effective = dict(line.split(None, 1) for line in result.stdout.splitlines() if " " in line)
    for key, value in zip(
        ("permitrootlogin", "passwordauthentication", "kbdinteractiveauthentication"), expected
    ):
        observed = effective.get(key)
        if key == "permitrootlogin" and value == "prohibit-password":
            valid = observed in ("prohibit-password", "without-password")
        else:
            valid = observed == value
        if not valid:
            raise ValueError(f"Effective {key} differs from requested policy; inspect the SSH configuration")


def validate(candidate_path, target_path, expected, config=Path("/etc/ssh/sshd_config"), sshd="/usr/sbin/sshd"):
    with tempfile.TemporaryDirectory(prefix="ansible-sshd-") as staging:
        staged, found = stage_config(config, Path(target_path).resolve(), Path(candidate_path).read_text(), config.parent, staging)
        if not found:
            raise ValueError("The main SSH configuration does not include the managed drop-in")
        subprocess.run([sshd, "-t", "-f", str(staged)], check=True, capture_output=True, text=True)
        check_policy(expected, staged, sshd)


def main():
    try:
        if len(sys.argv) == 5 and sys.argv[1] == "--installed":
            check_policy(sys.argv[2:], Path("/etc/ssh/sshd_config"), "/usr/sbin/sshd")
        elif len(sys.argv) == 6:
            validate(sys.argv[1], sys.argv[2], sys.argv[3:])
        else:
            raise SystemExit("Usage: validate-ssh-dropin {CANDIDATE TARGET | --installed} ROOT_LOGIN PASSWORD_AUTH KEYBOARD_AUTH")
    except subprocess.CalledProcessError as error:
        raise SystemExit("SSH validation failed; inspect the configuration locally") from error
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
