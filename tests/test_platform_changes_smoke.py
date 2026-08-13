"""Tests for the isolated, non-AI platform changes smoke command."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.models import PlatformChangeWatcherConfig, PlatformChangesConfig
from src import platform_changes_smoke as smoke_module
from src.platform_changes_smoke import run_platform_changes_smoke


def test_smoke_uses_temporary_state_and_preserves_v1_baseline(tmp_path: Path) -> None:
    state_path = tmp_path / "platform_change_state.json"
    original = {
        "version": 1,
        "watchers": {
            "existing-search": {
                "seen_urls": {
                    "https://news.example/seen": "2026-08-12T01:35:00+00:00"
                },
                "last_seen": "2026-08-12T01:35:00+00:00",
            }
        },
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    original_bytes = state_path.read_bytes()
    watcher = PlatformChangeWatcherConfig(
        name="xiaohongshu-public-index",
        mode="xiaohongshu_rules",
        platform="xiaohongshu",
        url="https://school.xiaohongshu.com/api/rules",
        source_level="official",
    )
    config = PlatformChangesConfig(
        enabled=True,
        state_file=str(state_path),
        watchers=[watcher],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "success": True,
                "data": {
                    "dataList": [
                        {
                            "articleId": 119895,
                            "title": "小红书规则更新",
                            "createTime": "2026年08月11日",
                        }
                    ]
                },
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    report = asyncio.run(
        run_platform_changes_smoke(config, require_state=True, client=client)
    )

    assert state_path.read_bytes() == original_bytes
    assert report["mode"] == "platform_changes_smoke"
    assert report["input_state_version"] == 1
    assert report["state_source"] == "existing"
    assert report["production_state_unchanged"] is True
    assert report["ai_called"] is False
    assert report["webhook_sent"] is False
    assert report["detected_item_count"] == 0
    watcher_report = report["watchers"][0]
    assert watcher_report["name"] == "xiaohongshu-public-index"
    assert watcher_report["mode"] == "xiaohongshu_rules"
    assert watcher_report["status"] == "ok"
    assert watcher_report["health_status"] == "baseline"
    assert watcher_report["item_count"] == 0
    assert watcher_report["content_count"] == 1
    assert watcher_report["baseline_created"] is True
    asyncio.run(client.aclose())


def test_smoke_can_require_restored_production_state(tmp_path: Path) -> None:
    config = PlatformChangesConfig(
        enabled=True,
        state_file=str(tmp_path / "missing.json"),
        watchers=[],
    )

    with pytest.raises(FileNotFoundError, match="required platform change state"):
        asyncio.run(run_platform_changes_smoke(config, require_state=True))


def test_smoke_cli_configures_logging_and_prints_json(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text('{"version": 1, "watchers": {}}', encoding="utf-8")
    platform_config = PlatformChangesConfig(
        enabled=True,
        state_file=str(state_path),
        watchers=[],
    )
    logging_calls = []

    class StubStorage:
        def __init__(self, data_dir, config_path):  # type: ignore[no-untyped-def]
            pass

        def load_config(self):  # type: ignore[no-untyped-def]
            return type(
                "Config",
                (),
                {"sources": type("Sources", (), {"platform_changes": platform_config})()},
            )()

    monkeypatch.setattr(smoke_module, "StorageManager", StubStorage)
    monkeypatch.setattr(
        smoke_module,
        "configure_logging",
        lambda console, level: logging_calls.append((console, level)),
    )
    monkeypatch.setattr(
        "sys.argv", ["horizon-platform-changes-smoke", "--require-state"]
    )

    smoke_module.main()

    assert logging_calls and logging_calls[0][1] == "WARNING"
