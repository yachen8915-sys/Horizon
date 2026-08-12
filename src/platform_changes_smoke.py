"""Isolated platform-change source smoke check with read-only production state."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from rich.console import Console

from ._cli import add_data_dir_arguments, add_log_level_argument
from .logging_config import configure_logging
from .models import PlatformChangesConfig
from .scrapers.platform_changes import PlatformChangesScraper
from .storage.manager import ConfigError, StorageManager


console = Console(stderr=True)


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_state_version(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("watchers"), dict):
        raise ValueError("platform change state has an invalid root")
    version = payload.get("version", 1)
    if not isinstance(version, int):
        raise ValueError("platform change state version must be an integer")
    return version


async def run_platform_changes_smoke(
    platform_config: PlatformChangesConfig,
    *,
    require_state: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    """Run only platform-change watchers against a temporary state copy."""
    production_state = Path(platform_config.state_file)
    if require_state and not production_state.exists():
        raise FileNotFoundError(
            f"required platform change state was not restored: {production_state}"
        )

    before_digest = _file_digest(production_state)
    input_state_version = (
        _read_state_version(production_state) if production_state.exists() else None
    )
    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 Horizon platform changes smoke"},
    )

    try:
        with tempfile.TemporaryDirectory(prefix="horizon-platform-changes-smoke-") as tmp:
            smoke_state = Path(tmp) / "platform_change_state.json"
            if production_state.exists():
                shutil.copy2(production_state, smoke_state)
            smoke_config = platform_config.model_copy(
                update={"state_file": str(smoke_state)}
            )
            scraper = PlatformChangesScraper(smoke_config, active_client)
            since = datetime.now(timezone.utc) - timedelta(
                days=platform_config.lookback_days
            )
            items = await scraper.fetch(since)
            output_state_version = (
                _read_state_version(smoke_state) if smoke_state.exists() else None
            )
    finally:
        if owns_client:
            await active_client.aclose()

    after_digest = _file_digest(production_state)
    return {
        "mode": "platform_changes_smoke",
        "state_source": "existing" if before_digest is not None else "empty",
        "input_state_version": input_state_version,
        "output_state_version": output_state_version,
        "production_state_unchanged": before_digest == after_digest,
        "watcher_count": len(platform_config.watchers),
        "watchers": scraper.last_watcher_results,
        "detected_item_count": len(items),
        "detected_items": [
            {"watcher": item.metadata.get("watcher"), "title": item.title}
            for item in items
        ],
        "ai_called": False,
        "webhook_sent": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run only platform-change watchers without AI or webhook delivery"
    )
    parser.add_argument(
        "--require-state",
        action="store_true",
        help="Fail unless an existing platform change state was restored",
    )
    add_data_dir_arguments(parser)
    add_log_level_argument(parser)
    args = parser.parse_args()
    configure_logging(console, level=args.log_level)
    load_dotenv()

    try:
        storage = StorageManager(data_dir=args.data_dir, config_path=args.config)
        config = storage.load_config()
        report = asyncio.run(
            run_platform_changes_smoke(
                config.sources.platform_changes,
                require_state=args.require_state,
            )
        )
    except (ConfigError, FileNotFoundError, ValueError) as exc:
        print(f"platform_changes_smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
