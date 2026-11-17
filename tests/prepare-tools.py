"""Seed an interrupted Go install and existing Bash login customization."""

import os
from pathlib import Path
import pwd

import yaml

account = pwd.getpwuid(os.getuid())
assert account.pw_name == "ansibletest", "Run only as the disposable test account"
login_profile = Path(account.pw_dir) / ".bash_profile"
with login_profile.open("a") as profile:
    profile.write("\nexport ANSIBLE_TEST_LOGIN_MARKER=preserved\n")
defaults = yaml.safe_load((Path(__file__).resolve().parents[1] / "roles/dev_host/defaults/main.yml").read_text())
version = defaults["dev_host_go_version"]
architecture = defaults["dev_host_go_architectures"][os.uname().machine]
root = Path(account.pw_dir) / ".local/share/go"
binary = root / version / "go/bin/go"
assert not binary.exists(), "The recovery fixture requires a fresh account"
binary.parent.mkdir(parents=True)
binary.write_text(f'''#!/bin/sh
case "$1" in
  version) printf 'go version go{version} linux/{architecture}\\n' ;;
  *) echo 'Incomplete SDK fixture cannot build' >&2; exit 1 ;;
esac
''')
binary.chmod(0o755)
staging = root / (version + ".staging")
staging.mkdir()
(staging / "interrupted").write_text("An interrupted extraction must be retried\n")
print("Seeded incomplete Go SDK and existing Bash login customization")
