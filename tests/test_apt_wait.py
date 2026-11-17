"""Keep pending and failed package jobs out of the convergence baseline."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("wait_apt", Path(__file__).with_name("wait-apt.py"))
wait_apt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait_apt)


class AptCompletionTests(unittest.TestCase):
    def status(self, active="inactive", result="success", exit_status="0", job=""):
        return "\n\n".join(f"Id={unit}\nActiveState={active}\nResult={result}\n"
                           f"ExecMainStatus={exit_status}\nJob={job}" for unit in wait_apt.UNITS)

    def test_waits_for_running_or_queued_jobs(self):
        self.assertFalse(wait_apt.completed(self.status(active="activating")))
        self.assertFalse(wait_apt.completed(self.status(job="42")))

    def test_requires_successful_completion_of_both_units(self):
        self.assertTrue(wait_apt.completed(self.status()))
        for output in (self.status(active="failed", result="exit-code", exit_status="1"),
                       self.status(result="timeout"), self.status(exit_status="1"),
                       self.status().split("\n\n")[0]):
            with self.subTest(output=output), self.assertRaises(AssertionError):
                wait_apt.completed(output)


if __name__ == "__main__":
    unittest.main()
