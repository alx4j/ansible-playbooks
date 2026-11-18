"""Verify the real bundled plugin, Codex discovery, settings, and hook payloads offline."""

import json
import os
from pathlib import Path
import pwd
import queue
import subprocess
import tempfile
import threading


HOME = Path.home()
assert pwd.getpwuid(os.getuid()).pw_name == "ansibletest", "Use the disposable fixture account"
CODEX = HOME / ".local/bin/codex"
HELPER = Path("/usr/local/lib/tokeneconomizer/install.py")
SOURCE = HOME / ".local/share/tokeneconomizer-source"
MARKET = HOME / ".local/share/tokeneconomizer-marketplace"
STATE = HOME / ".tokeneconomizer"
CONFIG = STATE / "config.json"
ENV = {"HOME": str(HOME), "CODEX_HOME": str(HOME / ".codex"),
       "PATH": f"{HOME}/.local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8",
       "PYTHONDONTWRITEBYTECODE": "1"}


def command(*args, failure=False, cwd=HOME, payload=None):
    result = subprocess.run(list(map(str, args)), env=ENV, cwd=cwd, input=payload,
                            text=True, capture_output=True, timeout=60)
    assert (result.returncode != 0) == failure, result.stdout + result.stderr
    return result.stderr if failure else result.stdout


def reconcile(*args):
    return json.loads(command("python3", HELPER, *args))


