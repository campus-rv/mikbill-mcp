from mb_mcp.gating import ApprovalStore


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_token_is_single_use():
    store = ApprovalStore()
    token = store.issue("mb_delete_user", {"uid": 1})
    assert store.consume(token, "mb_delete_user", {"uid": 1})
    assert not store.consume(token, "mb_delete_user", {"uid": 1})


def test_token_is_bound_to_params():
    store = ApprovalStore()
    token = store.issue("mb_delete_user", {"uid": 1})
    assert not store.consume(token, "mb_delete_user", {"uid": 2})
    # a wrong attempt burns the token
    assert not store.consume(token, "mb_delete_user", {"uid": 1})


def test_token_is_bound_to_tool():
    store = ApprovalStore()
    token = store.issue("mb_delete_user", {"uid": 1})
    assert not store.consume(token, "mb_disconnect_user", {"uid": 1})


def test_token_expires():
    clock = Clock()
    store = ApprovalStore(now=clock, ttl=300)
    token = store.issue("t", {"a": 1})
    clock.t = 301
    assert not store.consume(token, "t", {"a": 1})


def test_missing_or_unknown_token():
    store = ApprovalStore()
    assert not store.consume(None, "t", {})
    assert not store.consume("nope", "t", {})


def test_param_order_does_not_matter():
    store = ApprovalStore()
    token = store.issue("t", {"a": 1, "b": 2})
    assert store.consume(token, "t", {"b": 2, "a": 1})
