"""Verify workflow inputs and credential routing before invoking Ansible."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
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
            "DEPLOY_SUDO_USER": "deploymenttest",
            "SUDO_PASSWORD": "development password placeholder",
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
            "ansible_host": "192.0.2.10", "ansible_port": 22, "sudo_user": "deploymenttest",
            "dev_host_bootstrap_user": "ubuntu", "dev_host_ssh_port": 22,
            "dev_host_authorized_keys": ["ssh-ed25519 public-key-placeholder"],
        })
        self.assertEqual(env["DEPLOY_HOST"], "dev01")
        self.assertEqual(env["ANSIBLE_BECOME_PASS"], self.env["SUDO_PASSWORD"])
        for name in ("DEPLOY_SUDO_USER", "SUDO_PASSWORD", "DOPPLER_TOKEN", "GITHUB_EVENT_PATH", "SUDO_PASSWORD_HASH", "DEPLOY_BOOTSTRAP_PASSWORD"):
            self.assertNotIn(name, env)
        for name in ("SUDO_PASSWORD", "DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS", "DOPPLER_TOKEN", "GITHUB_EVENT_PATH"):
            self.assertNotIn(self.env[name], env["DEPLOY_INVENTORY"])
        self.hash.assert_not_called()
        self.key.assert_called_once_with(self.env["DEPLOY_SSH_PRIVATE_KEY"])

    def test_initial_probe_environment_drops_stale_credentials_and_playbook(self):
        self.env.update(SUDO_PASSWORD_HASH="stale hash", DEPLOY_BOOTSTRAP_PASSWORD="stale password",
                        DEPLOY_BOOTSTRAP_SECRET="saved bootstrap password placeholder",
                        DEPLOY_BOOTSTRAP_BECOME_PASSWORD="stale sudo", ANSIBLE_BECOME_PASS="stale dev",
                        DEPLOY_PLAYBOOK="bootstrap")
        env = DEPLOY.prepare_environment(self.event, self.env)
        self.assertEqual(env["ANSIBLE_BECOME_PASS"], self.env["SUDO_PASSWORD"])
        for name in ("SUDO_PASSWORD", "SUDO_PASSWORD_HASH", "DEPLOY_BOOTSTRAP_PASSWORD", "DEPLOY_PLAYBOOK",
                     "DEPLOY_BOOTSTRAP_SECRET",
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
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name):
                    event = {"inputs": {**self.event["inputs"], name: value}}
                    with self.assertRaises(ValueError):
                        DEPLOY.prepare_environment(event, self.env)
        self.key.assert_not_called()

    def test_missing_saved_settings_and_invalid_ports_fail_early(self):
        for name in ("DEPLOY_SUDO_USER", "SUDO_PASSWORD", "DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, name: ""})
        for port in ("0", "65536", "bad", "22; id"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, "DEPLOY_SSH_PORT": port})
        self.key.assert_not_called()

    def test_invalid_sudo_user_stops_before_key_processing(self):
        for username in ("", "root", "user name", "-option", "user; id", "$(id)", "user\nother", None, []):
            with self.subTest(username=username), self.assertRaisesRegex(ValueError, "Set SUDO_USER"):
                DEPLOY.prepare_environment(self.event, {**self.env, "DEPLOY_SUDO_USER": username})
        self.key.assert_not_called()

    def test_malformed_event_has_a_static_error(self):
        for event in (None, [], {}, {"inputs": "unexpected"}):
            with self.assertRaisesRegex(ValueError, "workflow event must contain deployment inputs"):
                DEPLOY.prepare_environment(event, self.env)


class InventoryBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.event = {"inputs": {"server_address": "first.example.com"}}
        self.inventory = {
            "development": {"hosts": ["first.example.com", "second.example.com"]},
            "_meta": {"hostvars": {
                "first.example.com": {
                    "ansible_host": "192.0.2.20", "dev_host_bootstrap_user": "debian",
                    "bootstrap_password_secret": "BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD",
                },
                "second.example.com": {
                    "ansible_host": "192.0.2.21", "dev_host_bootstrap_user": "ubuntu",
                    "bootstrap_password_secret": "BOOTSTRAP_SECOND_EXAMPLE_COM_PASSWORD",
                },
            }},
        }
        self.addCleanup(patch.stopall)
        patch.object(DEPLOY, "load_inventory", return_value=self.inventory).start()
        patch.object(DEPLOY, "public_key", return_value="ssh-ed25519 public-key-placeholder").start()
        self.env = {
            "DEPLOY_SUDO_USER": "deploymenttest", "SUDO_PASSWORD": "sudo password placeholder",
            "DEPLOY_SSH_PRIVATE_KEY": "private key placeholder", "DEPLOY_KNOWN_HOSTS": "host key placeholder",
            "DEPLOY_BOOTSTRAP_SECRET": "saved administrator password placeholder",
        }

    def run_main(self, resolve_only=False):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps(self.event))
            output = Path(temporary) / "output"
            with patch.dict(os.environ, {**self.env, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)}, clear=True), \
                    patch.object(DEPLOY, "configure_server", return_value=0) as configure:
                result = DEPLOY.main(resolve_only=resolve_only)
            return result, configure, output.read_text() if output.exists() else ""

    def test_selects_each_servers_own_administrator_and_secret(self):
        for address, resolved_address, username, secret in (
            ("first.example.com", "192.0.2.20", "debian", "BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD"),
            ("192.0.2.20", "192.0.2.20", "debian", "BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD"),
            ("second.example.com", "192.0.2.21", "ubuntu", "BOOTSTRAP_SECOND_EXAMPLE_COM_PASSWORD"),
        ):
            with self.subTest(address=address):
                self.event["inputs"]["server_address"] = address
                self.assertEqual(DEPLOY.resolve_target(self.event), {
                    "server_address": resolved_address, "bootstrap_user": username, "bootstrap_password_secret": secret,
                })

    def test_unknown_host_does_not_receive_a_registered_password(self):
        self.event["inputs"]["server_address"] = "unregistered.example.com"
        self.assertEqual(DEPLOY.resolve_target(self.event), {
            "server_address": "unregistered.example.com", "bootstrap_user": "root", "bootstrap_password_secret": "",
        })
        result, configure, _ = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(configure.call_args.args[1], "")
        self.assertFalse(configure.call_args.kwargs["require_bootstrap_password"])
        self.assertNotIn("DEPLOY_BOOTSTRAP_SECRET", configure.call_args.args[0])

    def test_explicit_password_takes_precedence_over_the_saved_secret(self):
        self.event["inputs"]["bootstrap_password"] = "manual password placeholder"
        self.assertEqual(DEPLOY.resolve_target(self.event)["bootstrap_password_secret"], "")
        result, configure, _ = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(configure.call_args.args[1], "manual password placeholder")

    def test_saved_password_cannot_be_used_for_a_different_administrator(self):
        self.event["inputs"]["bootstrap_user"] = "otheradmin"
        self.assertEqual(DEPLOY.resolve_target(self.event), {
            "server_address": "192.0.2.20", "bootstrap_user": "otheradmin", "bootstrap_password_secret": "",
        })

    def test_explicit_matching_administrator_can_use_the_saved_password(self):
        self.event["inputs"]["bootstrap_user"] = "debian"
        self.assertEqual(DEPLOY.resolve_target(self.event)["bootstrap_password_secret"], "BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD")

    def test_ambiguous_address_fails_before_selecting_credentials(self):
        self.inventory["_meta"]["hostvars"]["second.example.com"]["ansible_host"] = "192.0.2.20"
        self.event["inputs"]["server_address"] = "192.0.2.20"
        with self.assertRaisesRegex(ValueError, "multiple inventory entries"):
            DEPLOY.resolve_target(self.event)

    def test_rejects_unrelated_or_malformed_secret_references(self):
        for name in ("SUDO_PASSWORD", "GITHUB_TOKEN", "BOOTSTRAP_BAD\n_PASSWORD", None, []):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Inventory bootstrap secret names"):
                self.inventory["_meta"]["hostvars"]["first.example.com"]["bootstrap_password_secret"] = name
                DEPLOY.resolve_target(self.event)

    def test_main_uses_inventory_user_and_keeps_saved_password_out_of_ansible_environment(self):
        result, configure, _ = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(configure.call_args.args[1], self.env["DEPLOY_BOOTSTRAP_SECRET"])
        self.assertTrue(configure.call_args.kwargs["require_bootstrap_password"])
        env = configure.call_args.args[0]
        self.assertNotIn("DEPLOY_BOOTSTRAP_SECRET", env)
        self.assertNotIn(self.env["DEPLOY_BOOTSTRAP_SECRET"], env["DEPLOY_INVENTORY"])
        host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
        self.assertEqual(host["dev_host_bootstrap_user"], "debian")
        self.assertEqual(host["ansible_host"], "192.0.2.20")

    def test_resolution_outputs_only_the_secret_name_and_does_not_deploy(self):
        result, configure, output = self.run_main(resolve_only=True)
        self.assertEqual(result, 0)
        self.assertEqual(output, "bootstrap_password_secret=BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD\n")
        configure.assert_not_called()


class SetupSequenceTests(unittest.TestCase):
    def setUp(self):
        self.env = {"ANSIBLE_BECOME_PASS": "dev password placeholder"}
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.addCleanup(patch.stopall)
        patch("sys.stdout", self.output).start()
        patch("sys.stderr", self.errors).start()
        self.hash = patch.object(DEPLOY, "password_hash", return_value="$6$hash-placeholder").start()

    def configure(self, statuses, initial_password="initial password placeholder", **options):
        results = [subprocess.CompletedProcess([], status, "probe diagnostics\n", "") for status in statuses]
        with patch.object(DEPLOY, "run_playbook", side_effect=results) as run:
            status = DEPLOY.configure_server(self.env, initial_password, **options)
        return status, run.call_args_list

    def test_new_server_switches_credentials_before_configuration(self):
        status, calls = self.configure([4, 0, 0])
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "bootstrap", "site"])
        self.assertTrue(calls[0].kwargs["capture_output"])
        bootstrap = calls[1].args[0]
        self.assertEqual(bootstrap["DEPLOY_BOOTSTRAP_PASSWORD"], "initial password placeholder")
        self.assertEqual(bootstrap["SUDO_PASSWORD_HASH"], "$6$hash-placeholder")
        self.assertNotIn("ANSIBLE_BECOME_PASS", bootstrap)
        for call in (calls[0], calls[2]):
            self.assertNotIn("DEPLOY_BOOTSTRAP_PASSWORD", call.args[0])
            self.assertNotIn("SUDO_PASSWORD_HASH", call.args[0])
            self.assertEqual(call.args[0]["ANSIBLE_BECOME_PASS"], "dev password placeholder")
        self.hash.assert_called_once_with("dev password placeholder")
        self.assertNotIn("password placeholder", self.output.getvalue() + self.errors.getvalue())

    def test_existing_access_skips_bootstrap_even_with_stale_initial_password(self):
        status, calls = self.configure([0, 0], initial_password="obsolete initial password")
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "site"])
        self.hash.assert_not_called()

    def test_existing_access_does_not_require_the_saved_initial_password(self):
        status, calls = self.configure([0, 0], initial_password="", require_bootstrap_password=True)
        self.assertEqual(status, 0)
        self.assertEqual([call.args[1] for call in calls], ["access", "site"])
        self.hash.assert_not_called()

    def test_missing_saved_password_stops_after_probe_without_bootstrapping(self):
        with patch.object(DEPLOY, "run_playbook", return_value=subprocess.CompletedProcess([], 4, "", "")) as run:
            with self.assertRaisesRegex(ValueError, "bootstrap password secret"):
                DEPLOY.configure_server(self.env, "", require_bootstrap_password=True)
        self.assertEqual(run.call_count, 1)
        self.hash.assert_not_called()

    def test_sudo_and_validation_failures_stop_without_bootstrap(self):
        for failure in (1, 2, 99):
            with self.subTest(failure=failure):
                status, calls = self.configure([failure])
                self.assertEqual(status, failure)
                self.assertEqual([call.args[1] for call in calls], ["access"])
        self.assertIn("probe diagnostics", self.errors.getvalue())
        self.hash.assert_not_called()

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

    def test_main_preserves_configuration_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text('{"inputs": {}}')
            with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}, clear=True), \
                    patch.object(DEPLOY, "resolve_target", return_value={"server_address": "192.0.2.10", "bootstrap_user": "root", "bootstrap_password_secret": ""}), \
                    patch.object(DEPLOY, "prepare_environment", return_value={"PATH": "/usr/bin"}), \
                    patch.object(DEPLOY, "configure_server", return_value=4) as configure:
                self.assertEqual(DEPLOY.main(), 4)
                configure.assert_called_once_with({"PATH": "/usr/bin"}, "", require_bootstrap_password=False)

    def test_removed_maintenance_argument_cannot_trigger_server_setup(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/github-deploy.py"), "--maintenance"],
            env={}, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported workflow runner argument", result.stderr)


class WorkflowMaskingTests(unittest.TestCase):
    def test_deployment_uses_the_configured_environment_variable_and_secret(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        job = workflow["jobs"]["deploy"]
        self.assertEqual(job["environment"], "development")
        env = job["steps"][-1]["env"]
        self.assertEqual(env["DEPLOY_SUDO_USER"], "${{ vars.SUDO_USER }}")
        self.assertEqual(env["SUDO_PASSWORD"], "${{ secrets.SUDO_PASSWORD }}")
        self.assertEqual(env["DEPLOY_BOOTSTRAP_SECRET"], "${{ secrets[steps.target.outputs.bootstrap_password_secret] }}")

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
