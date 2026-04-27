"""Tests for focuslock_ntfy.py — ntfy publish + subscribe thread."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

from focuslock_ntfy import NtfySubscribeThread, ntfy_publish


def _wait_for_calls(mock, count=1, timeout=1.0):
    """Spin briefly until the mock has been called `count` times, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if mock.call_count >= count:
            return
        time.sleep(0.01)


# ── ntfy_publish ──


class TestNtfyPublish:
    def test_posts_to_default_server(self):
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            ntfy_publish("focuslock-mesh1", 42)
            _wait_for_calls(urlopen)
        assert urlopen.call_count == 1
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://ntfy.sh/focuslock-mesh1"

    def test_payload_is_version_only(self):
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            ntfy_publish("topic", 99)
            _wait_for_calls(urlopen)
        req = urlopen.call_args.args[0]
        body = json.loads(req.data.decode())
        assert body == {"v": 99}
        # Zero-knowledge by construction: only "v" appears in the payload
        assert set(body.keys()) == {"v"}

    def test_method_is_post_with_json_content_type(self):
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            ntfy_publish("topic", 1)
            _wait_for_calls(urlopen)
        req = urlopen.call_args.args[0]
        assert req.get_method() == "POST"
        assert req.headers.get("Content-type") == "application/json"

    def test_custom_server_overrides_default(self):
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            ntfy_publish("topic", 5, server="https://ntfy.example.org")
            _wait_for_calls(urlopen)
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://ntfy.example.org/topic"

    def test_trailing_slash_stripped_from_server(self):
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            ntfy_publish("topic", 5, server="https://ntfy.example.org/")
            _wait_for_calls(urlopen)
        req = urlopen.call_args.args[0]
        # No double slash
        assert req.full_url == "https://ntfy.example.org/topic"

    def test_http_exception_swallowed(self):
        """Publish failure must never propagate — gossip is the fallback."""
        with patch("focuslock_ntfy.urllib.request.urlopen", side_effect=OSError("connection refused")):
            # Should not raise
            ntfy_publish("topic", 1)
            time.sleep(0.05)

    def test_thread_is_daemon(self):
        """Publish thread must be daemon so the process can exit cleanly."""
        with patch("focuslock_ntfy.urllib.request.urlopen") as urlopen:
            # Block urlopen to keep the thread alive long enough to inspect
            block = threading.Event()
            urlopen.side_effect = lambda *a, **kw: block.wait(timeout=1) or MagicMock()

            before = {t.ident for t in threading.enumerate()}
            ntfy_publish("topic", 1)
            time.sleep(0.05)
            new_threads = [t for t in threading.enumerate() if t.ident not in before]
            assert any(t.daemon for t in new_threads)
            block.set()


# ── NtfySubscribeThread ──


