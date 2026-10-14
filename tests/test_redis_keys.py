from backend.db import keys


def test_keys_are_prefixed_and_distinct():
    names = [
        keys.queue("model-a"),
        keys.rl_requests("model-a", 100),
        keys.rl_tokens("model-a", 100),
        keys.limits("model-a"),
        keys.request("req_1"),
        keys.batch("bat_1"),
        keys.batch_pending("bat_1"),
        keys.STATS,
        keys.CALLBACK_ZSET,
    ]
    assert all(n.startswith(f"{keys.PREFIX}:") for n in names)
    assert len(names) == len(set(names))


def test_models_do_not_share_limiter_keys():
    assert keys.rl_requests("model-a", 1) != keys.rl_requests("model-b", 1)
