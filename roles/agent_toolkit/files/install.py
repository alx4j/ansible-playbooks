#!/usr/bin/env python3
"""Reconcile packaged Codex plugins without modifying a development checkout."""

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid


MARKETPLACE = "alx4j-agent-toolkit"


class ToolkitError(Exception):
    pass


def run(arguments, home, *, extra_env=None, timeout=120):
    environment = {"HOME": str(home), "CODEX_HOME": str(home / ".codex"),
                   "PATH": f"{home}/.local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    result = subprocess.run(list(map(str, arguments)), env=environment | (extra_env or {}), cwd=home,
                            text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ToolkitError("Command failed: " + " ".join(map(str, arguments[:3]))
                           + ". Inspect the managed checkout or Codex configuration.")
    return result.stdout.strip()


def check_checkout(checkout, repository, home):
    if checkout.is_symlink():
        raise ToolkitError("The managed toolkit checkout must not be a symlink.")
    marker = checkout / ".git/ansible-agent-toolkit.json"
    if not checkout.is_dir() or not (checkout / ".git").is_dir() or (checkout / ".git").is_symlink():
        raise ToolkitError("The managed toolkit path is not a regular Git checkout.")
    if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text()) != {"repository": repository}:
        raise ToolkitError("The existing toolkit checkout is not owned by this automation; it was preserved.")
    def git(*args):
        return run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", checkout, *args], home)
    if git("config", "--local", "--get", "remote.origin.url") != repository:
        raise ToolkitError("The managed toolkit origin changed; it was preserved.")
    if git("status", "--porcelain", "--untracked-files=all", "--ignored") or git("branch", "--show-current") != "main":
        raise ToolkitError("The managed toolkit has local changes or another branch; move development work to PROJECTS_DIR.")
    if git("rev-list", "--count", "origin/main..HEAD") != "0":
        raise ToolkitError("The managed toolkit has local commits; they were preserved.")


def publish_checkout(staged, checkout):
    # RENAME_NOREPLACE also protects an empty directory created during the clone.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if rename(-100, os.fsencode(staged), -100, os.fsencode(checkout), 1):
        raise OSError(ctypes.get_errno(), "Could not publish the managed checkout.")


