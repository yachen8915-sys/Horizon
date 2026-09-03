"""Build a transparent three-day review surface from legacy diagnostics."""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path


SECONDARY_TYPES = {"aihot", "google_news", "platform_trends"}
SENSITIVE_TERMS = {
    "政治",
    "军事",
    "战争",
    "伤亡",
    "遇难",
    "灾难",
    "持刀",
    "驱逐",
    "politic",
    "military",
    "war",
    "death",
    "killed",
}


def build_review(root: Path) -> dict[str, object]:
    days: list[dict[str, object]] = []
    all_rows: list[dict] = []
    for path in sorted(root.rglob("selection-*.json")) + sorted(
        root.rglob("selection.json")
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in payload.get("selected_items", [])
            if isinstance(row, dict)
        ]
        if not rows:
            continue
        date = path.parent.name
        sources = Counter(
            str(row.get("sub_source") or row.get("source_type") or "unknown")
            for row in rows
        )
        weak = sum(
            str(row.get("source_type") or "") in SECONDARY_TYPES
            for row in rows
        )
        sensitive = sum(_is_sensitive(row) for row in rows)
        top_source, top_count = sources.most_common(1)[0]
        days.append(
            {
                "date": date,
                "selected_count": len(rows),
                "secondary_or_aggregator_count": weak,
                "sensitive_topic_count": sensitive,
                "top_source": top_source,
                "top_source_share": top_count / len(rows),
            }
        )
        for row in rows:
            all_rows.append(
                {
                    **row,
                    "review_date": date,
                    "evidence_review": (
                        "needs_primary_source"
                        if str(row.get("source_type") or "")
                        in SECONDARY_TYPES
                        else "source_not_flagged"
                    ),
                    "sensitive_topic": _is_sensitive(row),
                    "human_verdict": "pending",
                }
            )

    event_counts = Counter(
        str(row.get("event_key"))
        for row in all_rows
        if row.get("event_key")
    )
    repeated_events = {
        key: count for key, count in sorted(event_counts.items()) if count > 1
    }
    return {
        "mode": "historical_diagnostics_review",
        "human_review_required": True,
        "limitations": [
            "Legacy diagnostics do not contain the new eight-dimensional scores.",
            "This report flags review risks; it does not claim a new AI verdict.",
        ],
        "days": days,
        "selected_count": len(all_rows),
        "repeated_events": repeated_events,
        "items": all_rows,
    }


def _is_sensitive(row: dict) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("title", "analysis_reason", "category")
    ).casefold()
    return any(term in text for term in SENSITIVE_TERMS)


def write_review(report: dict[str, object], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "editorial-shadow-review.json"
    html_path = output / "editorial-shadow-review.html"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = []
    for item in report.get("items", []):
        if not isinstance(item, dict):
            continue
        flags = []
        if item.get("evidence_review") == "needs_primary_source":
            flags.append("需回溯一手来源")
        if item.get("sensitive_topic"):
            flags.append("敏感话题")
        rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('review_date') or ''))}</td>"
            f"<td><a href=\"{escape(str(item.get('url') or ''), quote=True)}\">{escape(str(item.get('title') or ''))}</a></td>"
            f"<td>{escape(str(item.get('sub_source') or item.get('source_type') or ''))}</td>"
            f"<td>{escape('、'.join(flags) or '—')}</td>"
            "<td>待人工判断</td>"
            "</tr>"
        )
    html_path.write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Horizon 三日离线评审</title><style>body{font-family:system-ui;max-width:1200px;margin:32px auto;padding:0 18px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}th{background:#f4f6f8}</style></head><body>"
        "<h1>Horizon 三日离线评审</h1><p>基于历史 diagnostics，仅用于发现证据、集中度和敏感话题风险；最终价值判断仍需人工完成。</p>"
        "<table><thead><tr><th>日期</th><th>标题</th><th>来源</th><th>规则提示</th><th>人工结论</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>\n",
        encoding="utf-8",
    )
    return json_path, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_review(args.input)
    paths = write_review(report, args.output)
    print(json.dumps({"json": str(paths[0]), "html": str(paths[1])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
