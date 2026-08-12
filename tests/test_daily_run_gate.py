"""Regression tests for the GitHub scheduled-run duplicate gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_daily_run_gate.py"


def run_gate(tmp_path: Path, runs: list[dict]) -> tuple[subprocess.CompletedProcess[str], str]:
    output = tmp_path / "github-output.txt"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runs-json",
            json.dumps({"workflow_runs": runs}),
            "--current-run-id",
            "200",
            "--now-utc",
            "2026-08-12T03:29:16Z",
            "--github-output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output.read_text(encoding="utf-8") if output.exists() else ""


def test_delayed_schedule_skips_after_fallback_succeeded_that_beijing_day(
    tmp_path: Path,
) -> None:
    result, output = run_gate(
        tmp_path,
        [
            {
                "id": 100,
                "created_at": "2026-08-12T01:35:07Z",
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "display_title": "Daily Horizon Summary (full)",
            },
            {
                "id": 200,
                "created_at": "2026-08-12T03:29:16Z",
                "status": "in_progress",
                "conclusion": None,
                "event": "schedule",
                "display_title": "Daily Horizon Summary (scheduled)",
            },
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "SKIP prior successful daily run 100" in result.stdout
    assert "should_run=false" in output
    assert "prior_run_id=100" in output


def test_schedule_runs_when_only_non_daily_or_previous_beijing_day_runs_exist(
    tmp_path: Path,
) -> None:
    result, output = run_gate(
        tmp_path,
        [
            {
                "id": 90,
                "created_at": "2026-08-11T15:59:59Z",
                "status": "completed",
                "conclusion": "success",
                "event": "schedule",
                "display_title": "Daily Horizon Summary (scheduled)",
            },
            {
                "id": 91,
                "created_at": "2026-08-12T01:00:00Z",
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "display_title": "Daily Horizon Summary (webhook_test)",
            },
            {
                "id": 92,
                "created_at": "2026-08-12T01:10:00Z",
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "display_title": "Daily Horizon Summary (platform_changes_smoke)",
            },
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "RUN no prior successful daily run" in result.stdout
    assert "should_run=true" in output