def prepare_checkout(checkout, repository, home, ssh_options):
    if checkout.exists() or checkout.is_symlink():
        check_checkout(checkout, repository, home)
        return False
    with tempfile.TemporaryDirectory(prefix=".agent-toolkit-", dir=checkout.parent) as temporary:
        staged = Path(temporary) / "checkout"
        git_env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_SSH_COMMAND": shlex.join(["/usr/bin/ssh", *ssh_options])}
        run(["/usr/bin/git", "clone", "--branch", "main", "--no-recurse-submodules", "--", repository, staged],
            home, timeout=600, extra_env=git_env)
        marker = staged / ".git/ansible-agent-toolkit.json"
        with os.fdopen(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
            json.dump({"repository": repository}, stream)
            stream.write("\n")
        try:
            publish_checkout(staged, checkout)
        except FileExistsError:
            check_checkout(checkout, repository, home)
            return False
    return True


def fingerprint(directory):
    if directory.is_symlink() or not directory.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ToolkitError("Plugin packages must contain regular files, not symlinks.")
        if path.is_file():
            digest.update(str(relative).encode() + b"\0")
            digest.update(str(path.stat().st_mode & 0o111).encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Installer:
    def __init__(self, checkout, home, plugins, manage_instructions=True):
        self.checkout, self.home, self.plugins = checkout, home, plugins
        self.codex_home = home / ".codex"
        self.manage_instructions = manage_instructions
        self.report = {"changed": False, "installed": [], "backups": [], "warnings": []}

    def cli(self, *arguments):
        return json.loads(run([self.home / ".local/bin/codex", "plugin", *arguments, "--json"], self.home))

    def packages(self):
        catalog = json.loads((self.checkout / ".agents/plugins/marketplace.json").read_text())
        if catalog["name"] != MARKETPLACE or not isinstance(self.plugins, list) or "common" not in self.plugins:
            raise ToolkitError("Use the alx4j-agent-toolkit marketplace and include the common plugin.")
        entries = {entry["name"]: entry for entry in catalog["plugins"]}
        result = {}
        for name in self.plugins:
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name not in entries:
                raise ToolkitError("A selected toolkit plugin is missing or has an invalid name.")
            source = entries[name]["source"]
            path = self.checkout / source["path"]
            if source["source"] != "local" or not path.resolve().is_relative_to(self.checkout.resolve()):
                raise ToolkitError("Toolkit plugin sources must stay inside the managed checkout.")
            portable = json.loads((path / "plugin.json").read_text())
            compatibility = path / ".codex-plugin/plugin.json"
            if not compatibility.is_file():
                raise ToolkitError("Codex 0.169.0 requires .codex-plugin/plugin.json; merge the toolkit compatibility update first.")
            compatible = json.loads(compatibility.read_text())
            version = portable["version"]
            if portable["name"] != name or compatible["name"] != name or compatible["version"] != version:
                raise ToolkitError("Portable and Codex compatibility manifest names and versions must match.")
            if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", version):
                raise ToolkitError("Toolkit plugin versions must be safe package version strings.")
            checksum = fingerprint(path)
            if checksum is None:
                raise ToolkitError("Toolkit plugin sources must be regular directories.")
            result[name] = (path, version, checksum)
        return result

    def backup(self, path):
        destination = self.home / ".local/share/agent-toolkit-backups" / uuid.uuid4().hex
        destination.mkdir(parents=True, mode=0o700)
        target = destination / path.name
        path.rename(target)
        self.report["backups"].append(str(target))
        self.report["changed"] = True

    def install(self):
        packages = self.packages()
        instructions = self.checkout / "config/codex/AGENTS.md"
        if self.manage_instructions and (not instructions.is_file() or instructions.is_symlink()):
            raise ToolkitError("The toolkit must contain a regular config/codex/AGENTS.md file.")
        marketplaces = self.cli("marketplace", "list")["marketplaces"]
        existing = [entry for entry in marketplaces if entry["name"] == MARKETPLACE]
        if existing and any(Path(entry["root"]) != self.checkout for entry in existing):
            raise ToolkitError("The toolkit marketplace points elsewhere; preserve that setup or switch its source explicitly.")
        if not existing:
            self.cli("marketplace", "add", str(self.checkout))
            self.report["changed"] = True
        installed = {entry["name"]: entry for entry in self.cli("list", "--marketplace", MARKETPLACE)["installed"]}
        for name, (source, version, expected) in packages.items():
            cache = self.codex_home / "plugins/cache" / MARKETPLACE / name / version
            current = installed.get(name, {})
            if current.get("version") != version or not current.get("enabled") or fingerprint(cache) != expected:
                result = self.cli("add", f"{name}@{MARKETPLACE}")
                if Path(result["installedPath"]) != cache or fingerprint(cache) != expected:
                    raise ToolkitError("Codex did not install the expected toolkit package contents.")
                self.report["installed"].append(name)
                self.report["changed"] = True
        # The plugin owns skill discovery; automation uses the separate root-owned client.
        legacy = self.codex_home / "skills/doppler-secrets"
        if legacy.exists() or legacy.is_symlink():
            self.backup(legacy)
        if self.manage_instructions:
            target = self.codex_home / "AGENTS.md"
            if not target.is_symlink() or target.readlink() != instructions:
                if target.exists() or target.is_symlink():
                    self.backup(target)
                target.symlink_to(instructions)
                self.report["changed"] = True
            override = self.codex_home / "AGENTS.override.md"
            if override.is_file() and override.read_text().strip():
                self.report["warnings"].append("AGENTS.override.md takes precedence; it was preserved.")
        return self.report

    def run(self):
        state = self.home / ".local/state/agent-toolkit"
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(state / "install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ToolkitError("Another toolkit installation is running.") from None
            return self.install()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--prepare-checkout", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--ssh-options", default="[]")
    parser.add_argument("--plugins", default='["common", "game-development", "infrastructure", "productivity"]')
    parser.add_argument("--manage-instructions", choices=("yes", "no"), default="yes")
    args = parser.parse_args()
    if os.getuid() == 0:
        raise ToolkitError("Run as the development account, never root.")
    if args.prepare_checkout:
        return {"changed": prepare_checkout(args.checkout, args.repository, Path.home(), json.loads(args.ssh_options))}
    return Installer(args.checkout, Path.home(), json.loads(args.plugins), args.manage_instructions == "yes").run()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except ToolkitError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('{"error":"Toolkit reconciliation failed. No raw diagnostics were emitted."}', file=sys.stderr)
        sys.exit(1)
