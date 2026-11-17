"""Inspect repository activity during a single synchronization run on Linux."""

import argparse
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys


WORKING = "a process is working in this repository"
FILES_OPEN = "a process has files open in this repository"
UNKNOWN = "cannot inspect a development-account process safely"


def scan(path, uid):
    inaccessible = False
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            if Path(os.readlink(process / "cwd")).is_relative_to(path):
                return WORKING
            for descriptor in (process / "fd").iterdir():
                try:
                    if Path(os.readlink(descriptor)).is_relative_to(path):
                        return FILES_OPEN
                except FileNotFoundError:
                    pass
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            inaccessible = True
    if inaccessible:
        return UNKNOWN
    return None


def query(channel, path):
    try:
        channel.sendall(os.fsencode(path))
        reply = channel.recv(128)
    except OSError:
        return UNKNOWN
    if reply == b"idle":
        return None
    for reason in (WORKING, FILES_OPEN):
        if reply == reason.encode():
            return reason
    return UNKNOWN


def supervise(account, projects, command):
    # Only this reader needs root's view of procfs. The worker obtains its own
    # credentials after dropping privileges and receives only a private socket.
    if os.getuid() != 0 or account.pw_uid == 0 or projects.parent != Path(account.pw_dir):
        raise ValueError("Expected a development account and a projects directory inside its home")
    reader, worker = socket.socketpair(type=socket.SOCK_SEQPACKET)
    with reader, worker, subprocess.Popen(
        [*command, "--projects", str(projects), "--activity-fd", str(worker.fileno())],
        user=account.pw_uid, group=account.pw_gid,
        extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
        cwd=account.pw_dir, env={"HOME": account.pw_dir, "PATH": os.defpath},
        stdin=subprocess.DEVNULL, pass_fds=(worker.fileno(),),
    ) as process:
        worker.close()
        while request := reader.recv(4097):
            path = Path(os.fsdecode(request))
            reason = UNKNOWN
            if (len(request) <= 4096 and ".." not in path.parts
                    and path != projects and path.is_relative_to(projects)):
                try:
                    reason = scan(path, account.pw_uid)
                except OSError:
                    pass
            try:
                reader.sendall((reason or "idle").encode())
            except BrokenPipeError:
                break
        return process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user")
    parser.add_argument("projects", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("a worker command is required")
    sys.exit(supervise(pwd.getpwnam(args.user), args.projects, args.command))
