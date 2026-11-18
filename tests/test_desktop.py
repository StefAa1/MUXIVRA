"""Desktop shell wiring: token handshake, window URL and the single-instance lock."""
import sys
import types

import pytest

from conftest import _reset
from sshdeck import desktop


@pytest.fixture()
def fresh():
    _reset()
    yield


def test_tokens_are_single_use_and_expire():
    token = desktop.new_token()
    assert desktop.consume_token(token) is True
    assert desktop.consume_token(token) is False
    assert desktop.consume_token("never-issued") is False


def test_run_opens_window_with_one_time_token(monkeypatch, fresh):
    captured = {}

    class FakeServer:
        should_exit = False

    def fake_create_window(title, url, **kwargs):
        captured["title"] = title
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    fake_webview = types.SimpleNamespace(
        create_window=fake_create_window,
        start=lambda **kwargs: captured.setdefault("started", True),
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(desktop, "start_server", lambda port, log_level="warning": FakeServer())
    monkeypatch.setattr(desktop, "wait_ready", lambda port, timeout=25: True)

    rc = desktop.run(port=8899)
    assert rc == 0
    assert captured["title"] == "MUXIVRA"
    assert captured["url"].startswith("http://127.0.0.1:8899/?desktop_token=")
    assert captured["started"] is True
    token = captured["url"].split("desktop_token=", 1)[1]
    assert desktop.consume_token(token) is True, "the URL token must be exchangeable"


def test_second_instance_is_refused(monkeypatch, fresh):
    first = desktop.acquire_instance_lock()
    assert first is not None
    try:
        assert desktop.acquire_instance_lock() is None
    finally:
        first.close()
    second = desktop.acquire_instance_lock()
    assert second is not None
    second.close()
