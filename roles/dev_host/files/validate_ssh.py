#!/usr/bin/python3
"""Validate a candidate drop-in within the complete SSH Include expansion."""

import glob
import fnmatch
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def expand_config(path, target, candidate, ssh_directory, stack=()):
    """Preserve Include ordering and Match scope; substitute only the target."""
    path = Path(path).resolve()
    if path in stack:
        raise ValueError("Recursive SSH Include detected")
    text = candidate if path == target else path.read_text()
    found = path == target
    output = []
    for line in text.splitlines(keepends=True):
        parts = shlex.split(line, comments=True)
        if not parts or parts[0].lower() != "include":
            output.append(line if line.endswith("\n") else line + "\n")
            continue
        for pattern in parts[1:]:
            absolute = str(ssh_directory / pattern) if not Path(pattern).is_absolute() else pattern
            matches = set(glob.glob(absolute))
            # Include a new drop-in even though the destination doesn't exist yet.
            if fnmatch.fnmatch(str(target), absolute):
                matches.add(str(target))
            for match in sorted(matches):
                expanded, included = expand_config(
                    match, target, candidate, ssh_directory, (*stack, path)
                )
                output.append(expanded)
                found = found or included
    return "".join(output), found


def validate(candidate_path, target_path, expected, config=Path("/etc/ssh/sshd_config"), sshd="/usr/sbin/sshd"):
    target = Path(target_path).resolve()
    expanded, found = expand_config(
        config, target, Path(candidate_path).read_text(), config.parent
    )
    if not found:
        raise ValueError("The main SSH configuration does not include the managed drop-in")
    with tempfile.NamedTemporaryFile(mode="w", prefix="ansible-sshd-", suffix=".conf") as temporary:
        temporary.write(expanded)
        temporary.flush()
        subprocess.run([sshd, "-t", "-f", temporary.name], check=True, capture_output=True, text=True)
        result = subprocess.run(
            [sshd, "-T", "-f", temporary.name], check=True, capture_output=True, text=True
        )
    effective = dict(line.split(None, 1) for line in result.stdout.splitlines() if " " in line)
    for key, value in zip(
        ("permitrootlogin", "passwordauthentication", "kbdinteractiveauthentication"), expected
    ):
        # OpenSSH prints the older alias for prohibit-password on some releases.
        observed = effective.get(key)
        if key == "permitrootlogin" and value == "prohibit-password":
            valid = observed in ("prohibit-password", "without-password")
        else:
            valid = observed == value
        if not valid:
            raise ValueError(f"Effective {key} differs from requested policy; inspect earlier directives")


def main():
    if len(sys.argv) != 6:
        raise SystemExit("Usage: validate-ssh-dropin CANDIDATE TARGET ROOT_LOGIN PASSWORD_AUTH KEYBOARD_AUTH")
    try:
        validate(sys.argv[1], sys.argv[2], sys.argv[3:6])
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # sshd output can contain local configuration; keep failure output bounded.
        if isinstance(error, subprocess.CalledProcessError):
            raise SystemExit("SSH candidate validation failed; inspect the configuration locally") from error
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
