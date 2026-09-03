from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx

from src.models import GitHubSourceConfig
from src.scrapers.github import GitHubScraper


SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_repository_search_discovers_new_high_signal_projects() -> None:
    client = AsyncMock()
    client.get.return_value = _response(
        {
            "items": [
                {
                    "id": 101,
                    "full_name": "example/new-agent",
                    "html_url": "https://github.com/example/new-agent",
                    "description": "A useful AI agent",
                    "created_at": "2026-09-02T02:00:00Z",
                    "updated_at": "2026-09-02T08:00:00Z",
                    "stargazers_count": 420,
                    "forks_count": 30,
                    "open_issues_count": 4,
                    "language": "Python",
                    "topics": ["ai", "agents"],
                    "owner": {"login": "example"},
                },
                {
                    "id": 102,
                    "full_name": "example/old-project",
                    "html_url": "https://github.com/example/old-project",
                    "description": "Old",
                    "created_at": "2026-08-01T02:00:00Z",
                    "updated_at": "2026-09-02T08:00:00Z",
                    "stargazers_count": 9999,
                    "forks_count": 200,
                    "open_issues_count": 10,
                    "language": "Python",
                    "topics": ["ai"],
                    "owner": {"login": "example"},
                },
            ]
        }
    )
    source = GitHubSourceConfig(
        type="repo_search",
        query="topic:artificial-intelligence",
        fetch_limit=20,
        min_stars=50,
        discovery_window_days=30,
        category="new-ai-project",
        profile="pangmen-ai-tech-radar",
        shadow=True,
    )

    items = asyncio.run(GitHubScraper([source], client).fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.id == "github:repository:101"
    assert item.title == "example/new-agent (420 stars)"
    assert item.profile == "pangmen-ai-tech-radar"
    assert item.metadata["stars"] == 420
    assert item.metadata["topics"] == ["ai", "agents"]
    assert item.metadata["source_shadow"] is True
    _, kwargs = client.get.await_args
    assert kwargs["params"]["sort"] == "stars"
    assert kwargs["params"]["per_page"] == 20
    assert "topic:artificial-intelligence" in kwargs["params"]["q"]
    assert "created:>=2026-08-02" in kwargs["params"]["q"]
    assert "pushed:>=2026-09-01" in kwargs["params"]["q"]
    assert "stars:>=50" in kwargs["params"]["q"]


def test_github_reports_each_source_health_independently() -> None:
    client = AsyncMock()
    broken = MagicMock()
    broken.raise_for_status.side_effect = RuntimeError("repository unavailable")
    healthy = _response([])
    client.get.side_effect = [broken, healthy]
    sources = [
        GitHubSourceConfig(
            type="repo_releases",
            owner="missing",
            repo="repo",
        ),
        GitHubSourceConfig(
            type="repo_releases",
            owner="openai",
            repo="openai-agents-python",
        ),
    ]
    scraper = GitHubScraper(sources, client)

    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert scraper.last_source_results == [
        {
            "source_id": "github:repo_releases:missing/repo",
            "status": "failed",
            "reason_code": "transport_error",
            "detail": "RuntimeError: repository unavailable",
            "source_type": "repo_releases",
        },
        {
            "source_id": "github:repo_releases:openai/openai-agents-python",
            "status": "healthy",
            "reason_code": "healthy",
            "detail": "",
            "source_type": "repo_releases",
        },
    ]


def test_github_invalid_search_schema_is_reported() -> None:
    client = AsyncMock()
    client.get.return_value = _response({"unexpected": []})
    source = GitHubSourceConfig(type="repo_search", query="topic:ai")
    scraper = GitHubScraper([source], client)

    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert scraper.last_source_results[0]["status"] == "failed"
    assert scraper.last_source_results[0]["reason_code"] == "schema_error"


def test_release_rate_limit_falls_back_to_official_atom() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(403, text="rate limit exceeded")
        return httpx.Response(
            200,
            text="""
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>tag:github.com,2008:Repository/1/v0.9.0</id>
                <updated>2026-09-02T08:00:00Z</updated>
                <title>v0.9.0</title>
                <link rel="alternate" href="https://github.com/openai/openai-agents-python/releases/tag/v0.9.0" />
                <content type="html">Release notes</content>
                <author><name>OpenAI</name></author>
              </entry>
            </feed>
            """,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = GitHubSourceConfig(
        type="repo_releases", owner="openai", repo="openai-agents-python"
    )
    scraper = GitHubScraper([source], client)

    items = asyncio.run(scraper.fetch(SINCE))
    asyncio.run(client.aclose())

    assert len(items) == 1
    assert items[0].metadata["source_fallback"] == "release_atom"
    assert scraper.last_source_results[0]["status"] == "degraded"
    assert scraper.last_source_results[0]["reason_code"] == "fallback_used"
    assert [str(request.url) for request in requests] == [
        "https://api.github.com/repos/openai/openai-agents-python/releases",
        "https://github.com/openai/openai-agents-python/releases.atom",
    ]


def test_shadow_github_sources_are_not_collected_without_shadow_mode(monkeypatch) -> None:
    from types import SimpleNamespace

    from src.orchestrator import HorizonOrchestrator

    orchestrator = object.__new__(HorizonOrchestrator)
    sources = [
        GitHubSourceConfig(
            type="repo_search",
            query="topic:ai",
            shadow=True,
        )
    ]
    orchestrator.config = SimpleNamespace(
        collection=SimpleNamespace(
            source_shadow_enabled=False,
            engagement_tracking=SimpleNamespace(enabled=False),
        ),
        sources=SimpleNamespace(github=sources),
    )

    assert orchestrator._enabled_github_sources() == []


def test_shadow_github_sources_are_collected_in_shadow_mode() -> None:
    from types import SimpleNamespace

    from src.orchestrator import HorizonOrchestrator

    orchestrator = object.__new__(HorizonOrchestrator)
    source = GitHubSourceConfig(
        type="repo_search",
        query="topic:ai",
        shadow=True,
    )
    orchestrator.config = SimpleNamespace(
        collection=SimpleNamespace(source_shadow_enabled=True),
        sources=SimpleNamespace(github=[source]),
    )

    assert orchestrator._enabled_github_sources() == [source]
