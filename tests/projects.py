"""Exercise projects directory tasks against a disposable local home."""

import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProjectsDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ansible-projects-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.playbook = self.root / "projects.yml"
        self.playbook.write_text(json.dumps([{
            "name": "Test the projects directory",
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "tasks": [{
                "name": "Configure the projects directory",
                "ansible.builtin.import_tasks": str(ROOT / "roles/dev_host/tasks/projects.yml"),
            }],
        }]))
        self.ansible = shutil.which("ansible-playbook")
        self.assertIsNotNone(self.ansible, "Run tests with the repository's Ansible environment on PATH")

    def configure(self, name, success=True):
        result = subprocess.run(
            [self.ansible, "-i", "localhost,", str(self.playbook), "-e", json.dumps({
                "dev_host_home": str(self.home),
                "dev_host_user": pwd.getpwuid(os.getuid()).pw_name,
                "dev_host_projects_dir": name,
            })],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_creates_private_directory_and_preserves_contents_on_rerun(self):
        name = "work_projects-2026.11"
        self.configure(name)
        directory = self.home / name
        self.assertTrue(directory.is_dir())
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(directory.stat().st_uid, os.getuid())
        self.assertEqual(directory.stat().st_gid, grp.getgrnam(pwd.getpwuid(os.getuid()).pw_name).gr_gid)
        marker = directory / "existing-project.txt"
        marker.write_text("Preserve this project\n")
        result = self.configure(name)
        self.assertIn("changed=0", result.stdout)
        self.assertEqual(marker.read_text(), "Preserve this project\n")

    def test_rejects_invalid_names_before_creating_anything(self):
        for name in ["", ".", "..", "/tmp/projects", "../escape", "nested/projects", ".hidden", "two words", "projects\n", 42]:
            with self.subTest(name=name):
                result = self.configure(name, success=False)
                self.assertIn("PROJECTS_DIR must be a single directory name", result.stdout)
                self.assertEqual(list(self.home.iterdir()), [])

    def test_rejects_symlink_without_changing_its_target(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o755)
        marker = outside / "existing-project.txt"
        marker.write_text("Leave this directory alone\n")
        (self.home / "projects").symlink_to(outside, target_is_directory=True)
        result = self.configure("projects", success=False)
        self.assertIn("The projects directory must not be a symlink", result.stdout)
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)
        self.assertEqual(marker.read_text(), "Leave this directory alone\n")


if __name__ == "__main__":
    unittest.main()
