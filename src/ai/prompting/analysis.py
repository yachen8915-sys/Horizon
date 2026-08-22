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
    editorial_guidance = ""
    if profile.id == "pangmen-platform-trend-radar":
        editorial_guidance = """
Do not reject a topic only because it is entertainment, sports, celebrity, or
short-lived public curiosity. Judge the topic type as a label, then assess its
actual heat and whether there is a natural, evidence-backed content extension
for Pangmen. Pure AI product/model news belongs in the AI profiles, but a public
topic involving consumer behavior, content spread, brands, platforms, social
sentiment, or ordinary people's decisions may remain here. Provide up to three
specific extension_angles; do not invent an angle when the supplied evidence is
only a bare title. Keep operations_score, content_opportunity_score, and
evidence_quality_score independent. The program applies the final gates.
"""
        output_contract = """{
  "score": <same value as operations_score, from 0 to 10>,
  "operations_score": <number from 0 to 10>,
  "content_opportunity_score": <number from 0 to 10>,
  "operations_reason": "<one sentence explaining why operations should care>",
  "extension_score": <derived 0 to 10 score for the natural Pangmen content extension>,
  "extension_angles": ["<up to three specific, evidence-backed angles>"],
  "extension_reason": "<one sentence explaining why the extension is or is not natural>",
  "evidence_quality_score": <number from 0 to 10>,
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
    elif profile.id in {"pangmen-topic-radar", "pangmen-ai-tech-radar"}:
        editorial_guidance = """
For the editorial fields, use body evidence from the supplied content and metadata,
not the title alone. Do not treat different releases, features, examples, or tutorials
as the same event merely because they mention the same tool. Use concise canonical
snake_case identifiers. event_key must identify the exact factual release, update,
announcement, test, tutorial, or case so that the same event can match across sources.
For a roundup, use the primary factual update as event_key rather than treating the
roundup URL itself as a new event. editorial_key must be composed from primary_entity,
use_case, and content_format; the application will normalize and verify it.
Canonicalize semantically equivalent user tasks consistently. In particular, ordinary
end-to-end AI short-drama and AI comic-drama workflows that swap individual tools must
share topic_cluster `ai_narrative_video_creation` and use_case
`end_to_end_narrative_video_production` when body evidence supports that classification.
Social engagement metadata is corroborating evidence only. Evaluate editorial value,
audience fit, differentiation, and evidence quality independently; never claim that
engagement proves a factual assertion.
"""
        output_contract = """{
  "score": <number from 0 to 10>,
  "reason": "<concise evidence-based explanation>",
  "summary": "<one-sentence factual summary>",
  "tags": ["<tag>", "..."],
  "primary_entity": "<canonical product, tool, or brand identifier>",
  "topic_cluster": "<canonical topic identifier>",
  "use_case": "<canonical user task or scenario identifier>",
  "content_format": "product_release|feature_update|hands_on_test|tutorial_workflow|case_study|opinion_news",
  "novelty_level": "major_release|material_update|new_example|evergreen_repackage",
  "event_key": "<stable identifier for this exact real-world event>",
  "editorial_key": "<primary_entity|use_case|content_format>",
  "relevance_score": <number from 0 to 10>,
  "novelty_score": <number from 0 to 10>,
  "demonstrability_score": <number from 0 to 10>
  ,"editorial_value_score": <number from 0 to 10>
  ,"audience_fit_score": <number from 0 to 10>
  ,"differentiation_score": <number from 0 to 10>
  ,"evidence_quality_score": <number from 0 to 10>
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

{editorial_guidance}

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
    elif profile_id == "pangmen-topic-radar":
        keys = (
            "content_platform",
            "content_age_hours",
            "engagement_complete",
            "views",
            "likes",
            "favorites",
            "comments",
            "shares",
            "coins",
            "danmaku",
            "engagement_quality_score",
            "engagement_gate_status",
            "engagement_gate_reason",
        )
        metadata = {key: item.metadata[key] for key in keys if key in item.metadata}
        if metadata:
            metadata_section = (
                "\nVerified social engagement summary: "
                + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            )
    elif profile_id == "pangmen-platform-trend-radar":
        keys = (
            "platform",
            "platforms",
            "providers",
            "rank",
            "rank_limit",
            "hot_value",
            "heat_score",
            "heat_percentile",
            "rank_score",
            "trend_type",
            "trend_previous_heat_score",
            "trend_history_count",
            "cross_platform_count",
            "source_tier",
            "reliability",
            "source_url_kind",
        )
        metadata = {key: item.metadata[key] for key in keys if key in item.metadata}
        if metadata:
            metadata_section = (
                "\nVerified platform trend metadata: "
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
