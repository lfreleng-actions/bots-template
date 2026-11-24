# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation

"""Verify trusted evidence, hold the caps on untrusted session files, read usage.

``verify --directory DIR --expect NAME=SHA256 [--expect ...]`` checks
the exact bytes of each named file against a digest the trusted job
that wrote it published as a job output, never against a value found
in the downloaded artifact. A consumer that downloads evidence by
artifact ID still has to prove the bytes are the ones it was promised;
this is that proof.

``SESSION_FILES`` is the cap table for what an untrusted agent session
may hand to a trusted job: ``session-summary.md`` is required,
``usage.json`` optional, and each has a byte cap that
``artifact_fetch`` enforces while extracting the artifact. A bot that
needs more from its sessions extends this table; nothing else from a
session is read.

``session_usage`` reads the parsed ``usage.json`` into the session's
spend and token usage by model. The file comes from the agent job, so
every figure must be a finite, non-negative number, a count is kept as
an integer, and a model name must look like a model identifier. A
field that fails is left out; nothing raises. A result file is a
claim too, so ``usage_totals`` and ``spend_totals`` check each figure
again as they sum it.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, cast

MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_SUMMARY_BYTES = 8 * 1024 * 1024
MAX_USAGE_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# (name, byte cap, required)
SESSION_FILES: tuple[tuple[str, int, bool], ...] = (
    ("session-summary.md", MAX_SUMMARY_BYTES, True),
    ("usage.json", MAX_USAGE_BYTES, False),
)

Expectation = tuple[str, str, int]

# usage.json as @github/copilot 1.0.105 writes it. A model name must
# look like a model identifier, so a forged file cannot list models
# without bound or put markup in a report.
MODEL_RE = re.compile(r"^[a-z0-9.-]+$")
MAX_MODEL_CHARS = 100
MAX_USAGE_MODELS = 8
USAGE_TOKEN_FIELDS = (
    ("input_tokens", "inputTokens"),
    ("cache_read_tokens", "cacheReadTokens"),
    ("cache_write_tokens", "cacheWriteTokens"),
    ("output_tokens", "outputTokens"),
    ("reasoning_tokens", "reasoningTokens"),
)
# The integer figures of a model's usage record, in table order, with
# their column headings; premium requests sit after requests.
USAGE_COUNTS = (
    ("requests", "Requests"),
    ("input_tokens", "Input tokens"),
    ("cache_read_tokens", "Cache read"),
    ("cache_write_tokens", "Cache write"),
    ("output_tokens", "Output tokens"),
    ("reasoning_tokens", "Reasoning tokens"),
    ("nano_aiu", "Nano AIU"),
)


def read_regular(path: Path, limit: int) -> bytes:
    """Read bounded bytes from a regular file without following a final symlink."""
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # NONBLOCK lets fstat reject a FIFO without waiting for a writer.
        # Check the opened descriptor, not a prior stat that could race.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        with os.fdopen(os.open(path.name, flags, dir_fd=directory_fd), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{path.name} must be a regular non-symlink file")
            if info.st_size > limit:
                raise ValueError(f"{path.name} exceeds the {limit}-byte limit")
            content = source.read(limit + 1)
            if len(content) > limit:
                raise ValueError(f"{path.name} exceeds the {limit}-byte limit")
            return content
    finally:
        os.close(directory_fd)


def verify(directory: Path, expectations: list[Expectation]) -> None:
    """Authenticate evidence files against independently held hashes.

    Each expectation is ``(name, sha256, limit)``. Every digest is
    checked for shape before any file is opened, so a malformed
    expectation fails fast rather than after reading megabytes.
    """
    if not expectations:
        raise ValueError("at least one expectation is required")
    for name, digest, _ in expectations:
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"evidence name {name!r} must be a plain file name")
        if not SHA256_RE.fullmatch(digest):
            raise ValueError(f"trusted SHA-256 for {name} must contain 64 hex digits")
    for name, digest, limit in expectations:
        content = read_regular(directory / name, limit)
        if hashlib.sha256(content).hexdigest() != digest.lower():
            raise ValueError(f"SHA-256 mismatch for {name}")


def parse_expectation(text: str, limit: int) -> Expectation:
    """Split one ``NAME=SHA256`` argument into an expectation."""
    name, separator, digest = text.partition("=")
    if not separator or not name or not digest:
        raise ValueError(f"--expect must be NAME=SHA256, got {text!r}")
    return (name, digest, limit)


def finite_number(value: object) -> float | None:
    """A finite, non-negative number that is not a boolean, or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def usage_count(value: object) -> int | None:
    """A count as an integer, or None when malformed."""
    if finite_number(value) is None:
        return None
    return value if isinstance(value, int) else int(cast("float", value))


def usage_model(value: object) -> bool:
    """Whether a name is a model identifier a usage record may carry."""
    return (
        isinstance(value, str)
        and len(value) <= MAX_MODEL_CHARS
        and MODEL_RE.fullmatch(value) is not None
    )


def put_figure(
    record: dict[str, Any], name: str, value: object, *, count: bool = True
) -> None:
    """Store one usage figure under ``name`` when it passes the check."""
    figure = usage_count(value) if count else finite_number(value)
    if figure is not None:
        record[name] = figure


