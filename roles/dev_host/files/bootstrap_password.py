#!/usr/bin/python3 -I
"""Retire a local account's bootstrap password after the replacement login works."""

import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys


PENDING = Path("/var/lib/dev-vps-bootstrap-password-locks")


def account_name(name):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", name):
        raise ValueError("Invalid account name for bootstrap password handoff.")
    return name


def verify_state_directory():
    metadata = PENDING.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ValueError("The bootstrap password state must be a private directory owned by the administrator.")


def record(initial_user, development_user):
    if initial_user == development_user:
        raise ValueError("Password bootstrap needs an initial account distinct from the development account.")
    uid = pwd.getpwnam(account_name(initial_user)).pw_uid
    account_name(development_user)
    PENDING.mkdir(mode=0o700, exist_ok=True)
    verify_state_directory()
    # An empty file records the account identity atomically. It contains no credential.
    marker = PENDING / f"{development_user}.{uid}-{initial_user}"
    try:
        marker.touch(mode=0o600, exist_ok=False)
    except FileExistsError:
        return False
    return True


def finish(development_user):
    account_name(development_user)
    try:
        verify_state_directory()
    except FileNotFoundError:
        return False
    development_uid = pwd.getpwnam(development_user).pw_uid
    if development_uid == 0:
        raise ValueError("The replacement account must be a non-root development user.")
    changed = False
    for marker in sorted(PENDING.glob(f"{development_user}.*")):
        identity = marker.name.split(".", 1)[1]
        expected_uid, _, initial_user = identity.partition("-")
        uid = pwd.getpwnam(account_name(initial_user)).pw_uid
        if str(uid) != expected_uid or uid == development_uid:
            raise ValueError("Pending bootstrap account identity changed or matches the development account. Inspect the handoff.")
        # passwd --lock also succeeds if a previous run stopped between the lock
        # and marker removal. A failed lock leaves the marker for the next run.
        subprocess.run(["/usr/bin/passwd", "--lock", "--", initial_user], check=True,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        marker.unlink(missing_ok=True)
        changed = True
    return changed


def main(args):
    if len(args) == 3 and args[0] == "record":
        return record(args[1], args[2])
    if len(args) == 2 and args[0] == "finish":
        return finish(args[1])
    raise ValueError("Expected record INITIAL_USER DEVELOPMENT_USER or finish DEVELOPMENT_USER.")


if __name__ == "__main__":
    try:
        print("changed" if main(sys.argv[1:]) else "ok")
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"Bootstrap password handoff failed: {error}")
