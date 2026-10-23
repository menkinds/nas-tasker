import pytest

from nas_tasker.errors import ToolError
from nas_tasker.ratelimit import RateLimiter
from nas_tasker.tokens import OneTimeStore, TokenExpired, TokenInvalid, token_hash
from nas_tasker.validation import check_share_name, check_tail_lines, check_task_name


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.mark.parametrize("name", ["bot-gbrain-rebuild", "bot-a", "bot-" + "a" * 64])
def test_valid_task_names(name):
    assert check_task_name(name) == name


@pytest.mark.parametrize(
    "name,code",
    [
        ("gbrain-rebuild", "NOT_ALLOWED"),
        ("Bot-x", "NOT_ALLOWED"),
        ("bot-", "INVALID_INPUT"),
        ("bot-X", "INVALID_INPUT"),
        ("bot-a b", "INVALID_INPUT"),
        ("bot-../etc", "INVALID_INPUT"),
        ("bot-a/b", "INVALID_INPUT"),
        ("bot-a\n", "INVALID_INPUT"),
        ("bot-" + "a" * 65, "INVALID_INPUT"),
        (123, "INVALID_INPUT"),
        (None, "INVALID_INPUT"),
    ],
)
def test_invalid_task_names(name, code):
    with pytest.raises(ToolError) as exc:
        check_task_name(name)
    assert exc.value.code == code


def test_share_must_match_allowlist_exactly():
    allow = frozenset({"secret"})
    assert check_share_name("secret", allow) == "secret"
    for bad in ["Secret", "secret ", "secret/..", "other"]:
        with pytest.raises(ToolError) as exc:
            check_share_name(bad, allow)
        assert exc.value.code == "SHARE_NOT_ALLOWED"
    with pytest.raises(ToolError) as exc:
        check_share_name(["secret"], allow)
    assert exc.value.code == "INVALID_INPUT"


def test_tail_lines():
    assert check_tail_lines(None) == 100
    assert check_tail_lines(1000) == 1000
    for bad in [0, 1001, -1, True, "10", 1.5]:
        with pytest.raises(ToolError):
            check_tail_lines(bad)


def test_one_time_store_consumes_once():
    store = OneTimeStore(300, Clock())
    token, expires_at = store.issue({"share": "s"})
    assert expires_at == 1300.0
    assert len(token) >= 22  # >= 128 bits in urlsafe base64
    assert store.take(token, lambda p: True) == {"share": "s"}
    with pytest.raises(TokenInvalid):
        store.take(token, lambda p: True)


def test_one_time_store_expiry_and_mismatch():
    clock = Clock()
    store = OneTimeStore(300, clock)
    token, _ = store.issue({"share": "s"})
    with pytest.raises(TokenInvalid):
        store.take(token, lambda p: False)
    clock.now += 300
    with pytest.raises(TokenExpired):
        store.take(token, lambda p: True)
    with pytest.raises(TokenInvalid):
        store.take(None, lambda p: True)


def test_token_hash_is_short_and_stable():
    assert token_hash("abc") == token_hash("abc")
    assert len(token_hash("abc")) == 12
    assert "abc" not in token_hash("abc")


def test_rate_limiter_window():
    clock = Clock()
    limiter = RateLimiter({"run_task": (2, 60.0), "*": (100, 60.0)}, clock)
    limiter.check("a", "run_task")
    limiter.check("a", "run_task")
    with pytest.raises(ToolError) as exc:
        limiter.check("a", "run_task")
    assert exc.value.code == "RATE_LIMITED"
    limiter.check("b", "run_task")  # other caller unaffected
    clock.now += 60
    limiter.check("a", "run_task")


def test_auth_failure_limiter_blocks_then_releases():
    from nas_tasker.ratelimit import AuthFailureLimiter

    clock = Clock()
    limiter = AuthFailureLimiter(max_failures=3, window=60.0, block=120.0, clock=clock)
    for _ in range(3):
        limiter.check("1.2.3.4")
        limiter.failed("1.2.3.4")
    with pytest.raises(ToolError) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.code == "RATE_LIMITED"
    limiter.check("5.6.7.8")  # other IPs unaffected
    clock.now += 120
    limiter.check("1.2.3.4")
