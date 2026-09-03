"""Searchable raw-item snapshots for source-only shadow validation."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re

from .._file_utils import _atomic_write_text
from ..models import ContentItem


@dataclass(frozen=True)
class SourceShadowExportResult:
    jsonl_path: Path
    html_path: Path


def export_source_shadow_items(
    grouped_items: list[tuple[str, ContentItem]],
    output_dir: Path,
    *,
    run_id: str,
) -> SourceShadowExportResult:
    """Persist raw source results without invoking AI or the production ledger."""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        raise ValueError("run_id contains unsafe filename characters")
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"source-items-{run_id}.jsonl"
    html_path = output_dir / f"source-items-{run_id}.html"
    rows = [
        {
            "run_id": run_id,
            "source_group": source_group,
            "item": item.model_dump(mode="json"),
        }
        for source_group, item in grouped_items
    ]
    _atomic_write_text(
        jsonl_path,
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
    )
    _atomic_write_text(html_path, _render_html(grouped_items, run_id))
    return SourceShadowExportResult(jsonl_path=jsonl_path, html_path=html_path)


def _render_html(
    grouped_items: list[tuple[str, ContentItem]],
    run_id: str,
) -> str:
    cards: list[str] = []
    for source_group, item in sorted(
        grouped_items,
        key=lambda row: row[1].published_at,
        reverse=True,
    ):
        engagement = item.metadata.get("engagement") or {}
        access = str(item.metadata.get("source_access") or "")
        category = str(item.metadata.get("category") or "")
        gate_status = str(
            item.metadata.get("engagement_gate_status") or "unclassified"
        )
        gate_reason = str(item.metadata.get("engagement_gate_reason") or "")
        search_text = " ".join(
            [
                source_group,
                item.source_type.value,
                item.title,
                item.author or "",
                category,
                access,
                gate_status,
                gate_reason,
            ]
        ).lower()
        engagement_text = ", ".join(
            f"{key}: {value}"
            for key, value in engagement.items()
            if value is not None
        )
        cards.append(
            f'''<article class="item" data-search="{escape(search_text, quote=True)}">
  <div class="meta"><span>{escape(source_group)}</span><span>{escape(item.source_type.value)}</span><span>{escape(category)}</span><span>{escape(gate_status)}</span><span>{escape(gate_reason)}</span></div>
  <h2><a href="{escape(str(item.url), quote=True)}">{escape(item.title)}</a></h2>
  <p>{escape(item.author or "Unknown author")} · {escape(item.published_at.isoformat())}</p>
  <p>{escape(engagement_text or "No engagement metrics")} · {escape(access or "default access")}</p>
</article>'''
        )
    body = "\n".join(cards) or '<p id="empty">No source items.</p>'
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horizon source shadow {escape(run_id)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1040px;margin:40px auto;padding:0 20px;background:#f6f7f9;color:#17202a}}input{{box-sizing:border-box;width:100%;padding:12px 14px;margin:14px 0 24px;border:1px solid #ccd2d9;border-radius:8px}}.item{{background:white;padding:16px 20px;margin:12px 0;border-radius:10px;border:1px solid #e4e7eb}}.item h2{{font-size:18px;margin:10px 0}}.meta{{display:flex;gap:8px;flex-wrap:wrap}}.meta span{{font-size:12px;background:#eef2ff;padding:3px 8px;border-radius:99px}}a{{color:#2457c5}}
</style>
</head>
<body>
<h1>Horizon 来源影子候选</h1>
<p>Run: {escape(run_id)} · Items: {len(grouped_items)} · 未调用 AI</p>
<input id="search" type="search" placeholder="搜索标题、作者、来源、分类或访问方式">
<main>{body}</main>
<script>
const input=document.getElementById('search');
input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();document.querySelectorAll('.item').forEach(card=>{{card.hidden=q&&!card.dataset.search.includes(q)}})}});
</script>
</body>
</html>
'''
