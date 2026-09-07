"""Offline entry point and archived-policy replay contracts."""

import builtins
import io
import json
from pathlib import Path
import runpy
import socket
import subprocess
import sys

import pytest

from src.models import CandidateStatus, EvidenceStatus, IntelligenceRadarConfig, ReasonCode
from tests.test_intelligence_selection import _trend


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/replay_intelligence_selection.py"


def archive(tmp_path, rows, *, suffix="jsonl", diagnostic=None):
    root = tmp_path / "random-extraction" / "nested" / "runs"
    root.mkdir(parents=True)
    payloads = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in rows]
    path = root / f"candidates-run-fixture.{suffix}"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in payloads)
        if suffix == "jsonl" else json.dumps(payloads), encoding="utf-8",
    )
    (root / "diagnostics-run-fixture.json").write_text(json.dumps(diagnostic or {
        "source_health": [
            {"source_id": "platform-trends:weibo:dailyhotapi", "status": "failed"},
            {"source_id": "platform-trends:douyin:alapi", "status": "healthy"},
        ]}), encoding="utf-8")
    return tmp_path / "random-extraction"


def test_python_entry_point_runs_real_policy_without_external_side_effects(tmp_path, monkeypatch):
    root = archive(tmp_path, [_trend("legacy-trend", evidence=EvidenceStatus.UNVERIFIED)])
    original = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    output = tmp_path / "replay.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--input", str(root), "--output", str(output)])
    original_import = builtins.__import__
    original_open = builtins.open
    original_io_open = io.open

    def guarded_open(opener):
        def open_file(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in "wax+"):
                assert Path(file).resolve() == output.resolve(), "Unexpected write, including configured ledgers"
            return opener(file, mode, *args, **kwargs)
        return open_file

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith(("src.scrapers", "src.services", "src.ai", "src.orchestrator"))
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline replay attempted network or subprocess execution")

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", guarded_open(original_open))
    monkeypatch.setattr(io, "open", guarded_open(original_io_open))
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert SCRIPT.is_file(), "Replay Python entry point has not been implemented"
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert result.value.code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["selected_titles"] == ["legacy-trend"]
    assert report["presentation_categories"]["hot_leverage"] == ["legacy-trend"]
    assert report["coverage_notice"] == "热点覆盖不完整：微博来源暂时不可用，抖音来源仍正常。"
    assert report["incompatible_records"] == []
    assert report["diagnostics"]["ai_usage"]["calls"] == 0
    assert report["effective_config"]["delivery_enabled"] is False
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == original
    assert set(p for p in tmp_path.rglob("*") if p.is_file()) == {*original, output}


def test_replay_preserves_overflow_and_exclusions_without_mutating_config(tmp_path):
    from scripts.replay_intelligence_selection import replay_archive

    rows = [_trend(f"hot-{i:02}", evidence=EvidenceStatus.UNVERIFIED) for i in range(17)]
    watch = _trend("watch", operations=8, content=5, rank=1)
    duplicate = rows[0].model_copy(update={"candidate_id": "duplicate"}, deep=True)
    duplicate.item.id = "duplicate"
    duplicate.item.title = "duplicate"
    duplicate.item.metadata["rank"] = 11
    expired = _trend("expired").model_copy(update={"status": CandidateStatus.EXPIRED, "reason_codes": [ReasonCode.EXPIRED]})
    immature = _trend("immature")
    immature.item.metadata["engagement_gate_status"] = "observing"
    unsafe = _trend("台风灾难")
    low = _trend("low", operations=6, content=8)
    root = archive(tmp_path, [*rows, watch, duplicate, expired, immature, unsafe, low])
    config = IntelligenceRadarConfig(delivery_enabled=True, run_mode="morning", canary_mode=True)
    before = config.model_dump()
    result = replay_archive(root, config=config)
    assert config.model_dump() == before
    assert result["effective_config"]["delivery_enabled"] is False
    assert result["effective_config"]["canary_mode"] is False
    assert result["effective_config"]["run_mode"] == "shadow"
    assert result["counts"]["selected"] == 15
    assert len(result["presentation_categories"]["more_hot"]) == 3
    assert result["trend_pools"]["watch"] == ["watch"]
    assert result["reject_reasons"]["duplicate"] == ["duplicate"]
    assert result["reject_reasons"]["expired"] == ["expired"]
    assert "immature" not in result["selected_titles"]
    assert result["reject_reasons"]["台风灾难"] == ["brand_safety"]
    assert result["reject_reasons"]["low"] == ["low_operations_value"]
    assert result == replay_archive(root, config=config)