def object_of(value: object) -> dict[str, Any]:
    """``value`` when it is a JSON object, else an empty one."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def model_record(name: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """One model's figures from usage.json's ``modelMetrics``."""
    requests = object_of(metrics.get("requests"))
    tokens = object_of(metrics.get("usage"))
    record: dict[str, Any] = {"model": name}
    put_figure(record, "requests", requests.get("count"))
    put_figure(record, "premium_requests", requests.get("cost"), count=False)
    for field, key in USAGE_TOKEN_FIELDS:
        put_figure(record, field, tokens.get(key))
    put_figure(record, "nano_aiu", metrics.get("totalNanoAiu"))
    return record


def usage_record(usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """The session's token usage by model, or None when usage.json holds none."""
    if usage is None:
        return None
    record: dict[str, Any] = {}
    put_figure(record, "nano_aiu", usage.get("totalNanoAiu"))
    put_figure(record, "last_call_input_tokens", usage.get("lastCallInputTokens"))
    metrics = object_of(usage.get("modelMetrics"))
    names = sorted(
        name
        for name, value in metrics.items()
        if usage_model(name) and isinstance(value, dict)
    )
    # A model with no accepted figure is left out before the limit, so an
    # empty entry cannot displace one that carries usage.
    records = (model_record(name, object_of(metrics[name])) for name in names)
    models = [record for record in records if len(record) > 1][:MAX_USAGE_MODELS]
    if models:
        record["models"] = models
    return record or None


def usage_numbers(usage: dict[str, Any] | None) -> tuple[float | None, int | None]:
    """Premium request cost and whole agent seconds from usage.json."""
    if usage is None:
        return None, None
    cost = finite_number(usage.get("totalPremiumRequestCost"))
    duration = finite_number(usage.get("totalApiDurationMs"))
    return cost, None if duration is None else int(duration // 1000)


def session_usage(parsed: object) -> dict[str, Any]:
    """``premium_requests``, ``agent_seconds`` and ``usage`` from usage.json.

    ``parsed`` is the decoded file, or None when there is none. Anything
    but a JSON object counts as no file, and no file gives three nulls.
    """
    usage = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
    premium_requests, agent_seconds = usage_numbers(usage)
    return {
        "premium_requests": premium_requests,
        "agent_seconds": agent_seconds,
        "usage": usage_record(usage),
    }


def accepted_figures(row: dict[str, Any]) -> dict[str, int | float]:
    """The figures of one result row that pass the check, by field."""
    figures: dict[str, int | float] = {}
    premium = finite_number(row.get("premium_requests"))
    if premium is not None:
        figures["premium_requests"] = premium
    for field, _ in USAGE_COUNTS:
        value = row.get(field)
        if type(value) is int and finite_number(value) is not None:
            figures[field] = value
    return figures


def model_rows(result: dict[str, Any]) -> list[tuple[str, dict[str, int | float]]]:
    """Each model of a result's usage record with its accepted figures.

    A row with no accepted figure is left out, each name counts once,
    and at most eight rows are read.
    """
    models = object_of(result.get("usage")).get("models")
    if not isinstance(models, list):
        return []
    rows: dict[str, dict[str, int | float]] = {}
    for row in cast("list[Any]", models)[:MAX_USAGE_MODELS]:
        name = object_of(row).get("model")
        if not usage_model(name) or name in rows:
            continue
        figures = accepted_figures(object_of(row))
        if figures:
            rows[cast("str", name)] = figures
    return list(rows.items())


def usage_totals(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Token usage by model: sessions that used it and the sum of each figure.

    An absent or malformed figure adds nothing, and nor does one that
    would carry a total past a finite number.
    """
    totals: dict[str, dict[str, Any]] = {}
    for result in results:
        for name, figures in model_rows(result):
            total = totals.setdefault(
                name,
                {
                    "model": name,
                    "sessions": 0,
                    "premium_requests": 0.0,
                    **{field: 0 for field, _ in USAGE_COUNTS},
                },
            )
            total["sessions"] += 1
            for field, value in figures.items():
                candidate = total[field] + value
                if finite_number(candidate) is not None:
                    total[field] = candidate
    for total in totals.values():
        total["premium_requests"] = round(total["premium_requests"], 2)
    return [totals[name] for name in sorted(totals)]


def spend_totals(results: list[dict[str, Any]]) -> tuple[float, int]:
    """Total premium requests and agent seconds, ignoring absent or odd values."""
    requests = 0.0
    seconds = 0
    for result in results:
        premium = finite_number(result.get("premium_requests"))
        if premium is not None and math.isfinite(requests + premium):
            requests += premium
        agent = result.get("agent_seconds")
        if (
            type(agent) is int
            and finite_number(agent) is not None
            and finite_number(seconds + agent) is not None
        ):
            seconds += agent
    return round(requests, 2), seconds


def main(argv: list[str] | None = None) -> None:
    """Dispatch the verify command."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verification = commands.add_parser("verify", help="check trusted evidence digests")
    verification.add_argument("--directory", type=Path, required=True)
    verification.add_argument(
        "--expect", action="append", default=[], metavar="NAME=SHA256", required=True
    )
    verification.add_argument("--limit-bytes", type=int, default=MAX_EVIDENCE_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.limit_bytes <= 0:
            raise ValueError("--limit-bytes must be positive")
        expected = [parse_expectation(item, args.limit_bytes) for item in args.expect]
        verify(args.directory, expected)
    except (OSError, ValueError) as exc:
        message = ascii(str(exc)).replace("::", ": :").replace("##[", "# #[")
        parser.exit(1, f"evidence: {message}\n")


if __name__ == "__main__":
    main()
