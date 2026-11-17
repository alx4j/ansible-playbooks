"""Exercise the real Unix protocol and wrapper using synthetic credentials only."""

from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

FILES = Path(__file__).resolve().parents[1] / "roles/doppler_secrets/files"
WRAPPER = FILES / "skill/scripts/doppler.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BROKER = load("doppler_broker", FILES / "broker.py")
CLIENT = load("doppler_client", WRAPPER)


class DopplerTests(unittest.TestCase):
    @contextmanager
    def server(self, fetch=lambda token, names: {name: "fixture-value" for name in names}, uid=None):
        with tempfile.TemporaryDirectory(prefix="doppler-test-") as directory:
            path = str(Path(directory) / "socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen()
                listener.settimeout(5)

                def serve():
                    connection, _ = listener.accept()
                    with connection:
                        BROKER.handle(connection, os.getuid() if uid is None else uid, "fixture-bootstrap", fetch)

                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                yield path
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "Fixture connection did not finish")

    def cli(self, path, *arguments, **kwargs):
        return subprocess.run([sys.executable, str(WRAPPER), "--socket", path, *arguments],
                              text=True, capture_output=True, timeout=10, **kwargs)

    def test_selected_aliases_environment_and_process_context(self):
        requests = []

        def fetch(token, names):
            self.assertEqual(token, "fixture-bootstrap")
            requests.append(names)
            return {"SOURCE_KEY": "fixture-value", "UNREQUESTED": "must-not-pass"}

        with self.server(fetch) as path, tempfile.TemporaryDirectory() as directory:
            program = """import os, sys
assert os.environ['TARGET_KEY'] == 'fixture-value'
assert set(os.environ) <= {'TARGET_KEY', 'PATH', 'LC_CTYPE'}
assert sys.argv[1:] == ['literal; $(command)', 'two words']
assert sys.stdin.read() == 'input-data'
assert os.getcwd() == os.path.dirname(sys.argv[0])
print('application-ok')
sys.exit(17)
"""
            script = Path(directory) / "application.py"
            script.write_text(program)
            result = self.cli(path, "run", "--map", "SOURCE_KEY=TARGET_KEY", "--clean-env", "--inherit", "PATH",
                              "--", sys.executable, str(script), "literal; $(command)", "two words",
                              input="input-data", cwd=directory,
                              env={"PATH": os.defpath, "DOPPLER_TOKEN": "ambient-bootstrap", "OTHER_SECRET": "ambient-secret",
                                   "SOURCE_KEY": "stale-source", "TARGET_KEY": "stale-target", "CREDENTIALS_DIRECTORY": "/unused"})
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(result.stdout, "application-ok\n")
        self.assertEqual(requests, [["SOURCE_KEY"]])

    def test_check_and_doctor_never_print_values(self):
        with self.server() as path:
            result = self.cli(path, "--json", "check", "--require", "API_KEY")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["available"], ["API_KEY"])
        self.assertNotIn("fixture-value", result.stdout + result.stderr)
        with self.server(lambda *_: self.fail("Offline check contacted Doppler")) as path:
            result = self.cli(path, "doctor", "--offline")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_peer_identity_is_checked_before_fetch(self):
        with self.server(lambda *_: self.fail("Unauthorized request reached Doppler"), uid=os.getuid() + 1) as path:
            result = self.cli(path, "check", "--require", "API_KEY")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("fixture-bootstrap", result.stdout + result.stderr)

    def test_upstream_failures_do_not_expose_values_or_launch_command(self):
        def fail(*_):
            raise RuntimeError("fixture-bootstrap fixture-value")
        with self.server(fail) as path:
            result = self.cli(path, "run", "--require", "API_KEY", "--", sys.executable, "-c", "print('must-not-run')")
        self.assertEqual(result.returncode, 2)
        for value in ("fixture-bootstrap", "fixture-value", "must-not-run"):
            self.assertNotIn(value, result.stdout + result.stderr)

    def test_unavailable_service_has_no_local_fallback(self):
        result = self.cli("/nonexistent/doppler-test.sock", "run", "--require", "API_KEY", "--",
                          sys.executable, "-c", "print('must-not-run')", env={"PATH": os.defpath, "API_KEY": "stale-value"})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("must-not-run", result.stdout)

    def test_empty_missing_and_invalid_values_fail_before_execution(self):
        for values in ({}, {"API_KEY": ""}, {"API_KEY": None}, {"API_KEY": "bad\0value"}):
            with self.subTest(values=values), self.server(lambda *_, values=values: values) as path:
                result = self.cli(path, "run", "--require", "API_KEY", "--", sys.executable, "-c", "print('must-not-run')")
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("must-not-run", result.stdout)

    def test_invalid_selection_and_protocol_do_not_fetch(self):
        for names in ([], ["DOPPLER_TOKEN"], ["A,B"], ["../KEY"], ["A", "A"], [1], ["A"] * 65):
            with self.subTest(names=names), self.server(lambda *_: self.fail("Invalid names reached Doppler")) as path:
                with self.assertRaises(CLIENT.SafeError):
                    CLIENT.request(path, {"operation": "fetch", "names": names})
        with self.server(lambda *_: self.fail("Invalid operation reached Doppler")) as path:
            with self.assertRaises(CLIENT.SafeError):
                CLIENT.request(path, {"operation": "fetch", "names": ["API_KEY"], "url": "https://invalid.example"})

    def test_alias_collisions_and_reserved_inheritance_are_rejected(self):
        for required, aliases in (([], []), (["DOPPLER_TOKEN"], []), (["A"], ["B=A"]), ([], ["A=B", "A=C"])):
            with self.assertRaises(CLIENT.SafeError):
                CLIENT.selection(required, aliases)
        result = self.cli("/unused", "run", "--require", "API_KEY", "--clean-env", "--inherit", "DOPPLER_TOKEN",
                          "--", "/usr/bin/true")
        self.assertEqual(result.returncode, 2)

    def test_http_fetch_selects_names_and_filters_response(self):
        response = io.BytesIO(json.dumps({"API_KEY": "fixture-value", "EXTRA": "not-requested"}).encode())
        with patch.object(BROKER, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(BROKER.fetch_secrets("fixture-bootstrap", ["API_KEY"]), {"API_KEY": "fixture-value"})
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(urlparse(request.full_url).netloc, "api.doppler.com")
        self.assertEqual(parse_qs(urlparse(request.full_url).query)["secrets"], ["API_KEY"])
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture-bootstrap")
        self.assertIsNone(BROKER.NoRedirect().redirect_request(None, None, 302, None, None, "https://invalid.example"))

    def test_large_upstream_response_is_rejected(self):
        with patch.object(BROKER, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(b"x" * (BROKER.RESPONSE_LIMIT + 1))
            with self.assertRaises(ValueError):
                BROKER.fetch_secrets("fixture-bootstrap", ["API_KEY"])


if __name__ == "__main__":
    unittest.main()
