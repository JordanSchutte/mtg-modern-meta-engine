import httpx
import pytest

from mtgmeta.sources.melee.http import PoliteClient, RateLimitedError


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, s):
        self.slept.append(s)
        self.now += s

    def clock(self):
        return self.now


def make(handler, **kw):
    clock = FakeClock()
    client = PoliteClient(
        "me@example.com",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock.clock,
        **kw,
    )
    return client, clock


def test_requires_contact_email():
    with pytest.raises(ValueError):
        PoliteClient("")


def test_interval_floor_and_spacing():
    client, clock = make(lambda r: httpx.Response(200, text="ok"), interval=0.1)
    assert client.interval == 2.0  # floor enforced
    client.get_text("/a")
    client.get_text("/b")
    assert clock.slept == [2.0]


def test_identifies_itself_and_sends_referer():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": 1})

    client, _ = make(handler)
    client.post_form_json("/x", [("a", "1")], referer="https://melee.gg/Tournament/View/1")
    assert "me@example.com" in seen["user-agent"]
    assert seen["referer"] == "https://melee.gg/Tournament/View/1"
    assert seen["x-requested-with"] == "XMLHttpRequest"


def test_stops_after_repeated_429():
    client, _ = make(lambda r: httpx.Response(429), max_429=3)
    with pytest.raises(RateLimitedError):
        client.get_text("/busy")
    assert client.requests_made == 3


def test_recovers_from_single_429():
    responses = iter([httpx.Response(429), httpx.Response(200, text="fine")])
    client, clock = make(lambda r: next(responses))
    assert client.get_text("/x") == "fine"
    assert max(clock.slept) >= 2.5 * 4  # backed off well past the normal interval
