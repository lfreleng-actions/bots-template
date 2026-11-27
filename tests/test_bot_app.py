# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Authenticating as the App: a short-lived JWT and a read of its record."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
bot_app = import_module("bot_app")
github = import_module("bot_github")

CLIENT_ID = "Iv23liB4s8f6cI7i0oQB"
# Never a real key: the signing is stubbed wherever this is passed.
KEY = "not-a-key-" + "x" * 64


def unpad(part: str) -> bytes:
    """Decode one unpadded base64url segment of a JWT."""
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class FakeReply:
    """What ``urlopen`` returns: a context manager with a body."""

    def __init__(self, body: bytes) -> None:
        """Hold the body the reply will yield."""
        self.body = body

    def __enter__(self) -> FakeReply:
        """Enter the ``with`` block."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave the ``with`` block."""

    def read(self, limit: int = -1) -> bytes:
        """The body, cut to ``limit`` bytes as a real reply would be."""
        return self.body if limit < 0 else self.body[:limit]


class FakeGitHub:
    """Stands in for ``urlopen``, recording each request it receives."""

    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        """Answer every request with ``reply``, or raise ``error``.

        Bytes are sent as they are; anything else as JSON.
        """
        self.reply = reply
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeReply:
        """Record the request, then answer or fail."""
        del timeout
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if isinstance(self.reply, bytes):
            return FakeReply(self.reply)
        return FakeReply(json.dumps(self.reply).encode())


class JwtTest(unittest.TestCase):
    """``app_jwt`` issues an RS256 token the App can authenticate with."""

    def test_claims(self) -> None:
        """RS256, backdated a minute, valid a minute, issued by the client id."""
        signed: list[tuple[bytes, str]] = []

        def fake_sign(data: bytes, key: str) -> bytes:
            signed.append((data, key))
            return b"\x00signature"

        with patch.object(bot_app, "sign_rs256", side_effect=fake_sign):
            token = bot_app.app_jwt(CLIENT_ID, KEY, now=1_000_000)
        header, payload, signature = token.split(".")
        self.assertEqual(json.loads(unpad(header)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(
            json.loads(unpad(payload)),
            {"iat": 999_940, "exp": 1_000_060, "iss": CLIENT_ID},
        )
        self.assertEqual(unpad(signature), b"\x00signature")
        self.assertEqual(signed, [(f"{header}.{payload}".encode(), KEY)])
        self.assertNotIn("=", token)

    @unittest.skipUnless(shutil.which("openssl"), "openssl is not installed")
    def test_signature_verifies_with_the_public_key(self) -> None:
        """openssl signs with the key on stdin, and the signature checks out."""

        def openssl(*args: str, stdin: str | None = None) -> str:
            return subprocess.run(
                ["openssl", *args],
                input=stdin,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout

        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            key = openssl("genrsa", "2048")
            (root / "public.pem").write_text(
                openssl("pkey", "-pubout", stdin=key), encoding="utf-8"
            )
            (root / "data").write_bytes(b"header.payload")
            (root / "signature").write_bytes(bot_app.sign_rs256(b"header.payload", key))
            verified = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(root / "public.pem"),
                    "-signature",
                    str(root / "signature"),
                    str(root / "data"),
                ],
                capture_output=True,
                check=False,
                timeout=30,
            )
        self.assertEqual(verified.returncode, 0)

    def test_signing_failure_names_no_key(self) -> None:
        """A key openssl cannot use fails, and what openssl said is dropped."""
        failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=KEY.encode())
        with (
            patch.object(bot_app.subprocess, "run", return_value=failed),
            self.assertRaises(bot_app.AppError) as caught,
        ):
            bot_app.sign_rs256(b"data", KEY)
        self.assertNotIn(KEY, str(caught.exception))

    def test_missing_openssl_fails(self) -> None:
        """No openssl on the runner is a failure, not a silent skip."""
        with (
            patch.object(bot_app.shutil, "which", return_value=None),
            self.assertRaises(bot_app.AppError),
        ):
            bot_app.sign_rs256(b"data", KEY)


class RecordTest(unittest.TestCase):
    """``read_permissions`` reads ``GET /app`` with the JWT and fails closed."""

    def test_record_is_read_with_the_jwt_as_bearer(self) -> None:
        """GET /app on the REST API, at the pinned version, with the JWT."""
        fake = FakeGitHub({"slug": "x", "permissions": {"contents": "write"}})
        with patch.object(bot_app.urllib.request, "urlopen", side_effect=fake):
            grants = bot_app.read_permissions("a.b.c")
        self.assertEqual(grants, {"contents": "write"})
        (request,) = fake.requests
        self.assertEqual(request.full_url, "https://api.github.com/app")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer a.b.c")
        self.assertEqual(request.get_header("X-github-api-version"), github.API_VERSION)

    def test_failed_or_unreadable_reply_fails_closed(self) -> None:
        """No reply, a refusal, or a reply without permissions is an error."""
        refused = urllib.error.HTTPError(
            "https://api.github.com/app", 401, "Unauthorized", Message(), None
        )
        for name, fake in (
            ("unreachable", FakeGitHub(error=urllib.error.URLError("down"))),
            ("refused", FakeGitHub(error=refused)),
            ("timed out", FakeGitHub(error=TimeoutError())),
            ("not json", FakeGitHub(b"<html>")),
            ("too large", FakeGitHub(b" " * (bot_app.MAX_REPLY_BYTES + 1))),
            ("not an object", FakeGitHub([])),
            ("no permissions", FakeGitHub({"slug": "x"})),
            ("permissions not an object", FakeGitHub({"permissions": []})),
        ):
            with (
                self.subTest(name=name),
                patch.object(bot_app.urllib.request, "urlopen", side_effect=fake),
                self.assertRaises(bot_app.AppError) as caught,
            ):
                bot_app.read_permissions("a.b.c")
            self.assertNotIn("a.b.c", str(caught.exception))

    def test_app_permissions_signs_then_reads(self) -> None:
        """The one entry point a caller needs: credentials in, grants out."""
        fake = FakeGitHub({"permissions": {"issues": "write"}})
        with (
            patch.object(bot_app, "sign_rs256", return_value=b"sig"),
            patch.object(bot_app.urllib.request, "urlopen", side_effect=fake),
        ):
            grants = bot_app.app_permissions(CLIENT_ID, KEY)
        self.assertEqual(grants, {"issues": "write"})
        (request,) = fake.requests
        token = str(request.get_header("Authorization")).removeprefix("Bearer ")
        self.assertEqual(json.loads(unpad(token.split(".")[1]))["iss"], CLIENT_ID)


if __name__ == "__main__":
    unittest.main()
