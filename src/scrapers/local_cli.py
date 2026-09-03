"""Bounded subprocess helpers for optional local-only source fallbacks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import shutil
import subprocess
from typing import Any


@dataclass(frozen=True)
class LocalCommandResult:
    returncode: int
    stdout: str
    stderr: str


async def run_local_command(
    args: list[str],
    timeout_sec: float,
) -> LocalCommandResult:
    """Run an argument-vector command without shell interpolation."""

    def _run() -> LocalCommandResult:
        executable = shutil.which(args[0]) or args[0]
        resolved_args = [executable, *args[1:]]
        completed = subprocess.run(
            resolved_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        return LocalCommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    return await asyncio.to_thread(_run)


def decode_first_json(text: str) -> Any:
    """Decode the first JSON value while tolerating CLI notices after it."""

    decoder = json.JSONDecoder()
    for marker in ("[", "{"):
        offset = text.find(marker)
        if offset < 0:
            continue
        try:
            value, _ = decoder.raw_decode(text[offset:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("local CLI output did not contain valid JSON")


def decode_json_lines(text: str) -> list[dict[str, Any]]:
    """Decode one compact JSON object per output line."""

    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
