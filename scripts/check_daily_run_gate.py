"""Prevent a delayed scheduled workflow from sending a second daily digest."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


BEIJING = timezone(timedelta(hours=8))
NON_DAILY_TITLES = ("(webhook_test)", "(platform_changes_smoke)")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _prior_successful_daily_run(
    runs: list[dict[str, Any]],
    *,
    current_run_id: int,
    now_utc: datetime,
) -> dict[str, Any] | None:
    today = now_utc.astimezone(BEIJING).date()
    for run in runs:
        if int(run.get("id") or 0) == current_run_id:
            continue
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            continue
        if run.get("event") not in {"schedule", "workflow_dispatch"}:
            continue
        title = str(run.get("display_title") or "")
        if title.endswith(NON_DAILY_TITLES):
            continue
        created_at = str(run.get("created_at") or "")
        if not created_at or _parse_utc(created_at).astimezone(BEIJING).date() != today:
            continue
        return run
    return None


def _fetch_workflow_runs(token: str, repository: str) -> list[dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
        "daily-summary.yml/runs?per_page=100"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Horizon-daily-run-gate",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API host
        payload = json.load(response)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise ValueError("GitHub workflow runs response did not contain workflow_runs")
    return [run for run in runs if isinstance(run, dict)]


def _write_outputs(path: Path, *, should_run: bool, prior_run_id: str = "") -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"should_run={'true' if should_run else 'false'}\n")
        output.write(f"prior_run_id={prior_run_id}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-json")
    parser.add_argument("--current-run-id", type=int, default=None)
    parser.add_argument("--now-utc")
    parser.add_argument("--github-output")
    args = parser.parse_args()

    current_run_id = args.current_run_id or int(os.environ["GITHUB_RUN_ID"])
    now_utc = (
        _parse_utc(args.now_utc)
        if args.now_utc
        else datetime.now(timezone.utc)
    )
    if args.runs_json:
        payload = json.loads(args.runs_json)
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise ValueError("--runs-json must contain workflow_runs")
    else:
        runs = _fetch_workflow_runs(
            os.environ["GITHUB_TOKEN"],
            os.environ["GITHUB_REPOSITORY"],
        )

    prior = _prior_successful_daily_run(
        runs,
        current_run_id=current_run_id,
        now_utc=now_utc,
    )
    output_path = Path(args.github_output or os.environ["GITHUB_OUTPUT"])
    if prior is not None:
        prior_id = str(prior.get("id") or "")
        _write_outputs(output_path, should_run=False, prior_run_id=prior_id)
        print(f"SKIP prior successful daily run {prior_id} already exists")
        return

    _write_outputs(output_path, should_run=True)
    print("RUN no prior successful daily run exists for this Beijing date")


if __name__ == "__main__":
    main()
