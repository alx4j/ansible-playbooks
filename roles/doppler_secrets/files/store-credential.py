#!/usr/bin/env python3
"""Compare in memory and atomically store a host-encrypted Doppler token."""

import hmac
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys
import tempfile

DIRECTORY = Path("/etc/credstore.encrypted/doppler-secrets")
DESTINATION = DIRECTORY / "doppler_token"


def run_creds(arguments, data=None):
    result = subprocess.run(["/usr/bin/systemd-creds", *arguments], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("Credential operation failed")
    return result.stdout


def service_uses(token):
    service = "doppler-secrets.service"
    result = subprocess.run(["/usr/bin/systemctl", "show", service, "--property=MainPID", "--value"],
                            capture_output=True, text=True, check=True, timeout=30)
    pid = int(result.stdout)
    if not pid:
        return False
    runtime = Path(f"/proc/{pid}/root/run/credentials/{service}/doppler_token")
    try:
        with runtime.open("rb") as stream:
            return hmac.compare_digest(stream.read(8193), token)
    except FileNotFoundError:
        # The service may have exited since systemd reported its PID.
        return False


def store(token):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.geteuid() != 0 or not token or len(token) > 8192 or any(chr(byte).isspace() for byte in token) or b"\0" in token:
        raise ValueError("Invalid credential input or identity")
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("Unsafe credential directory")
    if DESTINATION.exists() or DESTINATION.is_symlink():
        info = DESTINATION.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("Unsafe encrypted credential")
        current = run_creds(["decrypt", "--name=doppler_token", str(DESTINATION), "-"])
        if hmac.compare_digest(current, token):
            return "unchanged" if service_uses(token) else "restart"
    with tempfile.TemporaryDirectory(prefix=".doppler-", dir=DIRECTORY) as staging:
        candidate = Path(staging) / "doppler_token"
        run_creds(["encrypt", "--with-key=host", "--name=doppler_token", "-", str(candidate)], token)
        candidate.chmod(0o600)
        with candidate.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(candidate, DESTINATION)
        descriptor = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return "changed"


if __name__ == "__main__":
    try:
        print(store(sys.stdin.buffer.read(8193)))
    except Exception:
        sys.exit("Could not reconcile the credential; check systemd, root permissions, and the existing host key.")
