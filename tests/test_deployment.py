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
key = pathlib.Path(args[args.index("--private-key") + 1]) if "--private-key" in args else None
password = pathlib.Path(args[args.index("--connection-password-file") + 1]) if "--connection-password-file" in args else None
result = {
    "args": args,
    "inventory_private": inventory.stat().st_mode & 0o777 == 0o600,
    "key_private": key is not None and key.stat().st_mode & 0o777 == 0o600,
    "password_private": password is not None and password.stat().st_mode & 0o777 == 0o600,
    "password_matches": password is not None and password.read_text() == "test bootstrap password placeholder",
    "password_mechanism": os.environ.get("ANSIBLE_SSH_PASSWORD_MECHANISM"),
    "directory_private": inventory.parent.stat().st_mode & 0o777 == 0o700,
    "control_directory_private": pathlib.Path(os.environ["ANSIBLE_SSH_CONTROL_PATH_DIR"]).stat().st_mode & 0o777 == 0o700,
    "control_directory_isolated": pathlib.Path(os.environ["ANSIBLE_SSH_CONTROL_PATH_DIR"]).parent == inventory.parent,
    "raw_key_removed": "DEPLOY_SSH_PRIVATE_KEY" not in os.environ,
    "raw_password_removed": "DEPLOY_BOOTSTRAP_PASSWORD" not in os.environ,
    "raw_bootstrap_sudo_removed": "DEPLOY_BOOTSTRAP_BECOME_PASSWORD" not in os.environ,
    "has_bootstrap_hash": bool(os.environ.get("SUDO_PASSWORD_HASH")),
    "has_become_password": bool(os.environ.get("ANSIBLE_BECOME_PASS")),
    "become_is_initial_login": os.environ.get("ANSIBLE_BECOME_PASS") == "test bootstrap password placeholder",
    "become_is_initial_sudo": os.environ.get("ANSIBLE_BECOME_PASS") == "test initial sudo password placeholder",
    "become_is_dev": os.environ.get("ANSIBLE_BECOME_PASS") == "test password placeholder",
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
            "DEPLOY_HOST": "dev01",
            "DEPLOY_INVENTORY": "test inventory placeholder",
            "DEPLOY_KNOWN_HOSTS": "test host key placeholder",
            "DEPLOY_SSH_PRIVATE_KEY": "test private key placeholder",
            "SUDO_PASSWORD_HASH": "test hash placeholder",
            "ANSIBLE_BECOME_PASS": "test password placeholder",
        }

    def run_deployment(self, **overrides):
        result = subprocess.run(
            [str(ROOT / "scripts/deploy")],
            env={**self.env, **overrides},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(list(self.runtime.iterdir()), [], "Credential files were not cleaned up")
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        for sensitive in ["test private key placeholder", "test hash placeholder", "test password placeholder", "test bootstrap password placeholder", "test initial sudo password placeholder"]:
            self.assertNotIn(sensitive, result.stdout + result.stderr + json.dumps(calls))
        return result, calls

    def test_configuration_and_credential_isolation(self):
        result, calls = self.run_deployment()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("--check", calls[-1]["args"])
        for call in calls:
            for key in ["inventory_private", "key_private", "directory_private", "control_directory_private", "control_directory_isolated", "raw_key_removed", "has_become_password"]:
                self.assertTrue(call[key])
            self.assertFalse(call["has_bootstrap_hash"])
            self.assertEqual(call["host_key_checking"], "True")
        options = calls[-1]["args"][calls[-1]["args"].index("--ssh-common-args") + 1]
        self.assertIn("StrictHostKeyChecking=yes", options)
        self.assertNotIn("--diff", calls[-1]["args"])

    def test_bootstrap_receives_the_account_hash(self):
        result, calls = self.run_deployment(DEPLOY_PLAYBOOK="bootstrap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bootstrap.yml", calls[-1]["args"])
        self.assertNotIn("--check", calls[-1]["args"])
        self.assertTrue(calls[-1]["has_bootstrap_hash"])
        self.assertFalse(calls[-1]["has_become_password"])

    def test_password_bootstrap_without_private_key(self):
        result, calls = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap", DEPLOY_SSH_PRIVATE_KEY="",
            DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--check", calls[-1]["args"])
        for call in calls:
            for key in ["password_private", "password_matches", "directory_private", "raw_password_removed", "raw_key_removed"]:
                self.assertTrue(call[key])
            self.assertEqual(call["password_mechanism"], "ssh_askpass")
            self.assertTrue(call["become_is_initial_login"])
            self.assertFalse(call["become_is_dev"])
            self.assertNotIn("--private-key", call["args"])
        options = calls[-1]["args"][calls[-1]["args"].index("--ssh-common-args") + 1]
        for option in ["StrictHostKeyChecking=yes", "BatchMode=no", "PreferredAuthentications=password", "PasswordAuthentication=yes", "PubkeyAuthentication=no"]:
            self.assertIn(option, options)

    def test_bootstrap_password_takes_precedence_over_key(self):
        result, calls = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap",
            DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--connection-password-file", calls[-1]["args"])
        self.assertNotIn("--private-key", calls[-1]["args"])
        self.assertNotIn("--check", calls[-1]["args"])

    def test_routine_runs_ignore_bootstrap_credentials(self):
        for playbook in ["site", "access"]:
            with self.subTest(playbook=playbook):
                result, calls = self.run_deployment(
                    DEPLOY_PLAYBOOK=playbook,
                    DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder",
                    DEPLOY_BOOTSTRAP_BECOME_PASSWORD="test initial sudo password placeholder",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                for call in calls:
                    self.assertTrue(call["raw_password_removed"])
                    self.assertTrue(call["raw_bootstrap_sudo_removed"])
                    self.assertTrue(call["become_is_dev"])
                    self.assertFalse(call["has_bootstrap_hash"])
                    self.assertNotIn("--connection-password-file", call["args"])
                options = calls[-1]["args"][calls[-1]["args"].index("--ssh-common-args") + 1]
                self.assertIn("PasswordAuthentication=no", options)
                self.assertIn("BatchMode=yes", options)
                self.calls.unlink()
                result, calls = self.run_deployment(
                    DEPLOY_PLAYBOOK=playbook, DEPLOY_SSH_PRIVATE_KEY="",
                    DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_explicit_bootstrap_sudo_password_overrides_login_password(self):
        result, calls = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap",
            DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder",
            DEPLOY_BOOTSTRAP_BECOME_PASSWORD="test initial sudo password placeholder",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for call in calls:
            self.assertTrue(call["become_is_initial_sudo"])
            self.assertTrue(call["raw_bootstrap_sudo_removed"])
            self.assertFalse(call["become_is_dev"])

    def test_key_bootstrap_can_use_initial_sudo_password(self):
        result, calls = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap",
            DEPLOY_BOOTSTRAP_BECOME_PASSWORD="test initial sudo password placeholder",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--private-key", calls[-1]["args"])
        self.assertTrue(calls[-1]["become_is_initial_sudo"])
        self.assertFalse(calls[-1]["become_is_dev"])

    def test_bootstrap_requires_a_password_or_key(self):
        result, calls = self.run_deployment(DEPLOY_PLAYBOOK="bootstrap", DEPLOY_SSH_PRIVATE_KEY="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_password_bootstrap_validates_host_keys_and_cleans_up(self):
        result, calls = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap", DEPLOY_SSH_PRIVATE_KEY="",
            DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder", TEST_KEY_EXIT="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_password_failure_propagates_and_cleans_credentials(self):
        result, _ = self.run_deployment(
            DEPLOY_PLAYBOOK="bootstrap", DEPLOY_SSH_PRIVATE_KEY="",
            DEPLOY_BOOTSTRAP_PASSWORD="test bootstrap password placeholder", TEST_APPLY_EXIT="4",
        )
        self.assertEqual(result.returncode, 4)

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

    def test_rejects_unexpected_playbooks_before_ansible(self):
        for playbook in ["maintenance", "../../other", "site; id"]:
            with self.subTest(playbook=playbook):
                result, calls = self.run_deployment(DEPLOY_PLAYBOOK=playbook)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

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
