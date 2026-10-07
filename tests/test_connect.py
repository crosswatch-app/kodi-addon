import pytest

from resources.lib import connect
from resources.lib.config import KEY_BASE_URL, KEY_STATUS, KEY_TOKEN
from resources.lib.constants import (
    LINK_AUTOCLOSE_SECONDS,
    PAIR_BAD_ADDRESS,
    PAIR_CERTIFICATE,
    PAIR_CODE,
    PAIR_DISABLED,
    PAIR_DONE,
    PAIR_FAILED,
    PAIR_INVALID_CODE,
    PAIR_RATE_LIMITED,
    PAIR_UNREACHABLE,
    STATUS_NOT_PAIRED,
    STATUS_PAIRED,
)
from resources.lib.pairing import PairResult
from tests.fakes import FakeKodi

STORED = {KEY_BASE_URL: "http://old:8787/webhook/kodiwatcher", KEY_TOKEN: "old-token", KEY_STATUS: "Paired with Old"}


class Exchange:
    def __init__(self, result: PairResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def __call__(self, address: str, code: str) -> PairResult:
        self.calls.append((address, code))
        return self.result


def test_pairing_stores_the_typed_address_the_token_and_the_status():
    kodi = FakeKodi(input_answers=["nas:8787", "abc 234"])
    exchange = Exchange(PairResult("paired", token="tok", instance="Living room", status=200))
    assert connect.pair(kodi, exchange) is True
    assert exchange.calls == [("http://nas:8787", "ABC234")]
    assert kodi.writes == [
        (KEY_TOKEN, "tok"),
        (KEY_BASE_URL, "http://nas:8787/webhook/kodiwatcher"),
        (KEY_STATUS, f"#{STATUS_PAIRED}"),
    ]
    assert kodi.notifications[-1][1] == f"#{PAIR_DONE}"


def test_the_address_is_prefilled_with_the_last_one_used():
    kodi = FakeKodi(settings=dict(STORED), input_answers=["", ""])
    connect.pair(kodi, Exchange(PairResult("failed")))
    assert kodi.input_calls[0][1] == "http://old:8787"


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (PairResult("invalid_code", status=401), f"#{PAIR_INVALID_CODE}"),
        (PairResult("rate_limited", status=429), f"#{PAIR_RATE_LIMITED}"),
        (PairResult("unreachable"), f"#{PAIR_UNREACHABLE}"),
        (PairResult("failed", status=404), f"#{PAIR_FAILED}"),
        (PairResult("disabled", status=200), f"#{PAIR_DISABLED}"),
        (PairResult("certificate"), f"#{PAIR_CERTIFICATE}"),
    ],
)
def test_a_failure_says_why_and_changes_no_setting(result, message):
    kodi = FakeKodi(settings=dict(STORED), input_answers=["nas:8787", "ABC234"])
    assert connect.pair(kodi, Exchange(result)) is False
    assert kodi.writes == []
    assert kodi.settings == STORED
    # A dialog, not a toast: Kodi's toast cuts these messages off.
    assert kodi.ok_calls[-1][1] == message
    assert kodi.notifications == []


def test_cancelling_the_address_asks_nothing_more():
    kodi = FakeKodi(input_answers=[""])
    exchange = Exchange(PairResult("paired", token="t"))
    assert connect.pair(kodi, exchange) is False
    assert len(kodi.input_calls) == 1
    assert exchange.calls == []


def test_an_impossible_address_is_refused_before_the_code_is_asked():
    kodi = FakeKodi(input_answers=["ftp://nas"])
    assert connect.pair(kodi, Exchange(PairResult("paired", token="t"))) is False
    assert kodi.ok_calls[-1][1] == f"#{PAIR_BAD_ADDRESS}"
    assert [heading for heading, _ in kodi.input_calls] == [kodi.input_calls[0][0]]


def test_cancelling_the_code_sends_nothing():
    kodi = FakeKodi(input_answers=["nas:8787", "  "])
    exchange = Exchange(PairResult("paired", token="t"))
    assert connect.pair(kodi, exchange) is False
    assert kodi.input_calls[1][0] == f"#{PAIR_CODE}"
    assert exchange.calls == []


LINK = {"action": "link", "url": "http://nas:8787/webhook/kodiwatcher", "code": "abc 234"}
PAIRED = PairResult("paired", token="tok", instance="Living room", status=200)


def test_link_asks_with_a_timeout_then_redeems_the_code_like_a_typed_one():
    kodi = FakeKodi(confirm_answer=True)
    exchange = Exchange(PAIRED)
    assert connect.link(kodi, LINK, exchange) is True
    assert kodi.confirm_calls[0][2] == LINK_AUTOCLOSE_SECONDS
    assert exchange.calls == [("http://nas:8787", "ABC234")]
    assert kodi.writes == [
        (KEY_TOKEN, "tok"),
        (KEY_BASE_URL, "http://nas:8787/webhook/kodiwatcher"),
        (KEY_STATUS, f"#{STATUS_PAIRED}"),
    ]
    assert kodi.notifications[-1][1] == f"#{PAIR_DONE}"


def test_link_answered_no_or_timed_out_never_spends_the_code():
    kodi = FakeKodi(settings=dict(STORED), confirm_answer=False)
    exchange = Exchange(PAIRED)
    assert connect.link(kodi, LINK, exchange) is False
    assert exchange.calls == []
    assert kodi.writes == []


def test_a_link_code_that_fails_says_why_and_changes_nothing():
    kodi = FakeKodi(settings=dict(STORED), confirm_answer=True)
    assert connect.link(kodi, LINK, Exchange(PairResult("invalid_code", status=401))) is False
    assert kodi.ok_calls[-1][1] == f"#{PAIR_INVALID_CODE}"
    assert kodi.writes == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "link"},
        {"action": "link", "url": "http://nas/webhook/kodiwatcher"},
        {"action": "link", "code": "ABC234"},
        {"action": "link", "url": "file:///etc/passwd", "code": "ABC234"},
        {"action": "link", "url": "http://nas/webhook/kodiwatcher?token=tok", "code": "ABC234"},
        # The token form CrossWatch sent before contract 1.4: never accepted as a credential.
        {"action": "link", "url": "http://nas/webhook/kodiwatcher", "token": "tok"},
    ],
)
def test_malformed_link_arguments_never_reach_a_dialog(arguments):
    kodi = FakeKodi(confirm_answer=True)
    exchange = Exchange(PAIRED)
    assert connect.link(kodi, arguments, exchange) is False
    assert kodi.confirm_calls == []
    assert exchange.calls == []
    assert kodi.writes == []


def test_unpair_clears_url_token_and_status_after_a_yes():
    kodi = FakeKodi(settings=dict(STORED), confirm_answer=True)
    assert connect.unpair(kodi) is True
    assert kodi.writes == [(KEY_TOKEN, ""), (KEY_BASE_URL, ""), (KEY_STATUS, f"#{STATUS_NOT_PAIRED}")]


def test_unpair_answered_no_changes_nothing():
    kodi = FakeKodi(settings=dict(STORED), confirm_answer=False)
    assert connect.unpair(kodi) is False
    assert kodi.writes == []


def test_arguments_are_split_on_the_first_equals_sign():
    assert connect.parse_arguments(["action=link", "url=http://h/?a=b", "stray"]) == {
        "action": "link",
        "url": "http://h/?a=b",
    }
