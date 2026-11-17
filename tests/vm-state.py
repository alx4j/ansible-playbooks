"""Check real service/firewall state and snapshot reload/restart timestamps."""

import json
import os
import pwd
import subprocess

assert os.geteuid() == 0, "Run the VM state checks with sudo"
account = pwd.getpwnam("ansibletest")


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


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
assert command(*user_systemctl, "is-active", "moshi-hook.service") == "active"
assert command(*user_systemctl, "is-enabled", "moshi-hook.service") == "enabled"
reload_timestamp = command(*user_systemctl, "show", "--property=GeneratorsStartTimestampMonotonic", "--value")
restart_timestamp = command(*user_systemctl, "show", "moshi-hook.service", "--property=ActiveEnterTimestampMonotonic", "--value")
assert reload_timestamp.isdigit() and int(reload_timestamp) > 0, reload_timestamp
assert restart_timestamp.isdigit() and int(restart_timestamp) > 0, restart_timestamp
print(json.dumps({"user_manager_reload": reload_timestamp, "moshi_start": restart_timestamp}, sort_keys=True))