class TestNtfySubscribeThreadInit:
    def test_init_default_server(self):
        sub = NtfySubscribeThread("my-topic", lambda v: None)
        assert sub.topic == "my-topic"
        assert sub.server == "https://ntfy.sh"
        assert sub.daemon is True
        assert sub.name == "ntfy-subscribe"

    def test_init_custom_server_strip_trailing_slash(self):
        sub = NtfySubscribeThread("topic", lambda v: None, server="https://my-ntfy.example/")
        assert sub.server == "https://my-ntfy.example"

    def test_init_backoff_starts_at_one_second(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        assert sub._backoff == 1

    def test_stop_sets_event(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        assert not sub._stop_event.is_set()
        sub.stop()
        assert sub._stop_event.is_set()


class TestNtfySubscribeRun:
    def test_run_exits_when_stop_event_already_set(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub.stop()
        # Should not enter the while loop at all
        with patch.object(sub, "_stream_messages") as stream:
            sub.run()
        assert stream.call_count == 0

    def test_normal_stream_return_resets_backoff(self):
        """When _stream_messages returns normally, backoff returns to 1."""
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub._backoff = 32  # pretend we'd been failing

        call_count = {"n": 0}

        def fake_stream():
            call_count["n"] += 1
            if call_count["n"] >= 1:
                sub.stop()  # exit after one iteration

        with patch.object(sub, "_stream_messages", side_effect=fake_stream):
            sub.run()
        assert sub._backoff == 1

    def test_exception_doubles_backoff_capped_at_60(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub._backoff = 1
        backoffs = []

        def fail_then_stop():
            backoffs.append(sub._backoff)
            if len(backoffs) >= 4:
                sub.stop()
            raise OSError("boom")

        # Patch wait to return False (didn't time out → continue loop) until we stop
        with patch.object(sub, "_stream_messages", side_effect=fail_then_stop):
            with patch.object(sub._stop_event, "wait", return_value=False) as wait_mock:
                # When stop() is called inside the failing func on iteration 4,
                # wait() will be patched to return False, but the next loop check
                # sees _stop_event.is_set() and exits.
                sub.run()

        # Backoff sequence: 1, 2, 4, 8
        assert backoffs == [1, 2, 4, 8]
        # wait() called once per failed iteration
        assert wait_mock.call_count == 4

    def test_exception_backoff_caps_at_60(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub._backoff = 32

        def fail_once():
            sub.stop()
            raise OSError("boom")

        with patch.object(sub, "_stream_messages", side_effect=fail_once):
            with patch.object(sub._stop_event, "wait", return_value=False):
                sub.run()
        assert sub._backoff == 60

    def test_exception_backoff_does_not_exceed_60(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub._backoff = 60  # already at cap

        def fail_once():
            sub.stop()
            raise OSError("boom")

        with patch.object(sub, "_stream_messages", side_effect=fail_once):
            with patch.object(sub._stop_event, "wait", return_value=False):
                sub.run()
        assert sub._backoff == 60

    def test_stop_during_backoff_wait_exits_loop(self):
        """If stop_event.wait() returns True (event set during backoff), break out."""
        sub = NtfySubscribeThread("topic", lambda v: None)
        with patch.object(sub, "_stream_messages", side_effect=OSError("boom")):
            with patch.object(sub._stop_event, "wait", return_value=True) as wait_mock:
                sub.run()
        # Run did one failed iteration, then wait returned True → break
        assert wait_mock.call_count == 1


# ── _stream_messages ──


def _line_iter(*lines):
    """Mock urlopen response: an iterable of bytes lines."""
    resp = MagicMock()
    resp.__iter__ = lambda self: iter([b + b"\n" for b in lines])
    return resp


class TestStreamMessages:
    def test_calls_on_wake_with_version_from_message(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        msg = json.dumps({"id": "abc1", "event": "message", "message": '{"v": 7}'}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(msg)):
            sub._stream_messages()
        assert wakes == [7]
        # since cursor advanced to the last message id
        assert sub._since == "abc1"

    def test_url_includes_since_cursor(self):
        sub = NtfySubscribeThread("topic", lambda v: None, server="https://ntfy.example")
        sub._since = "12345"
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter()) as urlopen:
            sub._stream_messages()
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://ntfy.example/topic/json?since=12345"

    def test_skips_empty_lines(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        msg = json.dumps({"event": "message", "message": '{"v": 1}'}).encode()
        with patch(
            "focuslock_ntfy.urllib.request.urlopen",
            return_value=_line_iter(b"", b"   ", msg, b""),
        ):
            sub._stream_messages()
        assert wakes == [1]

    def test_skips_malformed_json_lines(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        good = json.dumps({"event": "message", "message": '{"v": 99}'}).encode()
        with patch(
            "focuslock_ntfy.urllib.request.urlopen",
            return_value=_line_iter(b"not json{", good),
        ):
            sub._stream_messages()
        assert wakes == [99]

    def test_skips_open_and_keepalive_events(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        open_ev = json.dumps({"id": "o1", "event": "open"}).encode()
        keepalive = json.dumps({"id": "k1", "event": "keepalive"}).encode()
        msg = json.dumps({"id": "m1", "event": "message", "message": '{"v": 5}'}).encode()
        with patch(
            "focuslock_ntfy.urllib.request.urlopen",
            return_value=_line_iter(open_ev, keepalive, msg),
        ):
            sub._stream_messages()
        assert wakes == [5]
        # since cursor still advances on open/keepalive (they have ids)
        assert sub._since == "m1"

    def test_handles_message_without_body(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        # No "message" field
        no_body = json.dumps({"id": "x", "event": "message"}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(no_body)):
            sub._stream_messages()
        assert wakes == []  # nothing to wake on

    def test_handles_unparseable_body(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        bad_body = json.dumps({"id": "x", "event": "message", "message": "not json"}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(bad_body)):
            sub._stream_messages()
        assert wakes == []

    def test_handles_body_that_parses_to_non_dict(self):
        """If `message` parses to e.g. a number or list, .get('v') raises AttributeError."""
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        # body=42 (int) parses successfully but has no .get('v')
        weird = json.dumps({"id": "x", "event": "message", "message": "42"}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(weird)):
            sub._stream_messages()
        assert wakes == []

    def test_skips_message_with_no_version_field(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"
        # body is valid JSON but lacks "v"
        no_v = json.dumps({"id": "x", "event": "message", "message": '{"other": 1}'}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(no_v)):
            sub._stream_messages()
        assert wakes == []

    def test_message_without_id_does_not_advance_since(self):
        sub = NtfySubscribeThread("topic", lambda v: None)
        sub._since = "PREV"
        no_id = json.dumps({"event": "message", "message": '{"v": 1}'}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(no_id)):
            sub._stream_messages()
        # _since unchanged when message has no id
        assert sub._since == "PREV"

    def test_stop_event_breaks_loop_mid_stream(self):
        wakes = []
        sub = NtfySubscribeThread("topic", on_wake=wakes.append)
        sub._since = "0"

        def lines():
            yield json.dumps({"id": "1", "event": "message", "message": '{"v": 1}'}).encode() + b"\n"
            sub.stop()
            yield json.dumps({"id": "2", "event": "message", "message": '{"v": 2}'}).encode() + b"\n"

        resp = MagicMock()
        resp.__iter__ = lambda self: lines()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=resp):
            sub._stream_messages()
        # First message processed; second skipped because stop was set
        assert wakes == [1]

    def test_on_wake_exception_does_not_crash_loop(self):
        wakes = []

        def wake(v):
            wakes.append(v)
            if v == 1:
                raise RuntimeError("downstream broke")

        sub = NtfySubscribeThread("topic", on_wake=wake)
        sub._since = "0"
        m1 = json.dumps({"id": "1", "event": "message", "message": '{"v": 1}'}).encode()
        m2 = json.dumps({"id": "2", "event": "message", "message": '{"v": 2}'}).encode()
        with patch("focuslock_ntfy.urllib.request.urlopen", return_value=_line_iter(m1, m2)):
            sub._stream_messages()
        # Both calls happened — exception in first didn't abort the for-loop
        assert wakes == [1, 2]
