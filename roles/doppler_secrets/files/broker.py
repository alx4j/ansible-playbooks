#!/usr/bin/env python3
"""Serve explicitly selected Doppler secrets to one local account."""

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import socket
import struct
import sys
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REQUEST_LIMIT = 16384
RESPONSE_LIMIT = 2 * 1024 * 1024
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
API_URL = "https://api.doppler.com/v3/configs/config/secrets/download"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the bootstrap token to a redirect destination.
        return None


def selected_names(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 64
            or any(not isinstance(name, str) or not NAME.fullmatch(name)
                   or name.startswith("DOPPLER_") for name in value)
            or len(set(value)) != len(value)):
        raise ValueError("Invalid secret selection")
    return value


def fetch_secrets(token, names):
    selected_names(names)
    query = urlencode({"format": "json", "secrets": ",".join(names)})
    request = Request(API_URL + "?" + query, headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
        "User-Agent": "ansible-doppler-secrets/1",
    })
    # Fixed HTTPS destination, normal certificate verification, no ambient proxy.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
        raw = response.read(RESPONSE_LIMIT + 1)
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError("Response too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid response")
    if any(not isinstance(data.get(name), str) or not data[name] or "\0" in data[name] for name in names):
        raise ValueError("Missing or invalid secret")
    # Even an unexpected upstream response must not disclose unrequested values.
    return {name: data[name] for name in names}


def read_request(connection):
    with connection.makefile("rb") as stream:
        raw = stream.readline(REQUEST_LIMIT + 1)
    if len(raw) > REQUEST_LIMIT or not raw.endswith(b"\n"):
        raise ValueError("Invalid request")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("Invalid request")
    return request


def handle(connection, allowed_uid, token, fetch=fetch_secrets):
    connection.settimeout(30)
    try:
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != allowed_uid:
            raise PermissionError("Unauthorized peer")
        request = read_request(connection)
        if request == {"operation": "ping"}:
            reply = {"ok": True}
        elif set(request) == {"operation", "names"} and request["operation"] == "fetch":
            names = selected_names(request["names"])
            reply = {"ok": True, "values": fetch(token, names)}
        else:
            raise ValueError("Invalid operation")
        encoded = json.dumps(reply).encode() + b"\n"
        if len(encoded) > RESPONSE_LIMIT:
            raise ValueError("Response too large")
    except Exception:
        # Exceptions from TLS, HTTP, JSON, and socket libraries may contain secrets.
        encoded = b'{"ok":false,"error":"Secret request failed; check selection, service access, and Doppler availability."}\n'
    try:
        connection.sendall(encoded)
    except OSError:
        pass  # A disconnected caller must not terminate the service.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-user", required=True)
    args = parser.parse_args()
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        raise ValueError("Start through the systemd socket unit")
    token = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "doppler_token").read_text()
    if not token or len(token) > 8192 or any(character.isspace() for character in token):
        raise ValueError("Invalid service credential")
    uid = pwd.getpwnam(args.client_user).pw_uid
    with socket.socket(fileno=3) as listener:
        while True:
            connection, _ = listener.accept()
            with connection:
                handle(connection, uid, token)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.exit(f"Doppler service stopped ({type(error).__name__}); check its credential, account, and socket unit.")
