"""Verify workflow inputs and credential routing before invoking Ansible."""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode()
SPEC = importlib.util.spec_from_file_location("github_deploy", ROOT / "scripts/github-deploy.py")
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class GitHubDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.event = {"inputs": {
            "server_address": "192.0.2.10",
            "sudo_user": "deploymenttest",
            "bootstrap_user": "ubuntu", "bootstrap_password": "initial password placeholder",
        }}
        self.env = {
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
        for name in ("SUDO_PASSWORD", "DOPPLER_TOKEN", "GITHUB_EVENT_PATH", "SUDO_PASSWORD_HASH", "DEPLOY_BOOTSTRAP_PASSWORD"):
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

    def test_missing_doppler_token_does_not_reuse_a_stale_forwarding_variable(self):
        self.env.pop("DOPPLER_TOKEN")
        self.env["DEPLOY_DOPPLER_TOKEN"] = "stale token placeholder"
        env = DEPLOY.prepare_environment(self.event, self.env)
        self.assertNotIn("DEPLOY_DOPPLER_TOKEN", env)

    def test_projects_settings_reach_manual_and_automatic_playbooks(self):
        self.env["PROJECTS_DIR"] = "work-projects"
        self.env["PROJECTS_TOPIC"] = "server-projects"
        environments = (
            DEPLOY.prepare_environment(self.event, self.env),
            DEPLOY.prepare_inventory_environment("dev01", {"ansible_host": "192.0.2.10", "sudo_user": "tester"}, self.env),
        )
        for env in environments:
            with patch.object(DEPLOY.subprocess, "run") as run:
                DEPLOY.run_playbook(env, "site")
                self.assertEqual(run.call_args.kwargs["env"]["PROJECTS_DIR"], "work-projects")
                self.assertEqual(run.call_args.kwargs["env"]["PROJECTS_TOPIC"], "server-projects")

    def test_doppler_token_reaches_only_the_site_playbook(self):
        env = DEPLOY.prepare_environment(self.event, self.env)
        for playbook in ("access", "bootstrap", "site"):
            with self.subTest(playbook=playbook), patch.object(DEPLOY.subprocess, "run") as run:
                DEPLOY.run_playbook(env, playbook)
                child = run.call_args.kwargs["env"]
                self.assertNotIn("DEPLOY_DOPPLER_TOKEN", child)
                self.assertNotIn(self.env["DOPPLER_TOKEN"], child["DEPLOY_INVENTORY"])
                if playbook == "site":
                    self.assertEqual(child["DOPPLER_TOKEN"], self.env["DOPPLER_TOKEN"])
                else:
                    self.assertNotIn("DOPPLER_TOKEN", child)

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

    def test_manual_and_automatic_runs_preserve_the_same_host_configuration(self):
        variables = {
            "ansible_host": "192.0.2.10", "ansible_port": 2222,
            "dev_host_projects_dir": "work", "dev_host_go_version": "1.27.3",
            "dev_host_bootstrap_user": "staleuser", "bootstrap_password_secret": "UNUSED",
            "ansible_user": "staleuser", "dev_host_user": "staleuser", "sudo_user": "deploymenttest",
        }
        self.env["DEPLOY_SSH_PORT"] = "22"
        automatic = DEPLOY.prepare_inventory_environment("demo", variables, self.env)
        manual = DEPLOY.prepare_inventory_environment("demo", variables, self.env, inputs=self.event["inputs"])
        hosts = [json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["demo"]
                 for env in (automatic, manual)]
        self.assertEqual(hosts[1].pop("dev_host_bootstrap_user"), "ubuntu")
        self.assertEqual(hosts[0], hosts[1])
        self.assertEqual(hosts[1]["ansible_port"], 2222)
        self.assertEqual(hosts[1]["dev_host_projects_dir"], "work")
        self.assertEqual(hosts[1]["dev_host_go_version"], "1.27.3")
        self.assertEqual(hosts[1]["ansible_user"], "deploymenttest")
        self.assertEqual(hosts[1]["dev_host_user"], "deploymenttest")
        self.assertNotIn("bootstrap_password_secret", hosts[1])

    def test_missing_bootstrap_fields_keep_root_as_the_initial_account(self):
        del self.event["inputs"]["bootstrap_user"]
        del self.event["inputs"]["bootstrap_password"]
        del self.event["inputs"]["sudo_user"]
        env = DEPLOY.prepare_environment(self.event, self.env)
        self.assertNotIn("DEPLOY_BOOTSTRAP_PASSWORD", env)
        host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
        self.assertEqual(host["dev_host_bootstrap_user"], "root")
        self.assertEqual(host["sudo_user"], "dev")

    def test_manual_form_selects_account_even_when_inventory_names_another_user(self):
        variables = {"ansible_host": "192.0.2.10", "sudo_user": "registered"}
        for inputs, expected in (({}, "dev"), ({"sudo_user": "operator"}, "operator")):
            env = DEPLOY.prepare_inventory_environment("demo", variables, self.env, inputs=inputs)
            host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["demo"]
            self.assertEqual((host["ansible_user"], host["dev_host_user"], host["sudo_user"]),
                             (expected, expected, expected))

    def test_personal_key_is_added_separately_and_deduplicated_by_key_material(self):
        self.event["inputs"]["user_public_key"] = PUBLIC_KEY + " workstation\n"
        for runner_key, expected in (("ssh-ed25519 runner", ["ssh-ed25519 runner", PUBLIC_KEY]),
                                     (PUBLIC_KEY + " runner", [PUBLIC_KEY + " runner"])):
            self.key.return_value = runner_key
            env = DEPLOY.prepare_environment(self.event, self.env)
            host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
            self.assertEqual(host["dev_host_authorized_keys"], expected)

    def test_password_handoff_requires_separate_accounts_and_passwords(self):
        for inputs in ({"bootstrap_user": self.event["inputs"]["sudo_user"]},
                       {"bootstrap_password": self.env["SUDO_PASSWORD"]}):
            with self.subTest(field=next(iter(inputs))), self.assertRaises(ValueError):
                DEPLOY.prepare_environment({"inputs": {**self.event["inputs"], **inputs}}, self.env)
        self.key.assert_not_called()

    def test_rejects_unsafe_or_malformed_inputs_before_key_processing(self):
        cases = {
            "server_address": ["", "user@host", "host:22", "$(id)", "host\nother", "--help", [], "a" * 64,
                               "host..", "fe80::1%eth0\nother"],
            "bootstrap_user": ["--help", "root; id", ["root"]],
            "bootstrap_password": [" padded ", "bad\0value", []],
            "user_public_key": [[], "key file", "ssh-ed25519 YmFk", PUBLIC_KEY + "\n" + PUBLIC_KEY,
                                'command="false" ' + PUBLIC_KEY, "-----BEGIN OPENSSH PRIVATE KEY-----"],
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name):
                    event = {"inputs": {**self.event["inputs"], name: value}}
                    with self.assertRaises(ValueError):
                        DEPLOY.prepare_environment(event, self.env)
        self.key.assert_not_called()

    def test_missing_saved_settings_and_invalid_ports_fail_early(self):
        for name in ("SUDO_PASSWORD", "DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, name: ""})
        for port in ("0", "65536", "bad", "22; id"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                DEPLOY.prepare_environment(self.event, {**self.env, "DEPLOY_SSH_PORT": port})
        self.key.assert_not_called()

    def test_invalid_sudo_user_stops_before_key_processing(self):
        for username in ("", "root", "user name", "-option", "user; id", "$(id)", "user\nother", None, []):
            with self.subTest(username=username), self.assertRaisesRegex(ValueError, "non-root sudo username"):
                DEPLOY.prepare_environment({"inputs": {**self.event["inputs"], "sudo_user": username}}, self.env)
        self.key.assert_not_called()

    def test_malformed_event_has_a_static_error(self):
        for event in (None, [], {}, {"inputs": "unexpected"}):
            with self.assertRaisesRegex(ValueError, "workflow event must contain deployment inputs"):
                DEPLOY.prepare_environment(event, self.env)


class InventoryLoadingTests(unittest.TestCase):
    def test_explicit_empty_development_group_is_preserved(self):
        resolved = {"_meta": {"hostvars": {}}, "all": {"children": ["ungrouped", "development"]}}
        result = subprocess.CompletedProcess([], 0, json.dumps(resolved), "")
        with patch.object(DEPLOY.subprocess, "run", return_value=result):
            inventory = DEPLOY.load_inventory()
        self.assertEqual(DEPLOY.development_hosts(inventory), [])

    def test_missing_development_group_is_still_an_error(self):
        resolved = {"_meta": {"hostvars": {}}, "all": {"children": ["ungrouped"]}}
        result = subprocess.CompletedProcess([], 0, json.dumps(resolved), "")
        with patch.object(DEPLOY.subprocess, "run", return_value=result), self.assertRaisesRegex(ValueError, "must define"):
            DEPLOY.load_inventory()


class InventoryTargetTests(unittest.TestCase):
    def test_registered_alias_resolves_address_and_unlisted_server_is_allowed(self):
        inventory = {
            "development": {"hosts": ["alice@first.example.com"]},
            "_meta": {"hostvars": {"alice@first.example.com": {"ansible_host": "192.0.2.20"}}},
        }
        with patch.object(DEPLOY, "load_inventory", return_value=inventory):
            for address, expected in (("first.example.com", "192.0.2.20"),
                                      ("192.0.2.20", "192.0.2.20"),
                                      ("unlisted.example.com", "unlisted.example.com")):
                name, variables = DEPLOY.resolve_target(address, "dev")
                self.assertEqual(variables["ansible_host"], expected)
                self.assertEqual(name, "dev01" if address == "unlisted.example.com" else "first.example.com")
            inventory["development"]["hosts"].append("bob@second.example.com")
            inventory["_meta"]["hostvars"]["bob@second.example.com"] = {"ansible_host": "192.0.2.20"}
            with self.assertRaisesRegex(ValueError, "multiple inventory entries"):
                DEPLOY.resolve_target("192.0.2.20", "dev")
            name, variables = DEPLOY.resolve_target("192.0.2.20", "bob")
            self.assertEqual((name, variables["sudo_user"]), ("second.example.com", "bob"))

    def test_update_supports_literal_addresses_without_host_overrides(self):
        for address in ("host.example.com", "192.0.2.20", "2001:db8::1"):
            entry = f"operator@{address}"
            targets = DEPLOY.inventory_targets({"development": {"hosts": [entry]}})
            self.assertEqual(targets[entry], (address, {"ansible_host": address, "sudo_user": "operator"}))

    def test_manual_setup_uses_only_credentials_from_the_form(self):
        event = {"inputs": {"server_address": "first.example.com", "bootstrap_user": "debian",
                            "bootstrap_password": "manual password placeholder"}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "event.json"
            path.write_text(json.dumps(event))
            with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(path),
                                       "DEPLOY_BOOTSTRAP_SECRET": "unused stale password"}, clear=True), \
                    patch.object(DEPLOY, "resolve_target", return_value=("first.example.com", {"ansible_host": "192.0.2.20"})), \
                    patch.object(DEPLOY, "prepare_inventory_environment", return_value={"PATH": "/usr/bin"}) as prepare, \
                    patch.object(DEPLOY, "configure_server", return_value=0) as configure:
                self.assertEqual(DEPLOY.main(), 0)
                configure.assert_called_once_with({"PATH": "/usr/bin"}, "manual password placeholder")
                self.assertEqual(prepare.call_args.args[:2], ("first.example.com", {"ansible_host": "192.0.2.20"}))
                inputs = prepare.call_args.kwargs["inputs"]
                self.assertEqual(inputs["bootstrap_user"], "debian")


class PublicKeyUploadTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {"username": "tester", "server_address": "test.example.com", "user_public_key": PUBLIC_KEY}
        patcher = patch.object(DEPLOY, "load_inventory", return_value={"development": {"hosts": []}})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_inputs_are_mandatory_and_validated_before_connecting(self):
        for field, invalid in (("username", "-option"), ("server_address", "a,b"), ("user_public_key", "invalid")):
            for value in ("", None, invalid):
                with self.subTest(field=field, value=value), patch.object(DEPLOY, "run_playbook") as run:
                    with self.assertRaises(ValueError):
                        DEPLOY.upload_public_key({**self.inputs, field: value}, {})
                    run.assert_not_called()

    def test_upload_runs_only_the_key_playbook_with_scoped_connection_settings(self):
        target = {"ansible_host": "192.0.2.5", "ansible_port": 2222, "ansible_user": "other",
                  "ansible_become": True, "ansible_connection": "local"}
        env = {"DEPLOY_SSH_PRIVATE_KEY": "runner fixture", "DEPLOY_KNOWN_HOSTS": "host fixture",
               "SUDO_PASSWORD": "unused", "DOPPLER_TOKEN": "unused", "ANSIBLE_BECOME_PASS": "unused",
               "GITHUB_EVENT_PATH": "unused"}
        with patch.object(DEPLOY, "resolve_target", return_value=("alias", target)) as resolve, \
                patch.object(DEPLOY, "run_playbook", return_value=SimpleNamespace(returncode=4)) as run:
            self.assertEqual(DEPLOY.upload_public_key(self.inputs, env), 4)
        resolve.assert_called_once_with("test.example.com", "tester")
        child, playbook = run.call_args.args
        self.assertEqual(playbook, "upload-public-key")
        self.assertEqual(set(child), {"DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS", "DEPLOY_INVENTORY", "DEPLOY_HOST"})
        hosts = json.loads(child["DEPLOY_INVENTORY"])["development"]["hosts"]
        self.assertEqual(list(hosts), [child["DEPLOY_HOST"]])
        host = hosts[child["DEPLOY_HOST"]]
        self.assertEqual((host["ansible_user"], host["ansible_host"], host["ansible_port"]), ("tester", "192.0.2.5", 2222))
        self.assertEqual(host["user_public_key"], PUBLIC_KEY)
        self.assertNotIn("ansible_become", host)
        self.assertNotIn("ansible_connection", host)

    def test_upload_workflow_requires_three_inputs_and_only_the_runner_key_secret(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/upload-public-key.yml").read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(list(triggers), ["workflow_dispatch"])
        fields = triggers["workflow_dispatch"]["inputs"]
        self.assertEqual(set(fields), set(self.inputs))
        self.assertTrue(all(field["required"] for field in fields.values()))
        job = workflow["jobs"]["upload"]
        self.assertEqual(job["environment"], "development")
        upload = job["steps"][-1]
        self.assertIn("--upload-public-key", upload["run"])
        self.assertEqual(upload["env"], {"DEPLOY_SSH_PRIVATE_KEY": "${{ secrets.ANSIBLE_SSH_PRIVATE_KEY }}",
                                         "DEPLOY_KNOWN_HOSTS": "${{ vars.ANSIBLE_KNOWN_HOSTS }}",
                                         "DEPLOY_SSH_PORT": "${{ vars.ANSIBLE_SSH_PORT }}"})


class InventoryUpdateTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {
            "development": {"hosts": ["alice@first.example.com", "bob@second.example.com"]},
            "_meta": {"hostvars": {
                "alice@first.example.com": {"ansible_host": "192.0.2.20", "ansible_port": 2222,
                                      "dev_host_bootstrap_user": "debian",
                                      "bootstrap_password_secret": "BOOTSTRAP_FIRST_EXAMPLE_COM_PASSWORD",
                                      "dev_host_timezone": "Etc/UTC"},
                "bob@second.example.com": {"ansible_host": "192.0.2.21"},
            }},
        }
        self.env = {
            "SUDO_PASSWORD": "sudo password placeholder",
            "DEPLOY_SSH_PRIVATE_KEY": "private key placeholder", "DEPLOY_KNOWN_HOSTS": "host key placeholder",
            "DEPLOY_BOOTSTRAP_SECRET": "invalid initial password placeholder",
            "DEPLOY_BOOTSTRAP_PASSWORD": "stale initial password placeholder",
            "SUDO_PASSWORD_HASH": "stale hash placeholder",
        }
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.addCleanup(patch.stopall)
        patch.object(DEPLOY, "load_inventory", return_value=self.inventory).start()
        patch.object(DEPLOY, "public_key", return_value="ssh-ed25519 public-key-placeholder").start()
        self.hash = patch.object(DEPLOY, "password_hash").start()
        self.bootstrap = patch.object(DEPLOY, "configure_server", side_effect=AssertionError("Manual setup called")).start()
        patch("sys.stdout", self.output).start()
        patch("sys.stderr", self.errors).start()

    def update(self, statuses):
        results = [subprocess.CompletedProcess([], status, "probe diagnostics\n", "") for status in statuses]
        with patch.object(DEPLOY, "run_playbook", side_effect=results) as run:
            status = DEPLOY.update_inventory(self.env)
        self.hash.assert_not_called()
        self.bootstrap.assert_not_called()
        return status, [(call.args[0]["DEPLOY_HOST"], call.args[1]) for call in run.call_args_list], run.call_args_list

    def test_unready_server_is_skipped_and_next_server_is_updated(self):
        for failure in (2, 4):
            with self.subTest(access_status=failure):
                status, calls, _ = self.update([failure, 0, 0])
                self.assertEqual(status, 0)
                self.assertEqual(calls, [("first.example.com", "access"), ("second.example.com", "access"),
                                         ("second.example.com", "site")])
        self.assertIn("::warning::Skipped alice@first.example.com", self.output.getvalue())
        self.assertIn("run Bootstrap VPS manually", self.output.getvalue())

    def test_ready_servers_use_only_development_access_and_preserve_host_settings(self):
        status, calls, details = self.update([0, 0, 0, 0])
        self.assertEqual(status, 0)
        self.assertEqual(calls, [(name, stage) for name in ("first.example.com", "second.example.com")
                                for stage in ("access", "site")])
        for call in details:
            env = call.args[0]
            for name in ("DEPLOY_BOOTSTRAP_SECRET", "DEPLOY_BOOTSTRAP_PASSWORD", "SUDO_PASSWORD_HASH"):
                self.assertNotIn(name, env)
                self.assertNotIn(self.env[name], env["DEPLOY_INVENTORY"])
            host = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"][env["DEPLOY_HOST"]]
            user = "alice" if env["DEPLOY_HOST"] == "first.example.com" else "bob"
            self.assertEqual((host["ansible_user"], host["dev_host_user"], host["sudo_user"]), (user, user, user))
            self.assertNotIn("dev_host_bootstrap_user", host)
            self.assertNotIn("bootstrap_password_secret", host)
            if env["DEPLOY_HOST"] == "first.example.com":
                self.assertEqual(host["ansible_port"], 2222)
                self.assertEqual(host["dev_host_timezone"], "Etc/UTC")

    def test_configuration_failure_fails_the_run_and_continues_to_next_server(self):
        status, calls, _ = self.update([0, 2, 0, 0])
        self.assertEqual(status, 1)
        self.assertEqual(calls[-1], ("second.example.com", "site"))
        self.assertIn("Failed: configuration error", self.output.getvalue())

    def test_runner_and_validation_errors_are_failures_not_readiness_warnings(self):
        for error in (1, 99):
            with self.subTest(status=error):
                status, calls, _ = self.update([error, 0, 0])
                self.assertEqual(status, 1)
                self.assertNotIn(("first.example.com", "site"), calls)
        self.assertNotIn("::warning::", self.output.getvalue())

    def test_all_skipped_servers_are_visible_in_job_summary_without_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "summary.md"
            self.env["GITHUB_STEP_SUMMARY"] = str(summary)
            status, calls, _ = self.update([4, 2])
            report = summary.read_text()
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 2)
        for name in self.inventory["development"]["hosts"]:
            self.assertIn(f"| {name} | Skipped:", report)
        self.assertNotIn("Updated", report)
        for name in ("SUDO_PASSWORD", "DEPLOY_BOOTSTRAP_SECRET", "DEPLOY_SSH_PRIVATE_KEY"):
            self.assertNotIn(self.env[name], report + self.output.getvalue() + self.errors.getvalue())

    def test_all_targets_are_validated_before_any_connection(self):
        variables = self.inventory["_meta"]["hostvars"]["bob@second.example.com"]
        for setting, value in (("ansible_host", "--unsafe"), ("ansible_port", 0), ("ansible_port", 65536)):
            with self.subTest(setting=setting, value=value):
                previous = variables.copy()
                variables[setting] = value
                with patch.object(DEPLOY, "run_playbook") as run, self.assertRaises(ValueError):
                    DEPLOY.update_inventory(self.env)
                run.assert_not_called()
                variables.clear()
                variables.update(previous)

    def test_unqualified_and_malformed_entries_fail_before_connecting(self):
        for entry in ("bare.example.com", "root@host", "@host", "two@users@host", "bob@--limit", "bob@"):
            self.inventory["development"]["hosts"] = ["alice@first.example.com", entry]
            with self.subTest(entry=entry), patch.object(DEPLOY, "run_playbook") as run, self.assertRaises(ValueError):
                DEPLOY.update_inventory(self.env)
            run.assert_not_called()

    def test_missing_credentials_fail_before_any_connection(self):
        for name in ("SUDO_PASSWORD", "DEPLOY_KNOWN_HOSTS", "DEPLOY_SSH_PRIVATE_KEY"):
            with self.subTest(name=name), patch.object(DEPLOY, "run_playbook") as run, self.assertRaises(ValueError):
                DEPLOY.update_inventory({**self.env, name: ""})
            run.assert_not_called()

    def test_empty_inventory_reports_no_work_without_loading_credentials_or_connecting(self):
        self.inventory["development"]["hosts"] = []
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "summary.md"
            with patch.object(DEPLOY, "prepare_inventory_environment") as prepare, \
                    patch.object(DEPLOY, "run_playbook") as run:
                status = DEPLOY.update_inventory({"GITHUB_STEP_SUMMARY": str(summary)})
            self.assertEqual(status, 0)
            prepare.assert_not_called()
            run.assert_not_called()
            self.assertIn("No enabled servers", summary.read_text())
        self.assertIn("::notice::", self.output.getvalue())

    def test_child_groups_are_included_once_and_unrelated_hosts_are_excluded(self):
        self.inventory["development"] = {"hosts": ["first.example.com"], "children": ["region"]}
        self.inventory["region"] = {"hosts": ["first.example.com", "second.example.com"]}
        self.inventory["unrelated"] = {"hosts": ["excluded.example.com"]}
        self.assertEqual(DEPLOY.development_hosts(self.inventory), ["first.example.com", "second.example.com"])

    def test_automatic_entry_point_does_not_resolve_manual_inputs_or_initial_secrets(self):
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(DEPLOY, "resolve_target", side_effect=AssertionError("Manual credentials selected")), \
                patch.object(DEPLOY, "run_playbook", return_value=subprocess.CompletedProcess([], 4, "", "")):
            self.assertEqual(DEPLOY.main(update_only=True), 0)


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

    def test_existing_access_does_not_require_an_initial_password(self):
        status, calls = self.configure([0, 0], initial_password="")
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
                    patch.object(DEPLOY, "resolve_target", return_value=("dev01", {"ansible_host": "192.0.2.10"})), \
                    patch.object(DEPLOY, "prepare_inventory_environment", return_value={"PATH": "/usr/bin"}), \
                    patch.object(DEPLOY, "configure_server", return_value=4) as configure:
                self.assertEqual(DEPLOY.main(), 4)
                configure.assert_called_once_with({"PATH": "/usr/bin"}, "")

    def test_removed_maintenance_argument_cannot_trigger_server_setup(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/github-deploy.py"), "--maintenance"],
            env={}, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported workflow runner argument", result.stderr)


class WorkflowMaskingTests(unittest.TestCase):
    def test_only_successful_main_push_validation_can_start_an_automatic_run(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers["workflow_run"], {
            "workflows": ["Validate playbooks"], "types": ["completed"], "branches": ["main"],
        })
        job = workflow["jobs"]["deploy"]
        expression = " ".join(job["if"].split()).replace("&&", "and").replace("||", "or")
        base = {"event_name": "workflow_run", "ref": "refs/heads/main", "repository": "owner/repo", "sha": "current-main",
                "event": {"repository": {"default_branch": "main"}, "workflow_run": {
                    "event": "push", "head_branch": "main", "head_repository": {"full_name": "owner/repo"},
                    "conclusion": "success", "head_sha": "current-main",
                }}}
        for changed, expected in (({}, True), ({"event": "pull_request"}, False),
                                  ({"head_branch": "other"}, False), ({"conclusion": "failure"}, False),
                                  ({"conclusion": "cancelled"}, False), ({"head_sha": "old-main"}, False),
                                  ({"head_repository": {"full_name": "fork/repo"}}, False)):
            context = json.loads(json.dumps(base))
            context["event"]["workflow_run"].update(changed)
            github = json.loads(json.dumps(context), object_hook=lambda value: SimpleNamespace(**value))
            with self.subTest(event=changed):
                self.assertEqual(eval(expression, {"__builtins__": {}}, {"github": github}), expected)
        for branch, expected in (("main", True), ("feature", False)):
            context = {**base, "event_name": "workflow_dispatch", "ref": f"refs/heads/{branch}"}
            github = json.loads(json.dumps(context), object_hook=lambda value: SimpleNamespace(**value))
            self.assertEqual(eval(expression, {"__builtins__": {}, "format": lambda value, arg: value.format(arg)},
                                  {"github": github}), expected)
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ github.event.workflow_run.head_sha || github.sha }}")

    def test_automatic_run_has_no_initial_credential_step_or_secret(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        steps = workflow["jobs"]["deploy"]["steps"]
        automatic = next(step for step in steps if "--update-inventory" in step.get("run", ""))
        self.assertEqual(automatic["if"], "github.event_name == 'workflow_run'")
        self.assertEqual(set(automatic["env"]), {
            "DEPLOY_KNOWN_HOSTS", "DEPLOY_SSH_PORT", "DEPLOY_SSH_PRIVATE_KEY", "SUDO_PASSWORD",
            "PROJECTS_DIR", "PROJECTS_TOPIC", "DOPPLER_TOKEN",
        })
        self.assertEqual(automatic["env"]["DOPPLER_TOKEN"], "${{ secrets.DOPPLER_TOKEN }}")
        self.assertNotIn("BOOTSTRAP_", json.dumps(steps))
        self.assertEqual(steps[-1]["if"], "github.event_name == 'workflow_dispatch'")

    def test_deployment_uses_the_configured_environment_variable_and_secret(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        job = workflow["jobs"]["deploy"]
        self.assertEqual(job["environment"], "development")
        env = job["steps"][-1]["env"]
        inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["sudo_user"]["default"], "dev")
        self.assertEqual(inputs["user_public_key"]["type"], "string")
        self.assertFalse(inputs["user_public_key"]["required"])
        self.assertEqual(env["SUDO_PASSWORD"], "${{ secrets.SUDO_PASSWORD }}")
        self.assertNotIn("DEPLOY_BOOTSTRAP_SECRET", env)
        for step in job["steps"]:
            if "python3 scripts/github-deploy.py" in step.get("run", ""):
                self.assertEqual(step["env"]["DEPLOY_SSH_PRIVATE_KEY"], "${{ secrets.ANSIBLE_SSH_PRIVATE_KEY }}")
                self.assertEqual(step["env"]["PROJECTS_DIR"], "${{ vars.PROJECTS_DIR }}")
                self.assertEqual(step["env"]["PROJECTS_TOPIC"], "${{ vars.PROJECTS_TOPIC }}")

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
