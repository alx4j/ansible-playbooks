"""Keep the destructive full-host fixture off local and self-hosted machines."""

import os
from pathlib import Path
import subprocess
import unittest


class HostedVMGuardTests(unittest.TestCase):
    def test_rejects_environments_that_are_not_disposable_hosted_linux(self):
        script = Path(__file__).resolve().parents[1] / "scripts/test-vm"
        cases = (
            {},
            {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_OS": "Linux"},
            {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Windows"},
        )
        for environment in cases:
            with self.subTest(environment=environment):
                result = subprocess.run([str(script)], env={"PATH": os.defpath, **environment},
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stderr.strip(), "Run this test only in a disposable GitHub-hosted Linux VM.")
