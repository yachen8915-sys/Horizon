"""Tests for the local Windows dispatcher that backs up GitHub cron."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "trigger_horizon_canary.ps1"
REGISTER_SCRIPT = ROOT / "scripts" / "register_windows_canary_trigger.ps1"


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
            "2026-09-05T23:30:00Z",
            "-WorkflowRunsJson",
            json.dumps(workflow_runs),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_dispatches_full_canary_when_no_run_exists() -> None:
    result = run_trigger([])

    assert result.returncode == 0
    assert "DISPATCH full canary run" in result.stdout


def test_skips_when_today_has_a_successful_full_run() -> None:
    result = run_trigger(
        [
            {
                "created_at": "2026-09-05T23:10:00Z",
                "status": "completed",
                "conclusion": "success",
                "display_title": "Horizon Feishu Canary (full)",
            }
        ]
    )

    assert result.returncode == 0
    assert "SKIP successful full run already exists" in result.stdout


def test_ignores_webhook_connectivity_test() -> None:
    result = run_trigger(
        [
            {
                "created_at": "2026-09-05T23:10:00Z",
                "status": "completed",
                "conclusion": "success",
                "display_title": "Horizon Feishu Canary (webhook_test)",
            }
        ]
    )

    assert result.returncode == 0
    assert "DISPATCH full canary run" in result.stdout


def test_transition_period_treats_legacy_active_dispatch_as_full() -> None:
    result = run_trigger(
        [
            {
                "created_at": "2026-09-05T23:10:00Z",
                "status": "in_progress",
                "conclusion": None,
                "display_title": "Horizon Feishu Canary (workflow_dispatch)",
            }
        ]
    )

    assert result.returncode == 0
    assert "SKIP active full run already exists" in result.stdout


def test_registration_builds_0730_wake_enabled_task_without_writing() -> None:
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
    assert "Horizon Canary Dispatcher" in result.stdout
    assert "07:30" in result.stdout
    assert "WakeToRun" in result.stdout
