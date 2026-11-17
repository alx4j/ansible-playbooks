#!/usr/bin/env python3
"""Run a command with selected secrets from the local Doppler service."""

import argparse
import json
import os
import re
import socket
import sys

SOCKET = "/run/doppler-secrets.sock"
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
LIMIT = 2 * 1024 * 1024


class SafeError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SafeError("Invalid arguments; use --help for syntax.")


def parser():
    result = Parser(description=__doc__)
    result.add_argument("--json", action="store_true")
    result.add_argument("--socket", default=SOCKET, help="Explicit trusted Unix socket; normally leave unset")
    actions = result.add_subparsers(dest="action", required=True)
    actions.add_parser("paths")
    doctor = actions.add_parser("doctor")
    doctor.add_argument("--offline", action="store_true")
    for action in ("check", "run"):
        command = actions.add_parser(action)
        command.add_argument("--require", action="append", default=[])
        command.add_argument("--map", action="append", default=[])
        if action == "run":
            command.add_argument("--clean-env", action="store_true")
            command.add_argument("--inherit", action="append", default=[])
            command.add_argument("command", nargs=argparse.REMAINDER)
    return result


def valid_name(name):
    return bool(NAME.fullmatch(name)) and not name.startswith("DOPPLER_") and name != "CREDENTIALS_DIRECTORY"


def selection(required, aliases):
    pairs = [(name, name) for name in required]
    for alias in aliases:
        if alias.count("=") != 1:
            raise SafeError("Mappings must contain SOURCE=TARGET names only.")
        pairs.append(tuple(alias.split("=")))
    mappings = {}
    for source, target in pairs:
        if not valid_name(source) or not valid_name(target):
            raise SafeError("Use ordinary environment names; Doppler runtime names are reserved.")
        if (source in mappings and mappings[source] != target
                or any(other != source and value == target for other, value in mappings.items())):
            raise SafeError("Secret mappings must have unique source and target names.")
        mappings[source] = target
    if not 1 <= len(mappings) <= 64:
        raise SafeError("Select between 1 and 64 secrets with --require or --map.")
    return mappings


def request(path, payload):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(30)
            connection.connect(path)
            connection.sendall(json.dumps(payload).encode() + b"\n")
            with connection.makefile("rb") as stream:
                raw = stream.readline(LIMIT + 1)
        if len(raw) > LIMIT or not raw.endswith(b"\n"):
            raise ValueError
        reply = json.loads(raw)
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            raise ValueError
        return reply
    except (OSError, ValueError):
        raise SafeError("Doppler request failed; check the local service, selected names, and Doppler access.") from None


def application_environment(mappings, values, environment, clean=False, inherit=()):
    if any(not isinstance(values.get(name), str) or not values[name] or "\0" in values[name] for name in mappings):
        raise SafeError("Requested secrets are missing or invalid; command was not started.")
    result = {key: value for key, value in environment.items()
              if not key.startswith("DOPPLER_") and key != "CREDENTIALS_DIRECTORY"
              and key not in mappings and (not clean or key in inherit)}
    result.update({target: values[source] for source, target in mappings.items()})
    return result


def main():
    args = parser().parse_args()
    if not os.path.isabs(args.socket):
        raise SafeError("The socket path must be absolute.")
    if args.action == "paths":
        return {"ok": True, "socket": args.socket, "credential_storage": "systemd service"}
    if args.action == "doctor":
        request(args.socket, {"operation": "ping"})
        return {"ok": True, "message": "Local service is ready; Doppler authentication was not tested."}
    mappings = selection(args.require, args.map)
    if args.action == "run":
        if args.json or not args.command or args.command[0] != "--" or len(args.command) < 2:
            raise SafeError("run requires -- COMMAND [ARG ...] and does not support --json.")
        if args.inherit and not args.clean_env or any(not valid_name(name) for name in args.inherit):
            raise SafeError("--inherit requires --clean-env and ordinary environment names.")
    reply = request(args.socket, {"operation": "fetch", "names": list(mappings)})
    values = reply.get("values")
    if not isinstance(values, dict):
        raise SafeError("Invalid service response; command was not started.")
    environment = application_environment(mappings, values, os.environ,
                                          getattr(args, "clean_env", False), getattr(args, "inherit", ()))
    if args.action == "check":
        return {"ok": True, "available": list(mappings), "targets": list(mappings.values())}
    try:
        # exec preserves stdin, working directory, umask, exit status, and signals.
        os.execvpe(args.command[1], args.command[1:], environment)
    except FileNotFoundError:
        sys.exit(127)
    except PermissionError:
        sys.exit(126)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except SafeError as error:
        print(json.dumps({"ok": False, "message": str(error)}), file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        print('{"ok":false,"message":"Local secret operation failed; no diagnostics containing values were emitted."}', file=sys.stderr)
        sys.exit(1)
