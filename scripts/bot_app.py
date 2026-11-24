# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Authenticate as the GitHub App itself and read its own record.

Every bot repository carries this file verbatim. An installation
token speaks for one installation; the App's own record, with the
permissions it holds on every installation at once, needs a JWT that
the App's private key signs. ``preflight.py app-permissions`` reads
it through :func:`app_permissions`.

The key reaches ``openssl`` on its standard input and never a file or
a command line. The JWT lives a minute and leaves this module only in
the one request's ``Authorization`` header. No error message carries
either, nor anything ``openssl`` printed.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

import bot_github as github

APP_URL = "https://api.github.com/app"
# GitHub accepts an App JWT issued up to 60 seconds in the past, which
# allows for a runner clock running ahead; this one lapses a minute
# after it is made.
JWT_SKEW = 60
JWT_LIFETIME = 60
MAX_REPLY_BYTES = 256 * 1024
OPENSSL_TIMEOUT = 30


class AppError(Exception):
    """The App's record could not be read; its message names no secret."""


def b64url(data: bytes) -> str:
    """Unpadded base64url, as a JWT segment is written."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_rs256(data: bytes, private_key: str) -> bytes:
    """An RS256 signature of ``data`` made by openssl, which every runner has.

    Nothing openssl prints is reported: a message about the key could
    quote it.
    """
    binary = shutil.which("openssl")
    if binary is None:
        raise AppError("openssl is not installed on the runner")
    with tempfile.TemporaryDirectory() as holder:
        signing_input = Path(holder) / "signing-input"
        signing_input.write_bytes(data)
        proc = subprocess.run(
            [binary, "dgst", "-sha256", "-sign", "/dev/stdin", str(signing_input)],
            input=private_key.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=OPENSSL_TIMEOUT,
        )
    if proc.returncode != 0 or not proc.stdout:
        raise AppError(f"openssl could not sign the App JWT (exit {proc.returncode})")
    return proc.stdout


def app_jwt(client_id: str, private_key: str, now: int | None = None) -> str:
    """A JWT that authenticates as the App, issued by its client id."""
    issued = int(time.time()) if now is None else now
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = {"iat": issued - JWT_SKEW, "exp": issued + JWT_LIFETIME, "iss": client_id}
    signing_input = f"{header}.{b64url(json.dumps(claims).encode())}"
    signature = sign_rs256(signing_input.encode("ascii"), private_key)
    return f"{signing_input}.{b64url(signature)}"


def read_permissions(jwt: str) -> dict[str, Any]:
    """The ``permissions`` of ``GET /app``, read with the App's JWT.

    gh sends a token under the ``token`` scheme and would need the JWT
    on its command line to send it as ``Bearer``, so this one request
    goes through the standard library. Any failure raises.
    """
    request = urllib.request.Request(
        APP_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt}",
            "X-GitHub-Api-Version": github.API_VERSION,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=github.TIMEOUT_SECONDS) as reply:
            body = reply.read(MAX_REPLY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AppError(f"GET /app answered HTTP {exc.code}") from None
    except OSError as exc:
        raise AppError(f"GET /app failed ({type(exc).__name__})") from None
    if len(body) > MAX_REPLY_BYTES:
        raise AppError("the App record is implausibly large")
    try:
        record: Any = json.loads(body)
    except (ValueError, RecursionError):
        raise AppError("the App record is not JSON") from None
    if not isinstance(record, dict):
        raise AppError("the App record is not an object")
    grants = cast("dict[str, Any]", record).get("permissions")
    if not isinstance(grants, dict):
        raise AppError("the App record carries no permissions")
    return cast("dict[str, Any]", grants)


def app_permissions(client_id: str, private_key: str) -> dict[str, Any]:
    """The permissions the App holds, read with a JWT signed for this call."""
    return read_permissions(app_jwt(client_id, private_key))
