"""Verify workflow inputs and credential routing before invoking Ansible."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("github_deploy", ROOT / "scripts/github-deploy.py")
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class GitHubDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.event = {"inputs": {
            "server_address": "192.0.2.10",
            "bootstrap_user": "ubuntu", "bootstrap_password": "initial password placeholder",
        }}
        self.env = {
            "DEV_PASSWORD": "development password placeholder",
            "DEPLOY_SSH_PRIVATE_KEY": "private key placeholder",
            "DEPLOY_KNOWN_HOSTS": "verified host key placeholder",
            "DOPPLER_TOKEN": "unused token placeholder",
            "GITHUB_EVENT_PATH": "/event/path/placeholder",
        }
        self.key = patch.object(DEPLOY, "public_key", return_value="ssh-ed25519 public-key-placeholder").start()
        self.hash = patch.object(DEPLOY, "password_hash", return_value="$6$hash-placeholder").start()
        self.addCleanup(patch.stopall)

    def test_builds_single_host_inventory_and_separates_credentials(self):
        env = DEPLOY.prepare_environment(self.event, self.env)
        host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]
        self.assertEqual(list(host), ["dev01"])
        self.assertEqual(host["dev01"], {
            "ansible_host": "192.0.2.10", "ansible_port": 22, "ansible_user": "dev",
            "dev_host_bootstrap_user": "ubuntu", "dev_host_ssh_port": 22,
            "dev_host_authorized_keys": ["ssh-ed25519 public-key-placeholder"],
        })
        self.assertEqual(env["DEPLOY_HOST"], "dev01")
        self.assertEqual(env["DEPLOY_MODE"], "apply")
        self.assertEqual(env["ANSIBLE_BECOME_PASS"], self.env["DEV_PASSWORD"])
        for name in ("DEV_PASSWORD", "DOPPLER_TOKEN", "GITHUB_EVENT_PATH", "DEV_PASSWORD_HASH", "DEPLOY_BOOTSTRAP_PASSWORD"):
            self.assertNotIn(name, env)
        for value in self.env.values():
            self.assertNotIn(value, env["DEPLOY_INVENTORY"])
        self.hash.assert_not_called()
        self.key.assert_called_once_with(self.env["DEPLOY_SSH_PRIVATE_KEY"])

    def test_initial_probe_environment_drops_stale_credentials_and_playbook(self):
        self.env.update(DEV_PASSWORD_HASH="stale hash", DEPLOY_BOOTSTRAP_PASSWORD="stale password",
                        DEPLOY_BOOTSTRAP_BECOME_PASSWORD="stale sudo", ANSIBLE_BECOME_PASS="stale dev",
                        DEPLOY_PLAYBOOK="bootstrap")
        env = DEPLOY.prepare_environment(self.event, self.env)
        self.assertEqual(env["ANSIBLE_BECOME_PASS"], self.env["DEV_PASSWORD"])
        for name in ("DEV_PASSWORD", "DEV_PASSWORD_HASH", "DEPLOY_BOOTSTRAP_PASSWORD", "DEPLOY_PLAYBOOK",
                     "DEPLOY_BOOTSTRAP_BECOME_PASSWORD", "GITHUB_EVENT_PATH", "DOPPLER_TOKEN"):
            self.assertNotIn(name, env)
        self.hash.assert_not_called()

    def test_valid_addresses_and_nondefault_port(self):
        self.env["DEPLOY_SSH_PORT"] = "2222"
        for address in ("2001:db8::10", "vps.example.com", "vps.example.com."):
            with self.subTest(address=address):
                self.event["inputs"]["server_address"] = address
                env = DEPLOY.prepare_environment(self.event, self.env)
                host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
                self.assertEqual(host["ansible_host"], address)
                self.assertEqual(host["ansible_port"], 2222)
                self.assertEqual(host["dev_host_ssh_port"], 2222)

    def test_missing_bootstrap_fields_keep_root_as_the_initial_account(self):
        del self.event["inputs"]["bootstrap_user"]
        del self.event["inputs"]["bootstrap_password"]
        env = DEPLOY.prepare_environment(self.event, self.env)
        self.assertNotIn("DEPLOY_BOOTSTRAP_PASSWORD", env)
        host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
        self.assertEqual(host["dev_host_bootstrap_user"], "root")

    def test_rejects_unsafe_or_malformed_inputs_before_key_processing(self):
        cases = {
            "server_address": ["", "user@host", "host:22", "$(id)", "host\nother", "--help", [], "a" * 64, "host.."],
            "bootstrap_user": ["--help", "root; id", ["root"]],
            "bootstrap_password": [" padded ", "bad\0value", []],
            "mode": ["--diff", []],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name):
                    event = {"inputs": {**self.event["inputs"], name: value}}
                    with self.assertRaises(ValueError):
                        DEPLOY.prepare_environment(event, self.env)
        self.key.assert_not_called()

    def test_missing_saved_settings_and_invalid_ports_fail_early(self):
        for name in ("DEV_PASSWORD", "DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, name: ""})
        for port in ("0", "65536", "bad", "22; id"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, "DEPLOY_SSH_PORT": port})
        self.key.assert_not_called()

    def test_malformed_event_has_a_static_error(self):
        for event in (None, [], {}, {"inputs": "unexpected"}):
            with self.assertRaisesRegex(ValueError, "workflow event must contain deployment inputs"):
                DEPLOY.prepare_environment(event, self.env)


class SetupSequenceTests(unittest.TestCase):
    def setUp(self):
        self.env = {"DEPLOY_MODE": "apply", "ANSIBLE_BECOME_PASS": "dev password placeholder"}
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.addCleanup(patch.stopall)
        patch("sys.stdout", self.output).start()
        patch("sys.stderr", self.errors).start()
        self.hash = patch.object(DEPLOY, "password_hash", return_value="$6$hash-placeholder").start()

    def configure(self, statuses, initial_password="initial password placeholder"):
        results = [subprocess.CompletedProcess([], status, "probe diagnostics\n", "") for status in statuses]
        with patch.object(DEPLOY, "run_playbook", side_effect=results) as run:
            status = DEPLOY.configure_server(self.env, initial_password)
        return status, run.call_args_list

    def test_new_server_switches_credentials_before_configuration(self):
        status, calls = self.configure([4, 0, 0])
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "bootstrap", "site"])
        self.assertTrue(calls[0].kwargs["capture_output"])
        bootstrap = calls[1].args[0]
        self.assertEqual(bootstrap["DEPLOY_BOOTSTRAP_PASSWORD"], "initial password placeholder")
        self.assertEqual(bootstrap["DEV_PASSWORD_HASH"], "$6$hash-placeholder")
        self.assertNotIn("ANSIBLE_BECOME_PASS", bootstrap)
        for call in (calls[0], calls[2]):
            self.assertNotIn("DEPLOY_BOOTSTRAP_PASSWORD", call.args[0])
            self.assertNotIn("DEV_PASSWORD_HASH", call.args[0])
            self.assertEqual(call.args[0]["ANSIBLE_BECOME_PASS"], "dev password placeholder")
        self.hash.assert_called_once_with("dev password placeholder")
        self.assertNotIn("password placeholder", self.output.getvalue() + self.errors.getvalue())

    def test_existing_access_skips_bootstrap_even_with_stale_initial_password(self):
        status, calls = self.configure([0, 0], initial_password="obsolete initial password")
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "site"])
        self.hash.assert_not_called()

    def test_sudo_and_validation_failures_stop_without_bootstrap(self):
        for failure in (1, 2, 99):
            with self.subTest(failure=failure):
                status, calls = self.configure([failure])
                self.assertEqual(status, failure)
                self.assertEqual([call.args[1] for call in calls], ["access"])
        self.assertIn("probe diagnostics", self.errors.getvalue())
        self.hash.assert_not_called()

    def test_new_server_check_mode_never_creates_access(self):
        self.env["DEPLOY_MODE"] = "check"
        status, calls = self.configure([4])
        self.assertEqual(status, 4)
        self.assertEqual(len(calls), 1)
        self.assertIn("Use apply for first setup", self.errors.getvalue())
        self.hash.assert_not_called()

    def test_existing_server_check_mode_reaches_site_without_switching_mode(self):
        self.env["DEPLOY_MODE"] = "check"
        status, calls = self.configure([0, 0])
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "site"])
        self.assertTrue(all(call.args[0]["DEPLOY_MODE"] == "check" for call in calls))

    def test_bootstrap_failure_stops_before_configuration(self):
        status, calls = self.configure([4, 4])
        self.assertEqual(status, 4)
        self.assertEqual([call.args[1] for call in calls], ["access", "bootstrap"])

    def test_configuration_failure_is_propagated(self):
        status, calls = self.configure([4, 0, 2])
        self.assertEqual(status, 2)
        self.assertEqual([call.args[1] for call in calls], ["access", "bootstrap", "site"])

    def test_initial_key_access_is_supported_without_a_password(self):
        status, calls = self.configure([4, 0, 0], initial_password="")
        self.assertEqual(status, 0)
        self.assertEqual(calls[1].args[0]["DEPLOY_BOOTSTRAP_PASSWORD"], "")


class CredentialPreparationTests(unittest.TestCase):
    def test_key_file_is_private_and_removed_after_failed_validation(self):
        seen = []

        def run(args, **kwargs):
            key = Path(args[-1])
            seen.append(key)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.read_text(), "private key placeholder\n")
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            return subprocess.CompletedProcess(args, 1, "")

        with patch.object(DEPLOY.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "key is invalid or requires a passphrase"):
                DEPLOY.public_key("private key placeholder")
        self.assertTrue(seen)
        self.assertFalse(seen[0].exists())

    def test_hash_passes_password_through_stdin_only(self):
        with patch.object(DEPLOY.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "$6$hash-placeholder\n")) as run:
            self.assertEqual(DEPLOY.password_hash("test $() ' % password"), "$6$hash-placeholder")
        self.assertEqual(run.call_args.args[0], ["openssl", "passwd", "-6", "-stdin"])
        self.assertEqual(run.call_args.kwargs["input"], "test $() ' % password\n")

    def test_rejects_passwords_that_cannot_be_hashed_exactly(self):
        for password in ("a\nb", "a\rb", "a\0b", "x" * 257, "é" * 129):
            with self.assertRaises(ValueError):
                DEPLOY.password_hash(password)

    def test_maintenance_runs_independently_and_preserves_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text("{}")
            with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}, clear=True), \
                    patch.object(DEPLOY, "prepare_environment", return_value={"PATH": "/usr/bin"}), \
                    patch.object(DEPLOY.subprocess, "run", return_value=subprocess.CompletedProcess([], 4)) as run:
                self.assertEqual(DEPLOY.main(["--maintenance"]), 4)
                self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin", "DEPLOY_PLAYBOOK": "maintenance"})


class WorkflowMaskingTests(unittest.TestCase):
    def test_first_step_masks_event_input_without_shell_interpolation(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        step = workflow["jobs"]["deploy"]["steps"][0]
        self.assertNotIn("env", step)
        self.assertNotIn("${{", step["run"])
        password = "placeholder % ' $(false)\r\nsecond line"
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps({"inputs": {"bootstrap_password": password}}))
            result = subprocess.run(["bash", "-e", "-c", step["run"]], text=True, capture_output=True,
                                    env={**os.environ, "GITHUB_EVENT_PATH": str(event)}, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "::add-mask::placeholder %25 ' $(false)%0D%0Asecond line\n")


if __name__ == "__main__":
    unittest.main()
