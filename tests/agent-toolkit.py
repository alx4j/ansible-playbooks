"""Exercise production toolkit tasks with real Git and Codex in a disposable host."""

import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

import yaml

from test_agent_toolkit import fixture


ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
assert pwd.getpwuid(os.getuid()).pw_name == "ansibletest", "Use the disposable fixture account"
assert (HOME / ".local/bin/codex").is_file(), "Install the pinned Codex first"


def command(*args):
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def commit(path, message):
    command("git", "-C", path, "add", ".")
    command("git", "-C", path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", message)


with tempfile.TemporaryDirectory(prefix="agent-toolkit-") as directory:
    temporary = Path(directory)
    upstream = temporary / "upstream"
    fixture(upstream, ("common", "game-development", "infrastructure", "productivity"))
    shutil.copytree(ROOT / "roles/doppler_secrets/files/skill", upstream / "plugins/common/skills/doppler-secrets")
    command("git", "init", "-q", "-b", "main", upstream)
    commit(upstream, "initial toolkit")
    projects = HOME / "toolkit-development-fixture"
    projects.mkdir()
    working = projects / "alx4j-agent-toolkit"
    command("git", "clone", "-q", upstream, working)
    command("git", "-C", working, "switch", "-qc", "unfinished-work")
    (working / "uncommitted.txt").write_text("Preserve my unfinished work\n")
    working_head = command("git", "-C", working, "rev-parse", "HEAD")
    codex_home = HOME / ".codex"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "AGENTS.md").write_text("Existing fixture instructions\n")
    legacy = codex_home / "skills/doppler-secrets"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "customization.txt").write_text("Keep this customization\n")
    play = [{"name": "Exercise the managed toolkit with fixture Git and real Codex",
             "hosts": "localhost", "connection": "local", "gather_facts": False,
             "vars": {"dev_host_user": "ansibletest", "dev_host_home": str(HOME),
                      "dev_host_codex_instructions_source": "", "agent_toolkit_repository": upstream.as_uri(),
                      "agent_toolkit_access": {"stdout": json.dumps({"ssh_options": []})}},
             "environment": {"HOME": str(HOME), "CODEX_HOME": str(codex_home)},
             "tasks": [
                 {"name": "Install the production toolkit helper", "become": True,
                  "ansible.builtin.import_role": {"name": "agent_toolkit", "tasks_from": "setup"}},
                 {"name": "Run production checkout and package reconciliation",
                  "ansible.builtin.import_role": {"name": "agent_toolkit", "tasks_from": "configure"}},
             ]}]
    playbook = temporary / "playbook.yml"
    playbook.write_text(yaml.safe_dump(play, sort_keys=False))
    environment = {**os.environ, "ANSIBLE_ROLES_PATH": str(ROOT / "roles")}
    def apply(unchanged=False, failure=False):
        result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)],
                                env=environment, text=True, capture_output=True, timeout=180)
        if failure:
            assert result.returncode != 0 and "local changes" in result.stdout, result.stdout + result.stderr
            return
        if result.returncode and "No raw diagnostics were emitted" in result.stdout:
            # Only synthetic fixture data is present; expose the Python failure for CI diagnosis.
            diagnostic = subprocess.run([
                "python3", "-c", "import runpy; runpy.run_path('/usr/local/lib/agent-toolkit/install.py')['main']()",
                "--checkout", str(HOME / ".local/share/alx4j-agent-toolkit"),
            ], env={"HOME": str(HOME), "PATH": os.defpath}, text=True, capture_output=True, timeout=60)
            raise AssertionError(result.stdout + diagnostic.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
        if unchanged:
            assert re.search(r"localhost\s+:.*changed=0\s+unreachable=0\s+failed=0", result.stdout), result.stdout
        assert command("git", "-C", working, "rev-parse", "HEAD") == working_head
        assert command("git", "-C", working, "branch", "--show-current") == "unfinished-work"
        assert (working / "uncommitted.txt").read_text() == "Preserve my unfinished work\n"
    apply()
    managed = HOME / ".local/share/alx4j-agent-toolkit"
    cache = codex_home / "plugins/cache/alx4j-agent-toolkit"
    instructions = codex_home / "AGENTS.md"
    assert instructions.is_symlink() and instructions.resolve() == managed / "config/codex/AGENTS.md"
    assert not legacy.exists()
    backups = HOME / ".local/share/agent-toolkit-backups"
    assert any(path.read_text() == "Existing fixture instructions\n" for path in backups.glob("*/AGENTS.md"))
    assert any(path.read_text() == "Keep this customization\n" for path in backups.glob("*/doppler-secrets/customization.txt"))
    before = {name: (cache / name / "0.1.0/plugin.json").stat().st_mtime_ns
              for name in ("common", "game-development", "infrastructure", "productivity")}
    apply(unchanged=True)
    assert all((cache / name / "0.1.0/plugin.json").stat().st_mtime_ns == modified for name, modified in before.items())
    for name in ("plugin.json", ".codex-plugin/plugin.json"):
        path = upstream / "plugins/common" / name
        manifest = json.loads(path.read_text())
        manifest["version"] = "0.2.0"
        path.write_text(json.dumps(manifest))
    (upstream / "plugins/common/skills/common/SKILL.md").write_text("---\nname: common\ndescription: Updated fixture\n---\nUpdated\n")
    (upstream / "config/codex/AGENTS.md").write_text("Updated fixture instructions\n")
    commit(upstream, "update toolkit")
    apply()
    assert instructions.read_text() == "Updated fixture instructions\n"
    assert "Updated" in (cache / "common/0.2.0/skills/common/SKILL.md").read_text()
    assert all((cache / name / "0.1.0/plugin.json").stat().st_mtime_ns == modified
               for name, modified in before.items() if name != "common")
    apply(unchanged=True)
    if Path("/run/doppler-secrets.sock").exists():
        command("python3", cache / "common/0.2.0/skills/doppler-secrets/scripts/doppler.py", "doctor", "--offline")
    (managed / "local-change").write_text("Do not discard\n")
    apply(failure=True)
    assert (managed / "local-change").read_text() == "Do not discard\n"
    print("Real Codex packages, Git updates, instruction/skill backups, unchanged reruns, and working-copy preservation passed")
