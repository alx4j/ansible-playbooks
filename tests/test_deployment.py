"""Exercise the deployment boundary without connecting to a managed host."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STUB = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == "ssh-keygen":
    raise SystemExit(int(os.environ.get("TEST_KEY_EXIT", "0")))
inventory = pathlib.Path(args[args.index("-i") + 1])
key = pathlib.Path(args[args.index("--private-key") + 1])
result = {
    "args": args,
    "inventory_private": inventory.stat().st_mode & 0o777 == 0o600,
    "key_private": key.stat().st_mode & 0o777 == 0o600,
    "directory_private": inventory.parent.stat().st_mode & 0o777 == 0o700,
    "raw_key_removed": "DEPLOY_SSH_PRIVATE_KEY" not in os.environ,
    "has_bootstrap_hash": bool(os.environ.get("DEV_PASSWORD_HASH")),
    "has_become_password": bool(os.environ.get("ANSIBLE_BECOME_PASS")),
    "host_key_checking": os.environ.get("ANSIBLE_HOST_KEY_CHECKING"),
}
with open(os.environ["TEST_CALLS"], "a") as log:
    log.write(json.dumps(result) + "\\n")
if "--list-hosts" in args:
    print(os.environ.get("TEST_HOST_LIST", "    hosts (1):\\n      dev01"))
elif "--syntax-check" not in args:
    raise SystemExit(int(os.environ.get("TEST_APPLY_EXIT", "0")))
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.calls = self.root / "calls.jsonl"
        for name in ["ansible-playbook", "ssh-keygen"]:
            script = self.bin / name
            script.write_text(STUB)
            script.chmod(0o755)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "RUNNER_TEMP": str(self.runtime),
            "TEST_CALLS": str(self.calls),
            "DEPLOY_PLAYBOOK": "site",
            "DEPLOY_MODE": "check",
            "DEPLOY_HOST": "dev01",
            "DEPLOY_INVENTORY": "test inventory placeholder",
            "DEPLOY_KNOWN_HOSTS": "test host key placeholder",
            "DEPLOY_SSH_PRIVATE_KEY": "test private key placeholder",
            "DEV_PASSWORD_HASH": "test hash placeholder",
            "ANSIBLE_BECOME_PASS": "test password placeholder",
        }

    def run_deployment(self, **overrides):
        result = subprocess.run(
            [str(ROOT / "scripts/deploy")],
            env={**self.env, **overrides},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(list(self.runtime.iterdir()), [], "Credential files were not cleaned up")
        for sensitive in ["test private key placeholder", "test hash placeholder", "test password placeholder"]:
            self.assertNotIn(sensitive, result.stdout + result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def test_check_mode_and_credential_isolation(self):
        result, calls = self.run_deployment()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertIn("--check", calls[-1]["args"])
        for call in calls:
            for key in ["inventory_private", "key_private", "directory_private", "raw_key_removed", "has_become_password"]:
                self.assertTrue(call[key])
            self.assertFalse(call["has_bootstrap_hash"])
            self.assertEqual(call["host_key_checking"], "True")
        options = calls[-1]["args"][calls[-1]["args"].index("--ssh-common-args") + 1]
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertNotIn("--diff", calls[-1]["args"])

    def test_apply_mode_and_bootstrap_hash(self):
        result, calls = self.run_deployment(DEPLOY_MODE="apply", DEPLOY_PLAYBOOK="bootstrap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootstrap.yml", calls[-1]["args"])
        self.assertNotIn("--check", calls[-1]["args"])
        self.assertTrue(calls[-1]["has_bootstrap_hash"])

    def test_rejects_patterns_and_shell_injection(self):
        for target in ["all", "dev*", "dev01,dev02", "dev01; touch /tmp/unwanted", "$(id)", "--help"]:
            with self.subTest(target=target):
                result, calls = self.run_deployment(DEPLOY_HOST=target, TEST_HOST_LIST="    hosts (2):\n      dev01\n      dev02")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("--list-hosts" not in c["args"] and "--syntax-check" not in c["args"] for c in calls))
                self.calls.unlink(missing_ok=True)

    def test_rejects_unknown_host(self):
        result, calls = self.run_deployment(TEST_HOST_LIST="    hosts (0):")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_missing_configuration_fails_before_ansible(self):
        for name in ["DEPLOY_INVENTORY", "DEPLOY_KNOWN_HOSTS", "DEPLOY_SSH_PRIVATE_KEY"]:
            with self.subTest(name=name):
                result, calls = self.run_deployment(**{name: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_invalid_key_fails_before_ansible(self):
        result, calls = self.run_deployment(TEST_KEY_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_preserves_ansible_failure_and_cleans_credentials(self):
        result, _ = self.run_deployment(TEST_APPLY_EXIT="2")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
