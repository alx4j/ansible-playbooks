"""Finish initial APT jobs before measuring a disposable VM's convergence."""

import subprocess
import time


UNITS = ("apt-daily.service", "apt-daily-upgrade.service")


def completed(output):
    states = [dict(line.split("=", 1) for line in block.splitlines())
              for block in output.strip().split("\n\n") if block]
    assert {state["Id"] for state in states} == set(UNITS), "Missing APT unit status"
    if any(state["Job"] or state["ActiveState"] not in ("inactive", "failed") for state in states):
        return False
    assert all(state["ActiveState"] == "inactive" and state["Result"] == "success"
               and state["ExecMainStatus"] == "0" for state in states), states
    return True


def main():
    subprocess.run(["systemctl", "start", "--no-block", *UNITS], check=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        # Package upgrades may restart D-Bus. Reconnect for each observation.
        result = subprocess.run(["systemctl", "show", *UNITS, "-p", "Id", "-p", "ActiveState",
                                 "-p", "Result", "-p", "Job", "-p", "ExecMainStatus"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 0 and completed(result.stdout):
            print("Initial APT jobs completed successfully; service baseline can be recorded")
            return
        time.sleep(2)
    raise AssertionError("APT jobs did not finish within ten minutes")


if __name__ == "__main__":
    main()
