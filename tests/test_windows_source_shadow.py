from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTER_SCRIPT = ROOT / "scripts" / "register_windows_source_shadow.ps1"
RUNNER_SCRIPT = ROOT / "scripts" / "run_local_source_shadow.ps1"


def test_source_shadow_registration_is_safe_by_default() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REGISTER_SCRIPT),
            "-WhatIf",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Pangmen Source Shadow" in result.stdout
    assert "08:30/15:30" in result.stdout
    assert "not registered because -Enable was not provided" in result.stdout


def test_source_shadow_registration_preview_still_does_not_write() -> None:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REGISTER_SCRIPT),
            "-Enable",
            "-WhatIf",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "WOULD REGISTER" in result.stdout
    assert "REGISTERED" not in result.stdout


def test_local_runner_is_source_only() -> None:
    script = RUNNER_SCRIPT.read_text(encoding="utf-8")

    assert "scripts/run_source_shadow.py" in script
    assert "--config" in script
    assert "--hours" in script
    assert "horizon --intelligence-run-mode" not in script
    assert "workflow run" not in script
    assert "webhook" not in script.casefold()
