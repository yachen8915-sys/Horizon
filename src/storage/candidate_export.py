"""Local searchable JSONL and HTML exports for every candidate state."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re

from .._file_utils import _atomic_write_text
from ..models import CandidateRecord


@dataclass(frozen=True)
class CandidateExportResult:
    jsonl_path: Path
    html_path: Path


def export_candidates(
    candidates: list[CandidateRecord],
    output_dir: Path,
    *,
    run_id: str,
) -> CandidateExportResult:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ValueError("run_id contains unsafe filename characters")
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"candidates-{run_id}.jsonl"
    html_path = output_dir / f"candidates-{run_id}.html"
    rows = [candidate.model_dump(mode="json") for candidate in candidates]
    _atomic_write_text(
        jsonl_path,
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
    )
    _atomic_write_text(html_path, _render_html(candidates, run_id))
    return CandidateExportResult(jsonl_path=jsonl_path, html_path=html_path)


def _render_html(candidates: list[CandidateRecord], run_id: str) -> str:
    cards: list[str] = []
    for candidate in candidates:
        analysis = candidate.intelligence
        lane = analysis.primary_lane.value if analysis else "unclassified"
        decision_summary = analysis.decision_summary if analysis else ""
        content_summary = analysis.content_summary if analysis else ""
        reasons = ", ".join(reason.value for reason in candidate.reason_codes)
        search_text = " ".join(
            [
                candidate.item.title,
                candidate.item.author or "",
                candidate.status.value,
                lane,
                reasons,
                decision_summary,
                content_summary,
            ]
        ).lower()
        cards.append(
            f'''<article class="candidate" data-search="{escape(search_text, quote=True)}">
  <div class="meta"><span>{escape(candidate.status.value)}</span><span>{escape(lane)}</span><span>{escape(candidate.evidence_status.value)}</span></div>
  <h2><a href="{escape(str(candidate.item.url), quote=True)}">{escape(candidate.item.title)}</a></h2>
  <p class="decision">{escape(decision_summary)}</p>
  <p>{escape(content_summary)}</p>
  <p class="reason">{escape(reasons)}</p>
</article>'''
        )
    body = "\n".join(cards) or '<p id="empty">No candidates.</p>'
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horizon candidates {escape(run_id)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:980px;margin:40px auto;padding:0 20px;background:#f6f7f9;color:#17202a}}input{{box-sizing:border-box;width:100%;padding:12px 14px;margin:14px 0 24px;border:1px solid #ccd2d9;border-radius:8px}}.candidate{{background:white;padding:18px 20px;margin:12px 0;border-radius:10px;border:1px solid #e4e7eb}}.candidate h2{{font-size:18px;margin:10px 0}}.meta{{display:flex;gap:8px;flex-wrap:wrap}}.meta span{{font-size:12px;background:#eef2ff;padding:3px 8px;border-radius:99px}}.decision{{font-weight:650}}.reason{{color:#68707a;font-size:13px}}a{{color:#2457c5}}
</style>
</head>
<body>
<h1>Horizon 候选档案</h1>
<p>Run: {escape(run_id)} · Candidates: {len(candidates)}</p>
<input id="search" type="search" placeholder="搜索标题、作者、状态、判断分类或原因">
<main>{body}</main>
<script>
const input=document.getElementById('search');
input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();document.querySelectorAll('.candidate').forEach(card=>{{card.hidden=q&&!card.dataset.search.includes(q)}})}});
</script>
</body>
</html>
'''