@pytest.mark.parametrize("suffix", ["json", "jsonl"])
def test_optional_legacy_fields_and_incompatible_records_are_explicit(tmp_path, suffix):
    from scripts.replay_intelligence_selection import replay_archive

    legacy = _trend("legacy").model_dump(mode="json")
    legacy["item"]["processing"]["analysis"].pop("operations_focus", None)
    root = archive(tmp_path, [legacy, {"candidate_id": "broken"}], suffix=suffix)
    result = replay_archive(root, config=IntelligenceRadarConfig())
    assert result["selected_titles"] == ["legacy"]
    assert len(result["incompatible_records"]) == 1
    assert result["incompatible_records"][0]["candidate_id"] == "broken"
    assert result["counts"]["archived"] == 2


@pytest.mark.parametrize("kind", ["missing", "invalid_json", "invalid_shape", "invalid_diagnostic"])
def test_cli_malformed_input_exits_nonzero_without_report(tmp_path, kind):
    root = archive(tmp_path, [_trend("hot")])
    if kind == "missing":
        root = tmp_path / "missing"
    elif kind == "invalid_diagnostic":
        next(root.rglob("diagnostics-*.json")).write_text("[]", encoding="utf-8")
    else:
        next(root.rglob("*.jsonl")).write_text("{" if kind == "invalid_json" else "[]", encoding="utf-8")
    output = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(root), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Replay failed:" in result.stderr
    assert not output.exists()


def test_cli_refuses_output_inside_archive(tmp_path):
    root = archive(tmp_path, [_trend("hot")])
    output = next(root.rglob("*.jsonl"))
    before = output.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(root), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Replay failed:" in result.stderr
    assert output.read_bytes() == before


@pytest.mark.parametrize("diagnostic", [
    {"fetch_report": None},
    {"fetch_report": {"sources": ["broken-source"]}},
    *[
        {"fetch_report": {"sources": [{"source": "Platform Trends", key: value}]}}
        for key in ("providers", "source_health", "health", "feeds", "watchers")
        for value in ({}, ["broken-row"], None)
    ],
    {"source_health": {}},
    {"source_health": ["broken-row"]},
])
def test_main_rejects_malformed_diagnostic_containers_without_output(tmp_path, capsys, diagnostic):
    from scripts.replay_intelligence_selection import main

    root = archive(tmp_path, [_trend("hot")], diagnostic=diagnostic)
    output = tmp_path / "report.json"
    assert main(["--input", str(root), "--output", str(output)]) == 2
    assert "Replay failed: Malformed diagnostic" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("diagnostic", [
    {"fetch_report": {}},
    {"fetch_report": {"sources": [{}]}},
    {"fetch_report": {"sources": [{"source": "Platform Trends", "providers": [], "health": [{}]}]}},
    {"source_health": [{}]},
    {"run_id": "legacy-no-health"},
])
def test_main_accepts_missing_optional_diagnostic_fields(tmp_path, diagnostic):
    from scripts.replay_intelligence_selection import main

    root = archive(tmp_path, [_trend("hot")], diagnostic=diagnostic)
    output = tmp_path / "report.json"
    assert main(["--input", str(root), "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["selected_titles"] == ["hot"]
