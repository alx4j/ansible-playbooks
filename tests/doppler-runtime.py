"""Verify encrypted storage and runtime isolation with VM fixture tokens only."""

import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

assert os.geteuid() == 0, "Run these VM checks with sudo"
account = pwd.getpwnam("ansibletest")
service_account = pwd.getpwnam("doppler-secrets")
assert service_account.pw_uid != 0 and service_account.pw_shell == "/usr/sbin/nologin"
assert sys.argv[1:] in ([], ["--rotated"])
expected = b"ansible-fixture-two" if sys.argv[1:] else b"ansible-fixture-one"


def command(*arguments):
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert result.returncode == 0, "Fixture command failed; inspect service status without printing credentials"
    return result.stdout


credential = Path("/etc/credstore.encrypted/doppler-secrets/doppler_token")
host_key = Path("/var/lib/systemd/credential.secret")
for path in (credential, host_key):
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_mode & 0o077 == 0
    for user in ("ansibletest", "doppler-secrets"):
        result = subprocess.run(["runuser", "-u", user, "--", "test", "-r", str(path)], check=False)
        assert result.returncode == 1, "Encrypted storage or host key is readable by an unprivileged account"
assert expected not in credential.read_bytes(), "Token was stored in plaintext"
assert command("systemd-creds", "decrypt", "--name=doppler_token", str(credential), "-") == expected

pid = int(command("systemctl", "show", "doppler-secrets.service", "--property=MainPID", "--value"))
assert pid > 0 and Path(f"/proc/{pid}").stat().st_uid == service_account.pw_uid
assert expected not in Path(f"/proc/{pid}/cmdline").read_bytes()
assert expected not in Path(f"/proc/{pid}/environ").read_bytes()
runtime = Path(f"/proc/{pid}/root/run/credentials/doppler-secrets.service/doppler_token")
assert runtime.read_bytes() == expected, "The running service did not load the current credential"

socket = Path("/run/doppler-secrets.sock")
assert socket.is_socket() and socket.stat().st_uid == 0
assert socket.stat().st_gid == account.pw_gid and stat.S_IMODE(socket.stat().st_mode) == 0o660
wrapper = Path(account.pw_dir) / ".codex/skills/doppler-secrets/scripts/doppler.py"
command("runuser", "-u", "ansibletest", "--", "/usr/bin/python3", str(wrapper), "doctor", "--offline")
# The initial fixture administrator has neither the socket group nor the allowed peer UID.
result = subprocess.run(["runuser", "-u", "ansibleinitial", "--", "/usr/bin/python3", "-c",
                         "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/doppler-secrets.sock')"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
assert result.returncode != 0, "An unrelated account accessed the secret socket"
print("Doppler encrypted storage, service identity, runtime credential, and socket isolation passed")
