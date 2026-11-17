"""Prepare fixture-only SSH trust and inventory for the disposable hosted VM."""

import json
from pathlib import Path
import sys

directory = Path(sys.argv[1])
public_key = (directory / "id.pub").read_text().strip()
host_key = " ".join(Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().split()[:2])
(directory / "known_hosts").write_text(f"[127.0.0.1]:2222 {host_key}\n")
host = {
    "ansible_host": "127.0.0.1", "ansible_port": 2222,
    "ansible_ssh_private_key_file": str(directory / "id"),
    "ansible_ssh_common_args": f"-o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={directory / 'known_hosts'}",
    "sudo_user": "ansibletest", "dev_host_bootstrap_user": "ansibleinitial",
    "dev_host_authorized_keys": [public_key], "dev_host_passwordless_sudo": True,
    "dev_host_ssh_port": 2222, "dev_host_enable_firewall": True,
    "dev_host_codex_config_source": str(Path(__file__).resolve().parent / "fixtures/codex-config.toml"),
    # External GitHub access is exercised with fixtures in work-repositories-ssh.py.
    "work_repositories_enabled": False,
    # Toolkit Git updates and real Codex installs run separately with local fixtures.
    "agent_toolkit_enabled": False,
}
(directory / "inventory.json").write_text(json.dumps({"all": {"children": {"development": {"hosts": {"vm": host}}}}}))