def rpc_inspection(cwd):
    """Ask the real Codex app server to discover hooks/skills, without a model turn."""
    with tempfile.TemporaryFile(mode="w+") as errors:
        process = subprocess.Popen([str(CODEX), "--no-daemon", "app-server", "--stdio"],
                                   env=ENV, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True)
        messages = queue.Queue()
        def receive():
            for line in process.stdout:
                messages.put(json.loads(line))
            messages.put(None)
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        def request(identifier, method, params):
            process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            while True:
                message = messages.get(timeout=30)
                assert message is not None, "Codex stopped before returning the discovery response"
                if message.get("id") == identifier:
                    assert "error" not in message, message
                    return message["result"]
        try:
            request(1, "initialize", {"clientInfo": {"name": "ansible_fixture", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
            process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
            process.stdin.flush()
            hooks = request(2, "hooks/list", {"cwds": [str(cwd)]})
            skills = request(3, "skills/list", {"cwds": [str(cwd)], "forceReload": True})
            return hooks, skills
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            reader.join(timeout=5)
            process.stdout.close()


manifest = json.loads((MARKET / "tokeneconomizer/.codex-plugin/plugin.json").read_text())
CACHE = HOME / ".codex/plugins/cache/tokeneconomizer/tokeneconomizer" / manifest["version"]
assert (CACHE / "lib/tokeneconomizer/plugin/hook.py").is_file()
assert not (CACHE / "lib/tokeneconomizer").is_symlink()
assert json.loads(CONFIG.read_text()) == {"microskills": "off", "toolprune": "auto", "loopguard": "nudge"}
assert CONFIG.stat().st_mode & 0o777 == 0o600
assert STATE.stat().st_mode & 0o777 == 0o700
before_config = (HOME / ".codex/config.toml").read_bytes()
before_hooks = (HOME / ".codex/hooks.json").read_bytes() if (HOME / ".codex/hooks.json").exists() else None
installed_time = (CACHE / ".codex-plugin/plugin.json").stat().st_mtime_ns
assert not reconcile()["changed"]
assert (CACHE / ".codex-plugin/plugin.json").stat().st_mtime_ns == installed_time

# A mode-only repair preserves the config bytes and leaves subsequent runs unchanged.
original_config = CONFIG.read_bytes()
CONFIG.chmod(0o640)
repair = reconcile()
assert repair["changed"] and not repair["settings_updated"] and not repair["package_updated"], repair
assert CONFIG.read_bytes() == original_config and CONFIG.stat().st_mode & 0o777 == 0o600
assert not reconcile()["changed"]

# A default ACL would grant group read access when upstream replaces the file.
command("setfacl", "-d", "-m", "g::r-x", STATE)
# Per-project settings and learned data survive global configuration changes.
repository_state = STATE / "repos/ansible-preservation-fixture"
repository_state.mkdir(parents=True)
(repository_state / "config.json").write_text('{"microskills":"off"}\n')
(repository_state / "learned.json").write_text('{"fixture":"preserve learned state"}\n')
saved = json.loads(CONFIG.read_text())
saved["enabled"] = False
saved["codex_paid_model"] = "fixture-model"
CONFIG.write_text(json.dumps(saved))
result = reconcile("--microskills", "on", "--toolprune", "propose", "--loopguard", "off")
assert result["settings_updated"] and not result["package_updated"]
assert CONFIG.stat().st_mode & 0o777 == 0o600
configured = json.loads(CONFIG.read_text())
assert configured == {**saved, "microskills": "envfacts+distiller", "toolprune": "propose", "loopguard": "off"}
assert not reconcile("--microskills", "on", "--toolprune", "propose", "--loopguard", "off")["changed"]
assert json.loads((repository_state / "config.json").read_text()) == {"microskills": "off"}
assert json.loads((repository_state / "learned.json").read_text())["fixture"] == "preserve learned state"
configured.pop("enabled")
configured.pop("codex_paid_model")
CONFIG.write_text(json.dumps(configured))
assert reconcile()["settings_updated"]

# Repair contents and executable permissions even when the plugin version is unchanged.
core = CACHE / "lib/tokeneconomizer/plugin/hook.py"
original = core.read_bytes()
core.write_bytes(b"damaged fixture\n")
assert reconcile()["package_updated"]
assert core.read_bytes() == original
launcher = CACHE / "scripts/te-hook"
launcher.chmod(0o600)
assert reconcile()["package_updated"]
assert os.access(launcher, os.X_OK)
assert not reconcile()["changed"]
assert (HOME / ".codex/config.toml").read_bytes() == before_config
assert before_hooks == ((HOME / ".codex/hooks.json").read_bytes() if (HOME / ".codex/hooks.json").exists() else None)

with tempfile.TemporaryDirectory(prefix="tokeneconomizer-project-", dir=HOME) as directory:
    project = Path(directory)
    command("git", "init", "-q", project)
    hooks, skills = rpc_inspection(project)
    assert not any(entry["errors"] for entry in hooks["data"]), hooks
    owned = [hook for entry in hooks["data"] for hook in entry["hooks"]
             if hook.get("pluginId") == "tokeneconomizer@tokeneconomizer"]
    assert {hook["eventName"] for hook in owned} == {"sessionStart", "userPromptSubmit", "postToolUse", "stop", "sessionEnd"}, owned
    assert all(hook["enabled"] and hook["trustStatus"] == "untrusted" for hook in owned), owned
    assert not any(entry["errors"] for entry in skills["data"]), skills
    discovered = [skill for entry in skills["data"] for skill in entry["skills"]
                  if str(CACHE) in skill["path"]]
    assert len(discovered) >= 5, discovered

    # Execute only synthetic hook events: no model request, login, or paid judge call.
    for event in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"):
        payload = {"hook_event_name": event, "session_id": "ansible-tokeneconomizer-fixture",
                   "cwd": str(project), "prompt": "Synthetic fixture task",
                   "tool_name": "exec_command", "tool_input": {"cmd": "true"},
                   "tool_response": {"exit_code": 0, "output": "fixture"}}
        response = command("python3", CACHE / "scripts/te_bootstrap.py", "hook", "--host", "codex",
                           cwd=project, payload=json.dumps(payload))
        assert isinstance(json.loads(response), dict), response
    error_log = STATE / "errors.log"
    assert not error_log.exists() or not error_log.read_text(), "TokenEconomizer logged a fixture hook error"
    assert (STATE / "bin/te").is_file(), "SessionStart did not create the supported CLI launcher"
    command(STATE / "bin/te", "--host", "codex", "status", cwd=project)

local = SOURCE / "uncommitted-fixture.txt"
local.write_text("Preserve local changes\n")
try:
    assert "local changes" in command("python3", HELPER, "--check-checkout", failure=True)
    assert local.read_text() == "Preserve local changes\n"
finally:
    local.unlink()
assert not reconcile()["changed"]
print("TokenEconomizer: real Codex package/hook/skill discovery, untrusted hooks, offline hook events, "
      "settings and learned-state preservation, package repair, and unchanged reruns passed")
