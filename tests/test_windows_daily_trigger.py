"""Tests for the local Windows fallback dispatcher."""

import json
import os
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


def test_local_dispatcher_ignores_a_webhook_connectivity_test() -> None:
    result = run_trigger(
        [
            {
                "created_at": "2026-08-08T00:30:00Z",
                "status": "completed",
                "conclusion": "success",
                "display_title": "Daily Horizon Summary (webhook_test)",
            }
        ]
    )

    assert result.returncode == 0
    assert "DISPATCH full daily run" in result.stdout


def test_local_dispatcher_retries_a_transient_github_api_failure(tmp_path: Path) -> None:
    attempt_file = tmp_path / "attempts.txt"
    log_file = tmp_path / "trigger.log"
    fake_gh = tmp_path / "fake-gh.ps1"
    fake_gh.write_text(
        f'''$ErrorActionPreference = "Continue"
$attemptFile = "{attempt_file}"
$attempt = if (Test-Path -LiteralPath $attemptFile) {{
    [int](Get-Content -LiteralPath $attemptFile -Raw)
}} else {{
    0
}}
$attempt += 1
Set-Content -LiteralPath $attemptFile -Value $attempt
if ($attempt -eq 1) {{
    Write-Error "temporary GitHub failure"
    exit 1
}}
Write-Output '{{"workflow_runs":[]}}'
exit 0
''',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-WhatIf",
            "-GhPath",
            str(fake_gh),
            "-MaxAttempts",
            "2",
            "-RetryDelaySeconds",
            "0",
            "-LogPath",
            str(log_file),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert attempt_file.read_text(encoding="utf-8").strip() == "2"
    assert "temporary GitHub failure" in log_file.read_text(encoding="utf-8")


def test_local_dispatcher_runs_without_localappdata_environment_variable() -> None:
    environment = os.environ.copy()
    environment.pop("LOCALAPPDATA", None)

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-WhatIf",
            "-WorkflowRunsJson",
            "[]",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "DISPATCH full daily run" in result.stdout


def test_local_dispatcher_logs_an_unhandled_response_error(tmp_path: Path) -> None:
    log_file = tmp_path / "trigger.log"
    fake_gh = tmp_path / "fake-gh.ps1"
    fake_gh.write_text('Write-Output "not-json"\nexit 0\n', encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-WhatIf",
            "-GhPath",
            str(fake_gh),
            "-LogPath",
            str(log_file),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    log = log_file.read_text(encoding="utf-8")
    assert "FAILED" in log
    assert "ConvertFrom-Json" in log


def test_local_dispatcher_requests_only_compact_workflow_run_fields(tmp_path: Path) -> None:
    fake_gh = tmp_path / "fake-gh.ps1"
    fake_gh.write_text(
        '''if ($args -notcontains "--jq") {
    Write-Output ("x" * 20000)
    exit 0
}
Write-Output '[{"created_at":"2026-08-08T00:30:00Z","status":"completed","conclusion":"success"}]'
exit 0
''',
        encoding="utf-8",
    )

    result = subprocess.run(
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
            "-GhPath",
            str(fake_gh),
            "-LogPath",
            str(tmp_path / "trigger.log"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SKIP successful run already exists" in result.stdout


def test_local_dispatcher_checks_scheduled_and_manual_workflow_runs() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "runs?event=workflow_dispatch" not in script
    assert "actions/workflows/$Workflow/runs?per_page=30" in script


def test_registration_script_builds_0935_fallback_without_writing_task() -> None:
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
    assert "09:35 fallback" in result.stdout
    assert "09:15 and 09:35" not in result.stdout
    assert "including battery power" in result.stdout
    assert "3 task-level restarts" in result.stdout
    assert "persistent log" in result.stdout
