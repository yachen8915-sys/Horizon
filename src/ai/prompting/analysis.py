"""Prompt construction for profile-driven content analysis."""

import json

from ...models import ContentItem
from ...processing.profiles import LoadedProfile
from .common import EVIDENCE_RULES, UNTRUSTED_INPUT_RULE

ANALYSIS_RULES = f"""You are a content curator evaluating an item under the supplied processing profile.

- {UNTRUSTED_INPUT_RULE}
- Base the analysis only on the supplied item and its metadata.
{EVIDENCE_RULES}
- Apply the profile's evaluation policy consistently."""


def analysis_system_prompt(profile: LoadedProfile) -> str:
    if profile.id == "pangmen-platform-trend-radar":
        output_contract = """{
  "score": <same value as operations_score, from 0 to 10>,
  "operations_score": <number from 0 to 10>,
  "content_opportunity_score": <number from 0 to 10>,
  "operations_reason": "<one sentence explaining why operations should care>",
  "reason": "<concise explanation of both scores>",
  "summary": "<one or two sentence factual hotspot brief>",
  "tags": ["<tag>", "..."]
}"""
    elif profile.id == "pangmen-platform-change-radar":
        output_contract = """{
  "score": <number from 0 to 10>,
  "is_platform_change": <true or false>,
  "platform": "douyin|xiaohongshu|bilibili|wechat",
  "change_types": ["operation|ecommerce|feature|rule"],
  "source_level": "official|official_republished|secondary|unverified",
  "affected_audience": ["<specific audience>", "..."],
  "impact_level": "high|medium|low|unknown",
  "change_status": "<launched, grey rollout, announced, effective date, or unconfirmed>",
  "reason": "<concise evidence-based explanation>",
  "summary": "<one or two sentence factual change brief>",
  "tags": ["<tag>", "..."]
}"""
    else:
        output_contract = """{
  "score": <number from 0 to 10>,
  "reason": "<concise explanation>",
  "summary": "<one-sentence summary>",
  "tags": ["<tag>", "..."]
}"""
    return f"""{ANALYSIS_RULES}

# Profile policy

{profile.analysis_prompt}

# Output contract

Return valid JSON only:
{output_contract}"""


def analysis_user_prompt(
    item: ContentItem,
    content_section: str,
    discussion_section: str,
) -> str:
    profile_id = (
        item.processing.classification.profile
        if item.processing
        else item.profile
    )
    metadata_section = ""
    if profile_id == "pangmen-platform-change-radar":
        keys = (
            "platform",
            "change_types",
            "source_level",
            "source_attribution",
            "discovery_mode",
            "watcher",
            "changed_at",
            "article_published_at",
            "actual_change_at",
            "change_time_confidence",
            "diff_excerpt",
        )
        metadata = {key: item.metadata[key] for key in keys if key in item.metadata}
        metadata_section = (
            "\nPlatform change metadata: "
            + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        )
    return f"""Analyze the following content.

Title: {item.title}
Source: {item.source_type.value}
Author: {item.author or "Unknown"}
URL: {item.url}
{metadata_section}
{content_section}
{discussion_section}"""
