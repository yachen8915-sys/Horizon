from src.ai.tokens import get_usage_snapshot, record_usage, reset_usage


def test_usage_snapshot_counts_calls_and_tokens_by_provider() -> None:
    reset_usage()

    record_usage("openai", input_tokens=100, output_tokens=20)
    record_usage("openai", input_tokens=50, output_tokens=10)
    record_usage("anthropic", input_tokens=25, output_tokens=5)

    usage = get_usage_snapshot()

    assert usage.total_calls == 3
    assert usage.total_input_tokens == 175
    assert usage.total_output_tokens == 35
    assert usage.per_provider["openai"].calls == 2
    assert usage.per_provider["anthropic"].calls == 1

    reset_usage()
