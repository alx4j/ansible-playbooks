"""Use real sshd to reject invalid candidates without changing the live file."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location("validator", "/work/roles/dev_host/files/validate_ssh.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
target = Path("/etc/ssh/sshd_config.d/00-hardening.conf")
before = target.read_bytes()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    candidate = root / "candidate.conf"
    candidate.write_text("PasswordAuthentication invalid-value\n")
    try:
        validator.validate(candidate, target, ["no"] * 3)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Invalid SSH candidate was accepted")
    candidate.write_bytes(before)
    conflict = root / "sshd_config"
    conflict.write_text("PasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n")
    try:
        validator.validate(candidate, target, ["no"] * 3, config=conflict)
    except ValueError as error:
        assert "Effective passwordauthentication" in str(error)
    else:
        raise AssertionError("Earlier conflicting SSH policy was accepted")
assert target.read_bytes() == before
print("PASS: invalid syntax and earlier policy conflicts rejected; installed configuration preserved")
