"""Protect toolkit package updates, existing instructions, and local Git work."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_toolkit", ROOT / "roles/agent_toolkit/files/install.py")
TOOLKIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLKIT)


def fixture(checkout, names=("common", "infrastructure")):
    (checkout / ".agents/plugins").mkdir(parents=True)
    entries = []
    for name in names:
        plugin = checkout / "plugins" / name
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / "skills" / name).mkdir(parents=True)
        manifest = {"name": name, "version": "0.1.0", "description": "Fixture plugin", "skills": "./skills"}
        for path in (plugin / "plugin.json", plugin / ".codex-plugin/plugin.json"):
            path.write_text(json.dumps(manifest))
        (plugin / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Fixture skill\n---\nFixture contents\n")
        entries.append({"name": name, "source": {"source": "local", "path": f"./plugins/{name}"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}})
    (checkout / ".agents/plugins/marketplace.json").write_text(json.dumps({"name": TOOLKIT.MARKETPLACE, "plugins": entries}))
    (checkout / "config/codex").mkdir(parents=True)
    (checkout / "config/codex/AGENTS.md").write_text("Fixture global instructions.\n")


class ToolkitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.checkout = self.home / ".local/share/alx4j-agent-toolkit"
        fixture(self.checkout)
        (self.home / ".codex").mkdir()
        self.marketplaces, self.installed, self.adds = [], {}, []

    def cli(self, *args):
        if args == ("marketplace", "list"):
            return {"marketplaces": self.marketplaces}
        if args[:2] == ("marketplace", "add"):
            self.marketplaces.append({"name": TOOLKIT.MARKETPLACE, "root": str(self.checkout)})
            return {}
        if args[0] == "list":
            return {"installed": list(self.installed.values())}
        if args[0] == "add":
            name = args[1].split("@")[0]
            source = self.checkout / "plugins" / name
            version = json.loads((source / "plugin.json").read_text())["version"]
            cache = self.cache(name, version)
            if cache.exists():
                shutil.rmtree(cache)
            shutil.copytree(source, cache)
            self.installed[name] = {"name": name, "version": version, "enabled": True}
            self.adds.append(name)
            return {"installedPath": str(cache)}
        raise AssertionError(args)

    def cache(self, name="common", version="0.1.0"):
        return self.home / ".codex/plugins/cache" / TOOLKIT.MARKETPLACE / name / version

    def install(self, manage_instructions=True):
        installer = TOOLKIT.Installer(self.checkout, self.home, ["common", "infrastructure"], manage_instructions)
        with patch.object(installer, "cli", side_effect=self.cli):
            return installer.run()

    def test_install_then_rerun_without_rewriting_packages(self):
        result = self.install()
        self.assertEqual(result["installed"], ["common", "infrastructure"])
        before = (self.cache() / "plugin.json").stat().st_mtime_ns
        self.assertFalse(self.install()["changed"])
        self.assertEqual((self.cache() / "plugin.json").stat().st_mtime_ns, before)
        self.assertEqual((self.home / ".codex/AGENTS.md").resolve(), self.checkout / "config/codex/AGENTS.md")

    def test_changed_content_reinstalls_only_the_affected_plugin_even_without_version_bump(self):
        self.install()
        skill = self.checkout / "plugins/common/skills/common/SKILL.md"
        skill.write_text(skill.read_text() + "Updated\n")
        self.assertEqual(self.install()["installed"], ["common"])
        self.assertEqual((self.cache() / "skills/common/SKILL.md").read_text(), skill.read_text())
        self.assertFalse(self.install()["changed"])

    def test_repair_disabled_plugin_and_missing_cache(self):
        self.install()
        self.installed["common"]["enabled"] = False
        self.assertEqual(self.install()["installed"], ["common"])
        shutil.rmtree(self.cache("infrastructure"))
        self.assertEqual(self.install()["installed"], ["infrastructure"])

    def test_executable_permission_changes_refresh_the_package(self):
        script = self.checkout / "plugins/common/tool.sh"
        script.write_text("#!/bin/sh\nexit 0\n")
        self.install()
        script.chmod(0o755)
        self.assertEqual(self.install()["installed"], ["common"])
        self.assertTrue((self.cache() / "tool.sh").stat().st_mode & 0o111)
        self.assertFalse(self.install()["changed"])

    def test_version_bump_installs_the_new_package(self):
        self.install()
        for path in (self.checkout / "plugins/common").glob("**/plugin.json"):
            manifest = json.loads(path.read_text())
            manifest["version"] = "0.2.0"
            path.write_text(json.dumps(manifest))
        self.assertEqual(self.install()["installed"], ["common"])
        self.assertTrue(self.cache(version="0.2.0").is_dir())
        self.assertFalse(self.install()["changed"])

    def test_migration_preserves_instructions_legacy_customizations_and_overrides(self):
        old = self.home / ".codex/AGENTS.md"
        old.write_text("Personal instructions\n")
        override = self.home / ".codex/AGENTS.override.md"
        override.write_text("Keep my override\n")
        legacy = self.home / ".codex/skills/doppler-secrets"
        legacy.mkdir(parents=True)
        (legacy / "custom.txt").write_text("Local customization\n")
        result = self.install()
        backups = [Path(path) for path in result["backups"]]
        self.assertEqual(next(path for path in backups if path.name == "AGENTS.md").read_text(), "Personal instructions\n")
        self.assertEqual((next(path for path in backups if path.name == "doppler-secrets") / "custom.txt").read_text(),
                         "Local customization\n")
        self.assertEqual(override.read_text(), "Keep my override\n")
        self.assertTrue(result["warnings"])
        self.assertFalse(legacy.exists())
        self.assertFalse(self.install()["changed"])

    def test_missing_manifest_and_source_symlink_fail_before_migration(self):
        old = self.home / ".codex/AGENTS.md"
        old.write_text("Original\n")
        compatibility = self.checkout / "plugins/common/.codex-plugin/plugin.json"
        saved = compatibility.read_text()
        compatibility.unlink()
        with self.assertRaisesRegex(TOOLKIT.ToolkitError, "compatibility update"):
            self.install()
        self.assertEqual(old.read_text(), "Original\n")
        self.assertFalse(self.marketplaces)
        compatibility.write_text(saved)
        (self.checkout / "plugins/common/external").symlink_to(old)
        with self.assertRaisesRegex(TOOLKIT.ToolkitError, "symlinks"):
            self.install()

    def test_foreign_marketplace_is_not_replaced(self):
        self.marketplaces = [{"name": TOOLKIT.MARKETPLACE, "root": str(self.home / "projects/toolkit")}]
        with self.assertRaisesRegex(TOOLKIT.ToolkitError, "points elsewhere"):
            self.install()
        self.assertFalse(self.adds)

    def test_package_failure_preserves_standalone_skill(self):
        legacy = self.home / ".codex/skills/doppler-secrets"
        legacy.mkdir(parents=True)
        with patch.object(TOOLKIT.Installer, "cli", side_effect=TOOLKIT.ToolkitError("Fixture failure")):
            with self.assertRaises(TOOLKIT.ToolkitError):
                TOOLKIT.Installer(self.checkout, self.home, ["common"]).run()
        self.assertTrue(legacy.exists())

    def test_global_instructions_can_remain_independently_managed(self):
        target = self.home / ".codex/AGENTS.md"
        target.write_text("Custom instructions\n")
        self.install(manage_instructions=False)
        self.assertFalse(target.is_symlink())
        self.assertEqual(target.read_text(), "Custom instructions\n")

    def test_checkout_guard_rejects_unmanaged_modified_and_unpushed_work(self):
        with self.assertRaises(TOOLKIT.ToolkitError):
            TOOLKIT.check_checkout(self.checkout, "fixture", self.home)
        def git(*args):
            subprocess.run(["git", "-C", str(self.checkout), *args], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "initial")
        git("remote", "add", "origin", "fixture")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.checkout / ".git/ansible-agent-toolkit.json").write_text(json.dumps({"repository": "fixture"}))
        TOOLKIT.check_checkout(self.checkout, "fixture", self.home)
        local = self.checkout / "local-work"
        local.write_text("Preserve me\n")
        with self.assertRaisesRegex(TOOLKIT.ToolkitError, "local changes"):
            TOOLKIT.check_checkout(self.checkout, "fixture", self.home)
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "local")
        with self.assertRaisesRegex(TOOLKIT.ToolkitError, "local commits"):
            TOOLKIT.check_checkout(self.checkout, "fixture", self.home)
        self.assertEqual(local.read_text(), "Preserve me\n")


if __name__ == "__main__":
    unittest.main()
