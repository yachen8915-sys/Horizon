from datetime import datetime, timezone

import httpx

from src.models import HackerNewsConfig
from src.scrapers.hackernews import HackerNewsScraper


def test_hackernews_exposes_score_and_comment_count_as_engagement_metrics() -> None:
    scraper = HackerNewsScraper(
        HackerNewsConfig(enabled=True),
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)),
    )
    story = {
        "id": 123,
        "title": "AI workflow launch",
        "url": "https://example.com/launch",
        "by": "builder",
        "time": int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()),
        "score": 320,
        "descendants": 88,
        "type": "story",
    }

    item = scraper._parse_story(story, [])

    assert item.metadata["engagement"] == {"points": 320, "comments": 88}
