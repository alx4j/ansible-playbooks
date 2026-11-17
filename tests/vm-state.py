"""Check real service/firewall state and snapshot reload/restart timestamps."""

import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

assert os.geteuid() == 0, "Run the VM state checks with sudo"
account = pwd.getpwnam("ansibletest")


def command(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert result.returncode == 0, f"{args} exited {result.returncode}:\n{result.stdout}"
    return result.stdout.strip()


assert command("loginctl", "show-user", "ansibletest", "--property=Linger", "--value") == "yes"
for timer in ("apt-daily.timer", "apt-daily-upgrade.timer"):
    assert command("systemctl", "is-active", timer) == "active"
    assert command("systemctl", "is-enabled", timer) == "enabled"
firewall = command("ufw", "status")
assert "Status: active" in firewall and "2222/tcp" in firewall and "60000:61000/udp" in firewall, firewall
policy = command("/usr/sbin/sshd", "-T")
for option in ("permitrootlogin no", "passwordauthentication no", "kbdinteractiveauthentication no"):
    assert option in policy.splitlines(), option
user_systemctl = ("runuser", "-u", "ansibletest", "--", "env", f"XDG_RUNTIME_DIR=/run/user/{account.pw_uid}", "systemctl", "--user")
try:
    assert command(*user_systemctl, "is-active", "moshi-hook.service") == "active"
except AssertionError:
    # This unpaired fixture account has no deployment or application credentials.
    subprocess.run((*user_systemctl, "status", "moshi-hook.service", "--full", "--no-pager"),
                   stdout=sys.stderr, stderr=sys.stderr, check=False)
    subprocess.run(("journalctl", f"_UID={account.pw_uid}", "--lines=60", "--no-pager"),
                   stdout=sys.stderr, stderr=sys.stderr, check=False)
    raise
assert command(*user_systemctl, "is-enabled", "moshi-hook.service") == "enabled"
socket = Path(f"/run/user/{account.pw_uid}/moshi-hook.sock")
assert socket.is_socket() and socket.stat().st_uid == account.pw_uid, socket
reload_timestamp = command(*user_systemctl, "show", "--property=GeneratorsStartTimestampMonotonic", "--value")
restart_timestamp = command(*user_systemctl, "show", "moshi-hook.service", "--property=ActiveEnterTimestampMonotonic", "--value")
assert reload_timestamp.isdigit() and int(reload_timestamp) > 0, reload_timestamp
assert restart_timestamp.isdigit() and int(restart_timestamp) > 0, restart_timestamp
for unit in ("doppler-secrets.service", "doppler-secrets.socket"):
    assert command("systemctl", "is-active", unit) == "active"
    assert command("systemctl", "is-enabled", unit) == "enabled"
doppler_timestamp = command("systemctl", "show", "doppler-secrets.service", "--property=ActiveEnterTimestampMonotonic", "--value")
assert doppler_timestamp.isdigit() and int(doppler_timestamp) > 0
print(json.dumps({"user_manager_reload": reload_timestamp, "moshi_start": restart_timestamp,
                  "doppler_start": doppler_timestamp}, sort_keys=True))
