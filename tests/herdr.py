"""Verify Herdr sessions, Moshi discovery, and context in a disposable account."""

import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time


account = pwd.getpwuid(os.getuid())
assert account.pw_name == "ansibletest", "Run only as the disposable test account"
home = Path(account.pw_dir)
session = "ansible-moshi-test"
herdr = str(home / ".local/bin/herdr")
moshi = str(home / ".local/bin/moshi-hook")
snapshot = home / ".herdr-test.json"
context_file = home / ".herdr-context-test.json"
mode = sys.argv[1:]
assert mode in ([], ["--prepare"], ["--cleanup"]), "Unexpected test arguments"


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20)


def sessions():
    return json.loads(command(herdr, "session", "list", "--json"))["sessions"]


if mode == ["--cleanup"]:
    command(herdr, "session", "stop", session)
    assert not any(item["name"] == session and item["running"] for item in sessions())
    sys.exit(0)

if mode == ["--prepare"]:
    assert not any(item["name"] == session for item in sessions()), "Fixture session already exists"
    context_file.unlink(missing_ok=True)
    launch = [herdr, "--session", session, "server"]
    trace = home / ".herdr-startup-trace.log"
    if shutil.which("strace"):
        launch = ["strace", "-f", "-e", "trace=file", "-o", str(trace), *launch]
    with (home / ".herdr-test.log").open("w") as log:
        process = subprocess.Popen(launch, cwd=home,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   env=dict(os.environ, HOME=str(home), SHELL=account.pw_shell,
                                            PATH=str(home / ".local/bin") + ":/usr/local/bin:/usr/bin:/bin"),
                                   start_new_session=True)
    for _ in range(40):
        if process.poll() is not None:
            if trace.exists():
                print("\n".join(line for line in trace.read_text().splitlines() if "EACCES" in line or "EPERM" in line))
            raise AssertionError(f"Herdr exited ({process.returncode}):\n{(home / '.herdr-test.log').read_text()}")
        if any(item["name"] == session and item["running"] for item in sessions()):
            break
        time.sleep(0.25)
    else:
        raise AssertionError("Herdr did not start; inspect ~/.herdr-test.log")
    project = home / (os.environ.get("PROJECTS_DIR") or "projects")
    created = json.loads(command(herdr, "--session", session, "workspace", "create",
                                 "--cwd", str(project), "--label", "moshi-fixture", "--focus"))["result"]
    pane = created["root_pane"]["pane_id"]
    # Ask the actual pane shell to report its context, including Herdr's own environment.
    command(herdr, "--session", session, "pane", "run", pane,
            f"moshi-hook context > {shlex.quote(str(context_file))}")
    for _ in range(40):
        if context_file.exists() and context_file.stat().st_size:
            break
        time.sleep(0.25)
    else:
        raise AssertionError("The pane did not report Moshi context")

running = next(item for item in sessions() if item["name"] == session and item["running"])
with socket.socket(socket.AF_UNIX) as connection:
    connection.connect(running["socket_path"])
    pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
assert uid == account.pw_uid
workspaces = json.loads(command(herdr, "--session", session, "workspace", "list"))["result"]["workspaces"]
state = {"pid": pid, "workspaces": [
    {key: workspace[key] for key in ("workspace_id", "label", "active_tab_id", "tab_count", "pane_count")}
    for workspace in workspaces
]}
if mode == ["--prepare"]:
    snapshot.write_text(json.dumps(state))
else:
    assert json.loads(snapshot.read_text()) == state, "Herdr sessions changed during an unchanged Ansible run"

discovery = json.loads(command(moshi, "mux", "ls", "--json"))["herdr"]
assert discovery["installed"]
assert any(item["name"] == session and item["running"] for item in discovery["sessions"]), discovery
assert any(item["label"] == "moshi-fixture" and item["sessionName"] == session
           for item in discovery["workspaces"]), discovery
context = json.loads(context_file.read_text())
assert context["kind"] == "herdr", context
assert context["herdr"]["rawSession"] == session, context
assert context["herdr"]["workspaceId"] == workspaces[0]["workspace_id"], context
assert context["herdr"]["tabId"] == workspaces[0]["active_tab_id"], context
print("Herdr server, workspace discovery, pane context, and session preservation passed")
