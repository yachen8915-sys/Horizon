"""Core data models for Horizon."""

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Annotated, Literal, Optional, List, Dict, Any, NamedTuple, Union
from pydantic import (
    BaseModel,
    ConfigDict,
    HttpUrl,
    Field,
    field_validator,
    model_validator,
)


class SourceType(str, Enum):
    """Supported information source types."""

    GITHUB = "github"
    HACKERNEWS = "hackernews"
    RSS = "rss"
    REDDIT = "reddit"
    TELEGRAM = "telegram"
    TWITTER = "twitter"
    OPENBB = "openbb"
    OSSINSIGHT = "ossinsight"
    GDELT = "gdelt"
    GOOGLE_NEWS = "google_news"
    BILIBILI = "bilibili"
    AIHOT = "aihot"
    HUGGINGFACE = "huggingface"
    PLATFORM_TRENDS = "platform_trends"
    PLATFORM_CHANGES = "platform_changes"
    YOUTUBE = "youtube"
    BLUESKY = "bluesky"


class SourceDefinition(NamedTuple):
    """How a top-level source is represented in SourcesConfig."""

    config_field: str
    config_is_list: bool = False
    item_fields: tuple[str, ...] = ()


SOURCE_REGISTRY = {
    SourceType.GITHUB.value: SourceDefinition("github", config_is_list=True),
    SourceType.HACKERNEWS.value: SourceDefinition("hackernews"),
    SourceType.RSS.value: SourceDefinition("rss", config_is_list=True),
    SourceType.REDDIT.value: SourceDefinition("reddit", item_fields=("subreddits", "users")),
    SourceType.TELEGRAM.value: SourceDefinition("telegram", item_fields=("channels",)),
    SourceType.TWITTER.value: SourceDefinition("twitter", item_fields=("users",)),
    SourceType.OPENBB.value: SourceDefinition("openbb", item_fields=("watchlists",)),
    SourceType.OSSINSIGHT.value: SourceDefinition("ossinsight"),
    SourceType.GDELT.value: SourceDefinition("gdelt"),
    SourceType.GOOGLE_NEWS.value: SourceDefinition("google_news"),
    SourceType.BILIBILI.value: SourceDefinition("bilibili", item_fields=("queries",)),
    SourceType.AIHOT.value: SourceDefinition("aihot"),
    SourceType.HUGGINGFACE.value: SourceDefinition("huggingface"),
    SourceType.PLATFORM_TRENDS.value: SourceDefinition(
        "platform_trends", item_fields=("providers",)
    ),
    SourceType.PLATFORM_CHANGES.value: SourceDefinition(
        "platform_changes", item_fields=("watchers",)
    ),
    SourceType.YOUTUBE.value: SourceDefinition("youtube", item_fields=("queries",)),
    SourceType.BLUESKY.value: SourceDefinition(
        "bluesky", item_fields=("actors", "queries")
    ),
}

ProfileRoute = Optional[Union[str, List[str]]]


class DecisionLane(str, Enum):
    PRODUCT_CAPABILITY = "product_capability"
    HOT_CONTENT = "hot_content"
    TECHNICAL_FRONTIER = "technical_frontier"
    AI_INDUSTRY_SOCIETY = "ai_industry_society"
    PLATFORM_AI_CHANGE = "platform_ai_change"


class CandidateStatus(str, Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    OBSERVING = "observing"
    ELIGIBLE = "eligible"
    SELECTED = "selected"
    HELD = "held"
    MERGED = "merged"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PROCESSING_ERROR = "processing_error"


class EvidenceStatus(str, Enum):
    CONFIRMED = "confirmed"
    CORROBORATED = "corroborated"
    REPORTED = "reported"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"


class ReasonCode(str, Enum):
    PASSED_HARD_GATES = "passed_hard_gates"
    IMMATURE = "immature"
    LOW_PROPAGATION = "low_propagation"
    INCOMPLETE_ENGAGEMENT = "incomplete_engagement"
    LOW_RELEVANCE = "low_relevance"
    LOW_QUALITY = "low_quality"
    BRAND_SAFETY = "brand_safety"
    LOW_OPERATIONS_VALUE = "low_operations_value"
    INSUFFICIENT_HOTSPOT_SIGNAL = "insufficient_hotspot_signal"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    DUPLICATE = "duplicate"
    MERGED_INTO_STRONGER_EVIDENCE = "merged_into_stronger_evidence"
    HELD_BY_CAPACITY = "held_by_capacity"
    HELD_BY_DIVERSITY = "held_by_diversity"
    HELD_BY_EVIDENCE_LIMIT = "held_by_evidence_limit"
    SELECTED = "selected"
    EXPIRED = "expired"
    ANALYSIS_FAILED = "analysis_failed"
    PROCESSING_ERROR = "processing_error"


class RadarRunMode(str, Enum):
    SHADOW = "shadow"
    MORNING = "morning"
    AFTERNOON = "afternoon"


class CandidateScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_impact: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    audience_fit: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    novelty: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    evidence_quality: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    demonstrability: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    propagation_quality: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    freshness: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    differentiation: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)
    total: float = Field(default=0, ge=0, le=10, allow_inf_nan=False)


class EvidenceReference(BaseModel):
    source_item_id: str
    url: HttpUrl
    authority: str
    independent_group: str
    excerpt: str | None = None
    stance: Literal["supports", "contradicts"] = "supports"


