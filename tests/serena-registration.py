"""Check the provisioned MCP entry and preservation of pre-existing Codex settings."""

import json
from pathlib import Path
import subprocess

home = Path.home()
codex = home / ".local/bin/codex"
servers = json.loads(subprocess.check_output([codex, "mcp", "list", "--json"], text=True))
serena, = [server for server in servers if server["name"] == "serena"]
transport = serena["transport"]
assert serena["enabled"] and transport["type"] == "stdio", serena["name"]
assert transport["command"] == str(home / ".local/bin/serena")
assert "--project-from-cwd" in transport["args"]
subprocess.run([transport["command"], *transport["args"], "--help"], check=True, stdout=subprocess.DEVNULL)
assert any(server["name"] == "bootstrap_fixture" and server["transport"]["command"] == "/bin/true"
           for server in servers)
print("Verified project-aware Serena registration and preservation of the existing MCP server")
