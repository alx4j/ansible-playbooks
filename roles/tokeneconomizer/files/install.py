#!/usr/bin/env python3
"""Install a self-contained TokenEconomizer plugin and preserve its learned state."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


REPOSITORY = "https://github.com/microsoft/TokenEconomizer.git"
NAME = "tokeneconomizer"


class InstallError(Exception):
    pass


def run(arguments, home):
    # Installing a public plugin needs no inherited deployment or API credentials.
    environment = {"HOME": str(home), "CODEX_HOME": str(home / ".codex"),
                   "PATH": f"{home}/.local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8",
                   "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(list(map(str, arguments)), env=environment, cwd=home,
                            text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise InstallError("TokenEconomizer command failed; inspect the managed source and Codex installation.")
    return result.stdout.strip()


def check_checkout(checkout, home):
    if checkout.is_symlink():
        raise InstallError("The managed TokenEconomizer source must not be a symlink.")
    if not checkout.exists():
        return
    git_directory = checkout / ".git"
    marker = git_directory / "ansible-tokeneconomizer.json"
    if not git_directory.is_dir() or git_directory.is_symlink() or marker.is_symlink() or not marker.is_file():
        raise InstallError("The existing TokenEconomizer checkout is not owned by Ansible; it was preserved.")
    recorded = json.loads(marker.read_text())
    def git(*args):
        return run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", checkout, *args], home)
    if recorded.get("repository") != REPOSITORY or git("config", "--local", "--get", "remote.origin.url") != REPOSITORY:
        raise InstallError("The managed TokenEconomizer source has a different origin; it was preserved.")
    if git("status", "--porcelain", "--untracked-files=all") or git("rev-parse", "HEAD") != recorded.get("revision"):
        raise InstallError("The TokenEconomizer source has local changes or commits; they were preserved.")


def fingerprint(directory):
    if directory.is_symlink() or not directory.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise InstallError("The bundled plugin must contain regular files, not symlinks.")
        if path.is_file():
            digest.update(str(relative).encode() + b"\0")
            digest.update(str(path.stat().st_mode & 0o111).encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


class Installer:
    def __init__(self, home, settings):
        self.home, self.settings = home, settings
        self.checkout = home / ".local/share/tokeneconomizer-source"
        self.marketplace = home / ".local/share/tokeneconomizer-marketplace"
        self.codex_home = home / ".codex"
        self.report = {"changed": False, "package_updated": False, "settings_updated": False,
                       "hook_approval": "Review TokenEconomizer in Codex /hooks, then start a new session."}

    def cli(self, *args):
        return json.loads(run([self.home / ".local/bin/codex", "plugin", *args, "--json"], self.home))

    def install(self):
        existing = [entry for entry in self.cli("marketplace", "list")["marketplaces"] if entry["name"] == NAME]
        if any(Path(entry["root"]) != self.marketplace for entry in existing):
            raise InstallError("TokenEconomizer is registered from another marketplace path; it was preserved.")
        marker = self.marketplace / "ansible-managed.json"
        ownership = {"repository": REPOSITORY}
        if self.marketplace.exists() or self.marketplace.is_symlink():
            if (self.marketplace.is_symlink() or not marker.is_file() or marker.is_symlink()
                    or json.loads(marker.read_text()) != ownership):
                raise InstallError("The existing TokenEconomizer bundle is not owned by Ansible; it was preserved.")

        # Build through upstream's supported bundler: Codex otherwise drops the core symlink.
        with tempfile.TemporaryDirectory(prefix="tokeneconomizer-", dir=self.marketplace.parent) as directory:
            staged = Path(directory) / "marketplace"
            run(["/usr/bin/python3", self.checkout / "scripts/build_plugin.py", "--out", staged], self.home)
            (staged / marker.name).write_text(json.dumps(ownership) + "\n")
            manifest = json.loads((staged / NAME / ".codex-plugin/plugin.json").read_text())
            version = manifest["version"]
            if manifest["name"] != NAME or not re.fullmatch(r"[0-9][A-Za-z0-9.+-]*", version):
                raise InstallError("TokenEconomizer has an unexpected Codex manifest.")
            expected = fingerprint(staged / NAME)
            if fingerprint(self.marketplace) != fingerprint(staged):
                if self.marketplace.exists():
                    backup = Path(directory) / "previous"
                    self.marketplace.rename(backup)
                    try:
                        staged.rename(self.marketplace)
                    except OSError:
                        backup.rename(self.marketplace)
                        raise
                else:
                    staged.rename(self.marketplace)
                self.report["changed"] = True

        if not existing:
            self.cli("marketplace", "add", str(self.marketplace))
            self.report["changed"] = True
        installed = self.cli("list", "--marketplace", NAME)["installed"]
        current = next((entry for entry in installed if entry["name"] == NAME), {})
        cache = self.codex_home / "plugins/cache" / NAME / NAME / version
        if current.get("version") != version or not current.get("enabled") or fingerprint(cache) != expected:
            result = self.cli("add", f"{NAME}@{NAME}")
            if Path(result["installedPath"]) != cache or fingerprint(cache) != expected:
                raise InstallError("Codex did not install the expected TokenEconomizer package contents.")
            self.report.update(changed=True, package_updated=True)

        config = self.home / ".tokeneconomizer/config.json"
        if config.is_symlink():
            raise InstallError("TokenEconomizer's global config must not be a symlink; it was preserved.")
        saved = json.loads(config.read_text()) if config.exists() else {}
        updates = {key: value for key, value in self.settings.items() if saved.get(key) != value}
        if updates:
            run(["/usr/bin/python3", cache / "scripts/te_bootstrap.py", "cli", "--host", "codex",
                 "config", "set", *[f"{key}={value}" for key, value in updates.items()], "--global"], self.home)
            configured = json.loads(config.read_text())
            if any(configured.get(key) != value for key, value in self.settings.items()):
                raise InstallError("TokenEconomizer did not retain the requested global settings.")
            self.report.update(changed=True, settings_updated=True)

        # The upstream writer replaces this file. A default ACL can override its umask.
        with os.fdopen(os.open(config, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                os.fchmod(stream.fileno(), 0o600)
                self.report["changed"] = True
        return self.report

    def run(self):
        state = self.home / ".local/state/tokeneconomizer-install"
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(state / "install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise InstallError("Another TokenEconomizer installation is running.") from None
            return self.install()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-checkout", action="store_true")
    parser.add_argument("--microskills", choices=("off", "envfacts", "on"), default="off")
    parser.add_argument("--toolprune", choices=("off", "propose", "auto"), default="auto")
    parser.add_argument("--loopguard", choices=("off", "nudge"), default="nudge")
    args = parser.parse_args()
    if os.getuid() == 0:
        raise InstallError("Run as the development account, never root.")
    os.umask(0o077)
    home = Path.home()
    if args.check_checkout:
        check_checkout(home / ".local/share/tokeneconomizer-source", home)
        return {"changed": False}
    settings = {"microskills": "envfacts+distiller" if args.microskills == "on" else args.microskills,
                "toolprune": args.toolprune, "loopguard": args.loopguard}
    check_checkout(home / ".local/share/tokeneconomizer-source", home)
    return Installer(home, settings).run()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except InstallError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('{"error":"TokenEconomizer reconciliation failed. No raw diagnostics were emitted."}', file=sys.stderr)
        sys.exit(1)
