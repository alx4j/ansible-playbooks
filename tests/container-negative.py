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
    # The included Match must end when sshd returns to its parent file.
    nested = root / "nested"
    nested.write_text("Match User fixture\n    X11Forwarding no\n")
    main = root / "sshd_config"
    main.write_text(f"Include {target}\nInclude {nested}\nUsePAM yes\n")
    validator.validate(candidate, target, ["no"] * 3, config=main)
    validator.check_policy(["no"] * 3, main, "/usr/sbin/sshd")
    main.write_text(f"PasswordAuthentication yes\nInclude {target}\n")
    try:
        validator.check_policy(["no"] * 3, main, "/usr/sbin/sshd")
    except ValueError as error:
        assert "Effective passwordauthentication" in str(error)
    else:
        raise AssertionError("Installed effective policy drift was accepted")
assert target.read_bytes() == before
print("PASS: invalid syntax and earlier policy conflicts rejected; installed configuration preserved")