class AtomicClaim(BaseModel):
    text: str
    status: EvidenceStatus
    evidence_refs: List[str] = Field(default_factory=list)


class IntelligenceAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_lane: DecisionLane
    content_kind: str = "other"
    novelty_basis: Literal[
        "new_event",
        "new_release",
        "new_data",
        "new_angle",
        "ongoing_update",
        "none",
    ] = "none"
    direct_impacts: List[str] = Field(default_factory=list)
    decision_summary: str
    content_summary: str
    evidence_status: EvidenceStatus
    claims: List[AtomicClaim] = Field(default_factory=list)
    evidence_refs: List[EvidenceReference] = Field(default_factory=list)
    score: CandidateScore = Field(default_factory=CandidateScore)


class ClassificationResult(BaseModel):
    """Resolved processing profile for a content item."""

    profile: str
    method: Literal["source_override", "ai_match"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reason: Optional[str] = None


class ContentAnalysis(BaseModel):
    """Profile-driven first-pass analysis."""

    score: Optional[float] = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    operations_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    content_opportunity_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    operations_focus: Literal[
        "ai_tech",
        "workplace_youth",
        "visual_content",
        "general",
    ] | None = None
    operations_reason: Optional[str] = None
    is_platform_change: Optional[bool] = None
    platform: Optional[Literal["douyin", "xiaohongshu", "bilibili", "wechat"]] = None
    change_types: List[
        Literal["operation", "ecommerce", "feature", "rule"]
    ] = Field(default_factory=list)
    source_level: Optional[
        Literal["official", "official_republished", "secondary", "unverified"]
    ] = None
    affected_audience: List[str] = Field(default_factory=list)
    impact_level: Optional[Literal["high", "medium", "low", "unknown"]] = None
    change_status: Optional[str] = None
    primary_entity: Optional[str] = None
    topic_cluster: Optional[str] = None
    use_case: Optional[str] = None
    content_format: Optional[
        Literal[
            "product_release",
            "feature_update",
            "hands_on_test",
            "tutorial_workflow",
            "case_study",
            "opinion_news",
        ]
    ] = None
    novelty_level: Optional[
        Literal[
            "major_release",
            "material_update",
            "new_example",
            "evergreen_repackage",
        ]
    ] = None
    event_key: Optional[str] = None
    editorial_key: Optional[str] = None
    relevance_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    novelty_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    demonstrability_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    editorial_value_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    audience_fit_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    differentiation_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    evidence_quality_score: Optional[float] = Field(
        default=None, ge=0, le=10, allow_inf_nan=False
    )
    reason: str
    summary: str
    tags: List[str] = Field(default_factory=list)
    intelligence: Optional[IntelligenceAnalysis] = None


class ArtifactSource(BaseModel):
    """External source used while producing an artifact."""

    id: str
    title: str
    url: str


class ContentBlock(BaseModel):
    """A renderable section produced by an enrichment profile."""

    id: str
    type: Literal["section"] = "section"
    title: str
    content: str
    source_refs: List[str] = Field(default_factory=list)
    primary: bool = False


class ContentArtifact(BaseModel):
    """Localized, profile-defined enriched content."""

    language: str
    title: str
    blocks: List[ContentBlock] = Field(default_factory=list)
    sources: List[ArtifactSource] = Field(default_factory=list)


class ProcessingResult(BaseModel):
    """All AI processing state for a content item."""

    classification: ClassificationResult
    analysis: Optional[ContentAnalysis] = None
    artifacts: Dict[str, ContentArtifact] = Field(default_factory=dict)


class ContentItem(BaseModel):
    """Unified content item model from any source."""

    model_config = ConfigDict(extra="forbid")

    id: str  # Format: {source}:{subtype}:{native_id}
    source_type: SourceType
    title: str
    url: HttpUrl
    content: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    profile: ProfileRoute = None
    processing: Optional[ProcessingResult] = None


class CandidateStatusTransition(BaseModel):
    from_status: CandidateStatus | None = None
    to_status: CandidateStatus
    changed_at: datetime
    reason_code: ReasonCode | None = None
    detail: str | None = None


class CandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    item: ContentItem
    status: CandidateStatus = CandidateStatus.DISCOVERED
    discovered_at: datetime
    updated_at: datetime
    canonical_url: str | None = None
    source_item_id: str | None = None
    event_key: str | None = None
    event_version: int = Field(default=1, ge=1)
    event_version_at: datetime | None = None
    editorial_topic_key: str | None = None
    evidence_status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    intelligence: IntelligenceAnalysis | None = None
    evidence_refs: List[EvidenceReference] = Field(default_factory=list)
    merged_into: str | None = None
    reason_codes: List[ReasonCode] = Field(default_factory=list)
    status_history: List[CandidateStatusTransition] = Field(default_factory=list)
    rule_version: str


class DeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: str
    run_id: str
    run_mode: RadarRunMode
    delivered_at: datetime
    candidate_id: str
    event_key: str
    event_version: int = Field(ge=1)
    display_tier: Literal["selected", "more"]
    content_fingerprint: str


class AIProvider(str, Enum):
    """Supported AI providers."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE = "azure"
    ALI = "ali"
    GEMINI = "gemini"
    DOUBAO = "doubao"
    MINIMAX = "minimax"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"


# Provider-specific defaults used by setup and provider-chain expansion.
AI_PROVIDER_DEFAULTS = {
    AIProvider.ANTHROPIC: {
        "model": "claude-3-5-sonnet-20241022",
        "api_key_env": "ANTHROPIC_API_KEY",
        "base_url": None,
    },
    AIProvider.OPENAI: {
        "model": "gpt-4",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": None,
    },
    AIProvider.AZURE: {
        "model": "gpt-4",
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "base_url": None,
        "azure_endpoint_env": "AZURE_OPENAI_ENDPOINT",
        "api_version": "2024-10-21",
    },
    AIProvider.ALI: {
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    AIProvider.GEMINI: {
        "model": "gemini-1.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "base_url": None,
    },
    AIProvider.DOUBAO: {
        "model": "doubao-pro-32k",
        "api_key_env": "DOUBAO_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    AIProvider.MINIMAX: {
        "model": "MiniMax-M3",
        "api_key_env": "MINIMAX_API_KEY",
        "base_url": "https://api.minimax.io/v1",
    },
    AIProvider.DEEPSEEK: {
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    },
    AIProvider.OLLAMA: {
        "model": "llama3.1",
        "api_key_env": "",
        "base_url": "http://localhost:11434/v1",
    },
}


class AIConfig(BaseModel):
    """AI client configuration."""

    provider: AIProvider
    provider_chain: Optional[str] = None
    model: str
    base_url: Optional[str] = None
    api_key_env: str
    temperature: float = 0.3
    max_tokens: int = 4096
    throttle_sec: float = 0.0
    request_timeout_sec: float = Field(default=60.0, ge=0.1)
    analysis_concurrency: int = 1
    enrichment_concurrency: int = 1
    languages: List[str] = Field(default_factory=lambda: ["en"])
    # Azure OpenAI specific; required when provider == AZURE
    azure_endpoint_env: Optional[str] = None
    api_version: Optional[str] = None

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, languages: List[str]) -> List[str]:
        """Allow conventional language tags while excluding path syntax."""
        language_tag = re.compile(r"^[A-Za-z]{2,8}(?:[-_][A-Za-z0-9]{1,8})*$")
        invalid = [language for language in languages if not language_tag.fullmatch(language)]
        if invalid:
            raise ValueError(f"invalid language code: {invalid[0]!r}")
        return languages


class GitHubSourceConfig(BaseModel):
    """GitHub source configuration."""

    type: str  # "user_events", "repo_releases", etc.
    username: Optional[str] = None
    owner: Optional[str] = None
    repo: Optional[str] = None
    query: Optional[str] = None
    fetch_limit: int = Field(default=20, ge=1, le=100)
    min_stars: int = Field(default=0, ge=0)
    discovery_window_days: int = Field(default=30, ge=1, le=365)
    shadow: bool = False
    enabled: bool = True
    category: Optional[str] = None
    profile: ProfileRoute = None


class HackerNewsConfig(BaseModel):
    """Hacker News configuration."""

    enabled: bool = True
    fetch_top_stories: int = 30
    min_score: int = 100
    category: Optional[str] = None
    profile: ProfileRoute = None


class ExtractorType(str, Enum):
    TRAFILATURA = "trafilatura"


class TrafilaturaExtractorConfig(BaseModel):
    type: Literal[ExtractorType.TRAFILATURA] = ExtractorType.TRAFILATURA
    favor_precision: bool = False
    favor_recall: bool = False


ExtractorConfig = Annotated[
    Union[TrafilaturaExtractorConfig],
    Field(discriminator="type"),
]


class RSSSourceConfig(BaseModel):
    """RSS feed source configuration."""

    name: str
    url: HttpUrl
    enabled: bool = True
    shadow: bool = False
    expected_cadence_hours: float = Field(default=168, gt=0)
    stale_after_multiplier: float = Field(default=3, gt=0)
    category: Optional[str] = None
    content_extractor: Optional[str] = None
    profile: ProfileRoute = None


class RedditSubredditConfig(BaseModel):
    """Configuration for monitoring a specific subreddit."""

    subreddit: str
    enabled: bool = True
    sort: str = "hot"  # hot, new, top, rising
    time_filter: str = (
        "day"  # hour, day, week, month, year, all (only for top/controversial)
    )
    fetch_limit: int = 25
    min_score: int = 10
    category: Optional[str] = None
    profile: ProfileRoute = None


class RedditUserConfig(BaseModel):
    """Configuration for monitoring a specific Reddit user."""

    username: str  # without u/ prefix
    enabled: bool = True
    sort: str = "new"
    fetch_limit: int = 10
    category: Optional[str] = None
    profile: ProfileRoute = None


class RedditConfig(BaseModel):
    """Reddit source configuration."""

    enabled: bool = True
    shadow: bool = False
    subreddits: List[RedditSubredditConfig] = Field(default_factory=list)
    users: List[RedditUserConfig] = Field(default_factory=list)
    fetch_comments: int = 5  # top comments per post, 0 to disable


class TelegramChannelConfig(BaseModel):
    """Configuration for monitoring a specific Telegram channel."""

    channel: str  # channel username, e.g. "zaihuapd"
    enabled: bool = True
    fetch_limit: int = 20
    category: Optional[str] = None
    profile: ProfileRoute = None


class TelegramConfig(BaseModel):
    """Telegram source configuration."""

    enabled: bool = True
    channels: List[TelegramChannelConfig] = Field(default_factory=list)


class TwitterConfig(BaseModel):
    """Twitter source configuration.

    Two modes are supported:
    - "apify": Use Apify scweet actor (requires APIFY_TOKEN, more reliable)
    - "playwright": Use Playwright + browser cookies (free, no token needed)
    """

    enabled: bool = True
    mode: str = "apify"  # "apify", "playwright", or "official_api"
    shadow: bool = False
    users: List[str] = Field(default_factory=list)
    fetch_limit: int = 10
    category: Optional[str] = None
    profile: ProfileRoute = None
    fetch_reply_text: bool = False
    max_replies_per_tweet: int = 3
    max_tweets_to_expand: int = 10
    reply_min_likes: int = 0
    # Apify settings (used when mode == "apify")
    apify_token_env: str = "APIFY_TOKEN"
    actor_id: str = "altimis~scweet"
    # Official X API settings (paid, so bounded and shadowed by default in production config)
    official_bearer_token_env: str = "X_BEARER_TOKEN"
    official_queries: List[str] = Field(default_factory=list)
    official_max_requests_per_run: int = Field(default=2, ge=1, le=20)
    # Optional local fallback. It reuses an already-authenticated desktop browser
    # through OpenCLI and is not expected to exist on a cloud runner.
    local_cli_fallback_enabled: bool = False
    local_cli_command: str = "opencli"
    local_cli_timeout_sec: int = Field(default=45, ge=5, le=300)
    # Playwright settings (used when mode == "playwright")
    cookie_dir: str = "data"
    cookie_file_pattern: str = "x_cookies_*.json"


class YouTubeQueryConfig(BaseModel):
    """One official YouTube Data API search lane."""

    query: str = Field(min_length=1)
    enabled: bool = True
    fetch_limit: int = Field(default=10, ge=1, le=50)
    order: Literal["date", "rating", "relevance", "viewCount"] = "viewCount"
    category: Optional[str] = None
    profile: ProfileRoute = None


class YouTubeChannelConfig(BaseModel):
    """One trusted YouTube channel monitored through its uploads playlist."""

    channel_id: str = Field(min_length=1)
    name: Optional[str] = None
    enabled: bool = True
    fetch_limit: int = Field(default=5, ge=1, le=50)
    category: Optional[str] = None
    profile: ProfileRoute = None


class YouTubeDataConfig(BaseModel):
    """Official YouTube search and native engagement enrichment."""

    enabled: bool = False
    shadow: bool = True
    api_key_env: str = "YOUTUBE_DATA_API_KEY"
    channels: List[YouTubeChannelConfig] = Field(default_factory=list)
    queries: List[YouTubeQueryConfig] = Field(default_factory=list)
    relevance_language: str = "en"
    region_code: Optional[str] = "US"
    max_units_per_run: int = Field(default=50, ge=2, le=10000)
    # Optional anonymous local fallback. The official Data API remains preferred
    # whenever a key is available.
    local_cli_fallback_enabled: bool = False
    local_cli_command: str = "yt-dlp"
    local_cli_timeout_sec: int = Field(default=90, ge=10, le=600)
    local_cli_concurrency: int = Field(default=2, ge=1, le=4)


class BlueskyQueryConfig(BaseModel):
    """One public Bluesky post-search lane."""

    query: str = Field(min_length=1)
    enabled: bool = True
    fetch_limit: int = Field(default=25, ge=1, le=100)
    sort: Literal["top", "latest"] = "top"
    category: Optional[str] = None
    profile: ProfileRoute = None


class BlueskyConfig(BaseModel):
    """Anonymous Bluesky AppView discovery configuration."""

    enabled: bool = False
    shadow: bool = True
    public_api_base_url: HttpUrl = "https://public.api.bsky.app"
    actors: List[str] = Field(default_factory=list)
    actor_fetch_limit: int = Field(default=10, ge=1, le=100)
    queries: List[BlueskyQueryConfig] = Field(default_factory=list)
    max_requests_per_run: int = Field(default=8, ge=1, le=50)
    category: Optional[str] = "overseas-ai-community"
    profile: ProfileRoute = "pangmen-topic-radar"


class OpenBBWatchlist(BaseModel):
    """A named watchlist of tickers fetched from one OpenBB provider.

    Each watchlist produces one news.company() call per run, so group
    symbols by provider rather than creating one watchlist per symbol.
    """

    name: str
    symbols: List[str] = Field(default_factory=list)
    enabled: bool = True
    provider: str = "yfinance"
    fetch_limit: int = 20
    category: Optional[str] = None
    profile: ProfileRoute = None


class OpenBBConfig(BaseModel):
    """OpenBB Platform source configuration.

    Uses the installed `openbb` SDK to fetch news and filings for a set of
    tickers. The SDK is an optional dependency; if it is not installed the
    scraper will no-op with a console warning rather than crash the run.

    Provider credentials (FMP, Benzinga, Polygon, Intrinio, Tiingo, etc.)
    are resolved by openbb from environment variables / its own user
    settings file, so Horizon does not need to pass them explicitly.
    """

    enabled: bool = True
    watchlists: List[OpenBBWatchlist] = Field(default_factory=list)
    fetch_filings: bool = False
    filings_provider: str = "sec"


class OSSInsightConfig(BaseModel):
    """OSS Insight trending repos source configuration.

    Pulls top star-gain repositories from the OSS Insight public API and
    emits them as ContentItems. Optional `keywords` filter limits results
    to repos whose description, repo name, or collection names contain at
    least one of the listed substrings (case-insensitive). Leave
    `keywords` empty to ingest everything trending in the configured
    languages.
    """

    enabled: bool = False
    period: str = "past_24_hours"  # past_24_hours, past_28_days
    languages: List[str] = Field(
        default_factory=lambda: ["All", "Python", "TypeScript"]
    )
    keywords: List[str] = Field(default_factory=list)
    min_stars: int = 5
    max_items: int = 30
    category: Optional[str] = None
    profile: ProfileRoute = None


class GDELTConfig(BaseModel):
    """GDELT 2.0 DOC API source configuration.

    Queries the key-less GDELT DOC API
    (https://api.gdeltproject.org/api/v2/doc/doc) for recent news articles
    matching a search query and emits them as ContentItems. No API key is
    required. The DOC API caps results at 250 records per request, so keep
    `max_records` modest.
    """

    enabled: bool = False
    query: str = "artificial intelligence"
    mode: str = "ArtList"
    max_records: int = 75  # GDELT DOC API caps at 250; keep modest
    timespan: Optional[str] = None  # e.g. "24h"; overrides since-derived window
    language: Optional[str] = None  # sourcelang filter, e.g. "english"; None = no filter
    country: Optional[str] = None  # sourcecountry filter; None = no filter
    category: Optional[str] = None  # Horizon category label for downstream grouping
    profile: ProfileRoute = None


class GoogleNewsConfig(BaseModel):
    """Google News RSS search source configuration.

    Builds Google News RSS search URLs
    (https://news.google.com/rss/search) for a query and parses the
    resulting feed via feedparser. No API key is required.
    """

    enabled: bool = False
    query: str = "artificial intelligence"
    language: str = "en"  # hl
    country: str = "US"  # gl
    ceid: Optional[str] = None  # when None scraper derives it as "{country}:{language}"
    max_results: int = 100  # cap ~100
    category: Optional[str] = None
    profile: ProfileRoute = None


class BilibiliQueryConfig(BaseModel):
    query: str
    author: Optional[str] = None
    enabled: bool = True
    fetch_limit: int = 20
    category: Optional[str] = None
    profile: ProfileRoute = None


class BilibiliDiscoveryChannelConfig(BaseModel):
    """Auditable public-search route used for one Bilibili query."""

    model_config = ConfigDict(extra="forbid")

    name: str
    order: Literal["pubdate", "click", "totalrank", "dm", "stow"]


class BilibiliConfig(BaseModel):
    enabled: bool = False
    queries: List[BilibiliQueryConfig] = Field(default_factory=list)
    request_interval_seconds: float = Field(default=1.5, ge=0)
    retry_delay_seconds: float = Field(default=3.0, ge=0)
    discovery_channels: List[BilibiliDiscoveryChannelConfig] = Field(
        default_factory=lambda: [
            BilibiliDiscoveryChannelConfig(name="latest", order="pubdate")
        ]
    )


class AIHotConfig(BaseModel):
    """Anonymous, read-only AI HOT supplement source."""

    enabled: bool = False
    fetch_24h: bool = True
    fetch_7d: bool = True
    fetch_hot_topics: bool = True
    fetch_all_24h: bool = True
    fetch_all_7d: bool = False
    all_mode_min_score: int = Field(default=35, ge=0, le=100)
    all_mode_lookback_hours: int = Field(default=48, ge=1, le=168)
    keywords: List[str] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=100)
    request_interval_seconds: float = Field(default=1.0, ge=0)
    etag_file: str = "aihot_etags.json"


class HuggingFaceConfig(BaseModel):
    """Official Hugging Face model and Daily Papers discovery feeds."""

    enabled: bool = False
    fetch_models: bool = True
    fetch_papers: bool = True
    model_limit: int = Field(default=10, ge=1, le=50)
    paper_limit: int = Field(default=10, ge=1, le=50)
    model_min_trending_score: int = Field(default=1, ge=0)
    paper_min_upvotes: int = Field(default=1, ge=0)
    profile: ProfileRoute = "pangmen-ai-tech-radar"


class PlatformTrendProviderConfig(BaseModel):
    """One configurable public or third-party platform trend endpoint."""

    enabled: bool = True
    platform: Literal[
        "weibo",
        "douyin",
        "xiaohongshu",
        "wechat",
        "toutiao",
        "zhihu",
        "baidu",
        "36kr",
    ]
    provider: str
    provider_name: Optional[str] = None
    base_url: Optional[HttpUrl] = None
    endpoint: Optional[str] = None
    request_method: Literal["GET", "POST"] = "GET"
    response_adapter: Literal["generic", "dailyhotapi", "alapi_tophub"] = (
        "generic"
    )
    source_id: Optional[str] = None
    query_params: Dict[str, Union[str, int, float, bool]] = Field(
        default_factory=dict
    )
    body_params: Dict[str, Union[str, int, float, bool]] = Field(
        default_factory=dict
    )
    api_key_env: Optional[str] = None
    auth_type: Literal["header", "query"] = "header"
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer"
    observed_timezone: str = "UTC"
    fetch_limit: int = Field(default=20, ge=1, le=100)
    rank_limit: int = Field(default=50, ge=1, le=100)
    category: str = "platform-trend"
    profile: ProfileRoute = "pangmen-platform-trend-radar"
    reliability: str = "aggregator"


class PlatformTrendsConfig(BaseModel):
    enabled: bool = False
    providers: List[PlatformTrendProviderConfig] = Field(default_factory=list)


class PlatformChangeWatcherConfig(BaseModel):
    """One public platform page or Google News discovery watcher."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    mode: Literal[
        "index",
        "page_diff",
        "search_rss",
        "xiaohongshu_rules",
        "xiaohongshu_help_api",
        "bilibili_bundle_diff",
    ]
    platform: str = Field(min_length=1)
    enabled: bool = True
    url: Optional[HttpUrl] = None
    query: Optional[str] = None
    fallback_query: Optional[str] = None
    api_role: str = "4"
    same_domain_only: bool = True
    include_patterns: List[str] = Field(default_factory=list)
    exclude_patterns: List[str] = Field(default_factory=list)
    ignore_patterns: List[str] = Field(default_factory=list)
    fetch_limit: int = Field(default=20, ge=1, le=100)
    min_content_chars: int = Field(default=80, ge=1, le=100_000)
    change_types: List[
        Literal["operation", "ecommerce", "feature", "rule"]
    ] = Field(default_factory=list)
    source_level: Literal[
        "official", "official_republished", "secondary", "unverified"
    ] = "secondary"
    attribution_keywords: List[str] = Field(default_factory=list)
    official_domains: List[str] = Field(default_factory=list)
    language: str = "zh-CN"
    country: str = "CN"
    ceid: Optional[str] = "CN:zh-Hans"
    observed_timezone: str = "Asia/Shanghai"
    category: str = "platform-change"
    profile: ProfileRoute = "pangmen-platform-change-radar"

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> "PlatformChangeWatcherConfig":
        if self.mode in {
            "index",
            "page_diff",
            "xiaohongshu_rules",
            "xiaohongshu_help_api",
            "bilibili_bundle_diff",
        } and self.url is None:
            raise ValueError(f"platform change watcher mode {self.mode} requires url")
        if self.mode == "search_rss" and not (self.query or "").strip():
            raise ValueError("platform change watcher mode search_rss requires query")
        if len(self.change_types) != len(set(self.change_types)):
            raise ValueError("platform change watcher change_types must be unique")
        return self


class PlatformChangesConfig(BaseModel):
    """Stable public-source platform change monitoring configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    shadow: bool = False
    lookback_days: int = Field(default=7, ge=1, le=30)
    state_file: str = "data/platform_change_state.json"
    shadow_state_file: str = "data/shadow/platform_change_state.json"
    watchers: List[PlatformChangeWatcherConfig] = Field(default_factory=list)

    @field_validator("state_file", "shadow_state_file")
    @classmethod
    def validate_state_file(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("platform_changes.state_file cannot be empty")
        return value


class SourcesConfig(BaseModel):
    """All sources configuration."""

    github: List[GitHubSourceConfig] = Field(default_factory=list)
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)
    rss: List[RSSSourceConfig] = Field(default_factory=list)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    twitter: Optional[TwitterConfig] = None
    youtube: Optional[YouTubeDataConfig] = None
    bluesky: Optional[BlueskyConfig] = None
    openbb: Optional[OpenBBConfig] = None
    ossinsight: OSSInsightConfig = Field(default_factory=OSSInsightConfig)
    gdelt: Optional[GDELTConfig] = None
    google_news: Optional[GoogleNewsConfig] = None
    bilibili: BilibiliConfig = Field(default_factory=BilibiliConfig)
    aihot: AIHotConfig = Field(default_factory=AIHotConfig)
    huggingface: HuggingFaceConfig = Field(default_factory=HuggingFaceConfig)
    platform_trends: PlatformTrendsConfig = Field(default_factory=PlatformTrendsConfig)
    platform_changes: PlatformChangesConfig = Field(default_factory=PlatformChangesConfig)


class WebhookConfig(BaseModel):
    """Webhook notification configuration."""

    url_env: Optional[str] = (
        None  # Environment variable name containing the webhook URL
    )
    request_body: Optional[Union[str, dict, list]] = (
        None  # POST body: real JSON object or string with #{key} placeholders; if empty, will use GET
    )
    headers: Optional[str] = None  # Custom headers, "Key: Value" per line
    delivery: str = "summary"  # summary, or summary_and_items
    overview_position: str = "first"  # For summary_and_items: first, or last
    platform: str = "generic"  # generic, feishu, lark, dingtalk, slack, discord
    layout: str = "markdown"  # markdown, or collapsible
    fallback_layout: str = (
        "markdown"  # Layout to use when the requested layout is unsupported
    )
    languages: Optional[List[str]] = (
        None  # Optional language filter for webhook delivery; defaults to all AI languages
    )
    title_prefix: str = ""
    enabled: bool = False

    @field_validator("delivery")
    @classmethod
    def validate_delivery(cls, v: str) -> str:
        allowed = {"summary", "summary_and_items"}
        if v not in allowed:
            raise ValueError(f"webhook.delivery must be one of {allowed}, got '{v}'")
        return v

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        allowed = {"generic", "feishu", "lark", "dingtalk", "slack", "discord"}
        if v not in allowed:
            raise ValueError(f"webhook.platform must be one of {allowed}, got '{v}'")
        return v

    @field_validator("layout")
    @classmethod
    def validate_layout(cls, v: str) -> str:
        allowed = {"markdown", "collapsible"}
        if v not in allowed:
            raise ValueError(f"webhook.layout must be one of {allowed}, got '{v}'")
        return v

    @field_validator("fallback_layout")
    @classmethod
    def validate_fallback_layout(cls, v: str) -> str:
        allowed = {"markdown", "collapsible"}
        if v not in allowed:
            raise ValueError(
                f"webhook.fallback_layout must be one of {allowed}, got '{v}'"
            )
        return v

    @field_validator("overview_position")
    @classmethod
    def validate_overview_position(cls, v: str) -> str:
        allowed = {"first", "last"}
        if v not in allowed:
            raise ValueError(
                f"webhook.overview_position must be one of {allowed}, got '{v}'"
            )
        return v


class EmailConfig(BaseModel):
    """Email configuration for updates/subscriptions."""

    imap_server: str
    imap_port: int = 993
    imap_enabled: bool = True
    smtp_server: str
    smtp_port: int = 465
    smtp_username: Optional[str] = None
    email_address: str
    password_env: str = "EMAIL_PASSWORD"
    sender_name: str = "Horizon Daily"
    subscribe_keyword: str = "SUBSCRIBE"
    unsubscribe_keyword: str = "UNSUBSCRIBE"
    enabled: bool = False


class CategoryGroupConfig(BaseModel):
    """A quota group containing one or more source categories."""

    name: Optional[str] = None
    limit: int = Field(gt=0)
    categories: List[str] = Field(min_length=1)


class ProfileSettingsConfig(BaseModel):
    """User preferences applied to a processing profile at runtime."""

    model_config = ConfigDict(extra="forbid")

    threshold: Optional[float] = Field(default=None, ge=0, le=10)
    topic_dedup: bool = True


class ProcessingConfig(BaseModel):
    """Profile discovery and fallback settings."""

    model_config = ConfigDict(extra="forbid")

    profiles_dir: str = "profiles"
    default_profile: str = "tech-news"
    profile_settings: Dict[str, ProfileSettingsConfig] = Field(default_factory=dict)


class DisplayConfig(BaseModel):
    """Controls terminal output presentation."""

    model_config = ConfigDict(extra="forbid")

    icon_style: Literal["emoji", "nerd", "ascii"] = "emoji"


class EngagementThresholdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    absolute: int = Field(ge=0)
    relative: float = Field(ge=0)


class EngagementTrackingConfig(BaseModel):
    """One initial snapshot plus one refresh around 24 hours later."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    refresh_after_hours: int = Field(default=24, ge=1)
    lookback_hours: int = Field(default=48, ge=24)
    state_filename: str = "engagement_snapshots.json"
    thresholds: Dict[str, EngagementThresholdConfig] = Field(default_factory=dict)

    @field_validator("state_filename")
    @classmethod
    def validate_state_filename(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("state_filename must be a plain filename")
        return value


class SocialEngagementPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    maturity_hours: float = Field(default=6, ge=0)
    primary_metric: str = "views"
    minimum_sample: int = Field(default=200, ge=0)
    relative_quantile: float = Field(default=0.75, ge=0, le=1)
    relative_ratio: float = Field(default=0.35, ge=0, le=1)
    minimum_quality_score: float = Field(default=4, ge=0, le=10)
    required_metrics: List[str] = Field(
        default_factory=lambda: ["views", "likes", "comments"]
    )
    minimum_complete_fields: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def validate_metric_contract(self) -> "SocialEngagementPolicyConfig":
        if self.primary_metric not in self.required_metrics:
            raise ValueError("primary_metric must be included in required_metrics")
        if self.minimum_complete_fields > len(self.required_metrics):
            raise ValueError("minimum_complete_fields exceeds required_metrics")
        return self


class SocialEngagementQualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    platforms: Dict[str, SocialEngagementPolicyConfig] = Field(default_factory=dict)


class CollectionConfig(BaseModel):
    """Controls which source items are fetched."""

    model_config = ConfigDict(extra="forbid")

    time_window_hours: int = 24
    source_registry_file: str | None = None
    core_entity_registry_file: str | None = None
    source_health_state_file: str = "data/shadow/source_health_state.json"
    source_shadow_enabled: bool = False
    engagement_tracking: EngagementTrackingConfig = Field(
        default_factory=EngagementTrackingConfig
    )
    social_engagement_quality: SocialEngagementQualityConfig = Field(
        default_factory=SocialEngagementQualityConfig
    )

    @field_validator(
        "source_registry_file",
        "core_entity_registry_file",
        "source_health_state_file",
    )
    @classmethod
    def validate_source_state_paths(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:/", normalized)
            or ".." in normalized.split("/")
        ):
            raise ValueError("source registry/state paths must be relative safe paths")
        return normalized


class IntelligenceSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_min_items: int = Field(default=8, ge=1)
    max_items: int = Field(default=20, ge=1)
    minimum_score: float = Field(default=6.5, ge=0, le=10)
    author_limit: int = Field(default=2, ge=1)
    source_limit: int = Field(default=3, ge=1)
    platform_limit: int = Field(default=4, ge=1)
    topic_limit: int = Field(default=1, ge=1)
    unverified_hot_limit: int = Field(default=3, ge=0)
    platform_trend_detail_limit: int = Field(default=15, ge=1, le=15)
    cooldown_days: int = Field(default=7, ge=1)
    major_event_override_score: float = Field(default=9, ge=0, le=10)


class IntelligenceRadarConfig(BaseModel):
    """Safe-by-default controls for the redesigned intelligence pipeline."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    delivery_enabled: bool = False
    canary_mode: bool = False
    run_mode: RadarRunMode = RadarRunMode.SHADOW
    decision_lanes: List[DecisionLane] = Field(
        default_factory=lambda: list(DecisionLane)
    )
    candidate_store_file: str = "data/candidates/candidate_ledger.jsonl"
    delivery_store_file: str = "data/candidates/delivery_ledger.jsonl"
    rule_version: str = "2026-09-03-v1"
    selection: IntelligenceSelectionConfig = Field(
        default_factory=IntelligenceSelectionConfig
    )

    @model_validator(mode="after")
    def validate_delivery_mode(self) -> "IntelligenceRadarConfig":
        if self.delivery_enabled and self.run_mode is RadarRunMode.SHADOW:
            raise ValueError(
                "intelligence delivery cannot be enabled in shadow mode"
            )
        if self.canary_mode and not self.delivery_enabled:
            raise ValueError("intelligence canary mode requires delivery to be enabled")
        return self

    @field_validator("candidate_store_file", "delivery_store_file")
    @classmethod
    def validate_ledger_paths(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:/", normalized)
            or ".." in normalized.split("/")
        ):
            raise ValueError("intelligence ledger paths must be relative safe paths")
        return normalized

    @field_validator("decision_lanes")
    @classmethod
    def validate_decision_lanes(cls, value: List[DecisionLane]) -> List[DecisionLane]:
        if not value or len(value) != len(set(value)):
            raise ValueError("intelligence.decision_lanes must be non-empty and unique")
        return value

    @field_validator("rule_version")
    @classmethod
    def validate_rule_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("intelligence.rule_version cannot be empty")
        return value.strip()


class EditorialSelectionConfig(BaseModel):
    """Same-day diversity and cross-day freshness for AI editorial profiles."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    state_file: str = "data/digest_selection_state.json"
    history_days: int = Field(default=7, ge=1)
    editorial_cooldown_days: int = Field(default=3, ge=1)
    semantic_cooldown_days: int = Field(default=3, ge=1)
    same_day_semantic_limit: int = Field(default=1, ge=1)
    primary_entity_limit: int = Field(default=2, ge=1)
    topic_cluster_limit: int = Field(default=2, ge=1)
    use_case_limit: int = Field(default=2, ge=1)
    tutorial_workflow_limit: int = Field(default=2, ge=1)
    sub_source_limit: int = Field(default=2, ge=1)
    max_history_entries: int = Field(default=500, ge=1)

    @field_validator("state_file")
    @classmethod
    def validate_state_file(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("digest.editorial_selection.state_file cannot be empty")
        return value


class DigestConfig(BaseModel):
    """Controls grouping and limits in the final digest."""

    model_config = ConfigDict(extra="forbid")

    max_items: Optional[int] = Field(default=None, gt=0)
    category_groups: Dict[str, CategoryGroupConfig] = Field(default_factory=dict)
    default_group: str = "other"
    default_group_limit: Optional[int] = Field(default=None, gt=0)
    profile_limits: Dict[str, int] = Field(default_factory=dict)
    unbounded_profiles: List[str] = Field(default_factory=list)
    platform_trend_leverage_limit: Optional[int] = Field(default=None, gt=0)
    platform_trend_watch_limit: Optional[int] = Field(default=None, gt=0)
    platform_trend_minimum_per_platform: Optional[int] = Field(default=None, gt=0)
    editorial_selection: EditorialSelectionConfig = Field(
        default_factory=EditorialSelectionConfig
    )
    profile_order: List[str] = Field(default_factory=list)

    @field_validator("profile_limits")
    @classmethod
    def validate_profile_limits(cls, value: Dict[str, int]) -> Dict[str, int]:
        if any(not profile_id.strip() for profile_id in value):
            raise ValueError("digest.profile_limits keys must be non-empty strings")
        if any(limit <= 0 for limit in value.values()):
            raise ValueError("digest.profile_limits values must be greater than zero")
        return value

    @field_validator("unbounded_profiles")
    @classmethod
    def validate_unbounded_profiles(cls, value: List[str]) -> List[str]:
        if any(not profile_id.strip() for profile_id in value):
            raise ValueError("digest.unbounded_profiles entries must be non-empty strings")
        if len(value) != len(set(value)):
            raise ValueError("digest.unbounded_profiles entries must be unique")
        return value

    @field_validator("profile_order")
    @classmethod
    def validate_profile_order(cls, value: List[str]) -> List[str]:
        if any(not profile_id.strip() for profile_id in value):
            raise ValueError("digest.profile_order entries must be non-empty strings")
        if len(value) != len(set(value)):
            raise ValueError("digest.profile_order entries must be unique")
        return value


class Config(BaseModel):
    """Main configuration model."""

    model_config = ConfigDict(extra="forbid")

    ai: AIConfig
    sources: SourcesConfig
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    intelligence: IntelligenceRadarConfig = Field(
        default_factory=IntelligenceRadarConfig
    )
    digest: DigestConfig = Field(default_factory=DigestConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    extractors: Dict[str, ExtractorConfig] = Field(default_factory=dict)
    email: Optional[EmailConfig] = None
    webhook: Optional[WebhookConfig] = None

    @property
    def delivery_allowed(self) -> bool:
        return not self.intelligence.enabled or self.intelligence.delivery_enabled
