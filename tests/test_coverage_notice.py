import pytest

from src.orchestrator import FetchReport, SourceFetchOutcome


PARTIAL_NOTICE = "热点覆盖不完整：微博来源暂时不可用，抖音及其他热点来源仍正常。"


def health(platform, provider, status):
    return {
        "source_id": f"platform-trends:{platform}:{provider}",
        "status": status,
        "reason_code": "business_error" if status == "failed" else status,
        "detail": "Traceback token=secret raw response retries=3",
    }


def platform_report(*rows):
    return FetchReport([SourceFetchOutcome(
        "Platform Trends", "failure" if all(r["status"] == "failed" for r in rows) else "success",
        provider_health=list(rows),
    )]).to_dict()


def partial_report():
    return platform_report(
        health("weibo", "dailyhot", "failed"),
        health("douyin", "dailyhot", "healthy"),
        health("zhihu", "alapi_tophub", "healthy"),
    )


def notice_for(payload):
    from src.processing.coverage_notice import build_platform_coverage_notice
    return build_platform_coverage_notice(payload)


def test_business_failure_reports_remaining_platforms_without_private_details():
    payload = partial_report()
    assert payload["status"] == "success"  # Aggregate success cannot hide failed providers.
    assert notice_for(payload) == PARTIAL_NOTICE


def test_non_core_platform_failure_is_partial_not_all_hotspots_unavailable():
    notice = notice_for(platform_report(
        health("zhihu", "alapi_tophub", "failed"),
        health("douyin", "dailyhot", "healthy"),
    ))
    assert notice == "热点覆盖不完整：知乎来源暂时不可用，抖音来源仍正常。"


def test_non_platform_feed_failure_does_not_imply_hotspot_coverage_failure():
    payload = partial_report()
    payload["sources"][0]["providers"][0]["status"] = "healthy"
    payload["sources"].append(SourceFetchOutcome("RSS Feeds", "failure", feed_health=[
        {"source_id": "rss:non-core-feed", "status": "failed"},
    ]).to_dict())
    payload["status"] = "partial_failure"
    assert notice_for(payload) is None


def test_total_platform_failure_is_unavailable_even_when_rss_succeeds():
    payload = platform_report(health("weibo", "dailyhot", "failed"),
                              health("douyin", "alapi_tophub", "failed"))
    payload["sources"].append(SourceFetchOutcome("RSS Feeds", "success").to_dict())
    payload["status"] = "partial_failure"
    assert notice_for(payload) == "热点覆盖暂时不可用：当前热点来源均未能正常获取数据。"


def test_same_platform_provider_fallback_does_not_claim_platform_unavailable():
    notice = notice_for(platform_report(health("weibo", "dailyhot", "failed"),
                                        health("weibo", "alapi_tophub", "healthy")))
    assert notice == "热点覆盖不完整：微博部分来源暂时不可用，仍有其他来源正常。"


@pytest.mark.parametrize("status", ["stale", "degraded", "coverage_gap"])
def test_non_failed_health_gaps_are_not_described_as_healthy(status):
    notice = notice_for(platform_report(health("weibo", "dailyhot", status),
                                        health("douyin", "dailyhot", "healthy")))
    assert notice == "热点覆盖不完整：微博来源数据暂不完整，抖音来源仍正常。"


@pytest.mark.parametrize("payload", [None, {}, {"status": "not_attempted", "sources": []},
    platform_report(health("weibo", "dailyhot", "healthy")),
    platform_report(health("weibo", "dailyhot", "disabled")),
    FetchReport([SourceFetchOutcome("Platform Trends", "empty")]).to_dict(),
])
def test_healthy_empty_disabled_and_unattempted_runs_have_no_notice(payload):
    assert notice_for(payload) is None


def test_aggregate_platform_transport_failure_has_safe_notice():
    payload = FetchReport([SourceFetchOutcome("Platform Trends", "failure",
        error="Traceback token=secret raw body")]).to_dict()
    assert notice_for(payload) == "热点覆盖暂时不可用：当前热点来源均未能正常获取数据。"


def test_unknown_source_identifier_is_not_echoed_into_notice():
    notice = notice_for(platform_report(
        health("token=secret", "raw-response", "failed"),
        health("douyin", "dailyhot", "healthy"),
    ))
    assert notice == "热点覆盖不完整：其他热点来源暂时不可用，抖音来源仍正常。"
