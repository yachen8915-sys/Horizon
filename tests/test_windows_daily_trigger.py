"""Tests for the local Windows fallback dispatcher."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trigger_daily_horizon.ps1"
REGISTER_SCRIPT = ROOT / "scripts" / "register_windows_daily_trigger.ps1"


def run_trigger(workflow_runs: list[dict]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-WhatIf",
            "-NowUtc",
            "2026-08-08T01:15:00Z",
            "-WorkflowRunsJson",
            json.dumps(workflow_runs),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_local_dispatcher_starts_full_run_when_no_run_exists() -> None:
    result = run_trigger([])

    assert result.returncode == 0
    assert "DISPATCH full daily run" in result.stdout


def test_local_dispatcher_skips_when_today_has_a_successful_run() -> None:
    result = run_trigger(
        [{"created_at": "2026-08-08T00:30:00Z", "status": "completed", "conclusion": "success"}]
    )

    assert result.returncode == 0
    assert "SKIP successful run already exists" in result.stdout


def test_local_dispatcher_retries_after_a_failed_run() -> None:
    result = run_trigger(
        [{"created_at": "2026-08-08T00:30:00Z", "status": "completed", "conclusion": "failure"}]
    )

    assert result.returncode == 0
    assert "DISPATCH full daily run" in result.stdout


def test_registration_script_builds_both_daily_triggers_without_writing_task() -> None:
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

    assert result.returncode == 0
    assert "09:15 and 09:35" in result.stdout
    assert "including battery power" in result.stdout
