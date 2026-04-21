"""Tests for shared/focuslock_http.py — JSONResponseMixin."""

import io
import json

from focuslock_http import JSONResponseMixin


class _FakeHandler(JSONResponseMixin):
    """Minimal stand-in for BaseHTTPRequestHandler to exercise respond_json."""

    def __init__(self):
        self.status_code = None
        self.headers = []  # list of (name, value)
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status_code = code

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        pass


class TestRespondJson:
    def test_status_code_set(self):
        h = _FakeHandler()
        h.respond_json(200, {"ok": True})
        assert h.status_code == 200

    def test_body_is_valid_json(self):
        h = _FakeHandler()
        h.respond_json(200, {"key": "value", "count": 42})
        body = h.wfile.getvalue().decode()
        assert json.loads(body) == {"key": "value", "count": 42}

    def test_content_length_matches_body(self):
        h = _FakeHandler()
        h.respond_json(200, {"x": "hello"})
        body = h.wfile.getvalue()
        headers = dict(h.headers)
        assert int(headers["Content-Length"]) == len(body)

    def test_json_content_type(self):
        h = _FakeHandler()
        h.respond_json(200, {})
        headers = dict(h.headers)
        assert headers["Content-Type"] == "application/json"

    def test_security_headers_always_present(self):
        h = _FakeHandler()
        h.respond_json(200, {})
        names = [n for n, _ in h.headers]
        assert "X-Frame-Options" in names
        assert "X-Content-Type-Options" in names
        headers = dict(h.headers)
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_cors_opt_in_only(self):
        h = _FakeHandler()
        h.respond_json(200, {}, cors=False)
        names = [n for n, _ in h.headers]
        assert "Access-Control-Allow-Origin" not in names

    def test_cors_enabled(self):
        h = _FakeHandler()
        h.respond_json(200, {}, cors=True)
        headers = dict(h.headers)
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_list_payload(self):
        h = _FakeHandler()
        h.respond_json(200, [1, 2, 3])
        assert json.loads(h.wfile.getvalue().decode()) == [1, 2, 3]

    def test_error_status(self):
        h = _FakeHandler()
        h.respond_json(403, {"error": "forbidden"})
        assert h.status_code == 403
        assert json.loads(h.wfile.getvalue().decode()) == {"error": "forbidden"}


# ── Audit C1: direct-post canonicalization parity ──
#
# The Collar's SigVerifier.canonicalize (slave), VaultCrypto.canonicalizeDirectPost
# (controller), and collarCanonicalize (Bunny Tasker) must all produce
# byte-for-byte identical output for a given (path, body, ts, nonce). Drift
# breaks signature verification silently.
#
# This Python reference is the written spec. When touching any of the three Java
# copies, run these tests and add a new vector if the change is semantic.


def _c1_urlenc(s: str) -> str:
    """Matches Java URLEncoder.encode(s, 'UTF-8').replace('+', '%20')."""
    out = []
    for b in s.encode("utf-8"):
        if (
            (0x41 <= b <= 0x5A)  # A-Z
            or (0x61 <= b <= 0x7A)  # a-z
            or (0x30 <= b <= 0x39)  # 0-9
            or b in (0x2E, 0x2D, 0x2A, 0x5F)  # . - * _
        ):
            out.append(chr(b))
        elif b == 0x20:
            out.append("%20")
        else:
            out.append(f"%{b:02X}")
    return "".join(out)


def _c1_encode_value(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return str(v)
    return _c1_urlenc(str(v))


def c1_canonicalize(path: str, body: str, ts: int, nonce: str) -> str:
    """Reference canonicalizer. The three Java copies must match this byte-for-byte."""
    import json as _json

    params_str = None
    if body and body.strip().startswith("{"):
        try:
            obj = _json.loads(body)
            parts = []
            for k in sorted(obj.keys()):
                v = obj[k]
                if v is None:
                    continue
                parts.append(f"{_c1_urlenc(k)}={_c1_encode_value(v)}")
            params_str = "&".join(parts)
        except _json.JSONDecodeError:
            params_str = None
    if params_str is None:
        params_str = f"_raw={_c1_urlenc(body or '')}"
    return f"focusctl|{path}|{ts}|{nonce}|{params_str}"


class TestDirectPostCanonicalize:
    """Golden vectors pinning the Audit C1 signed-payload format."""

    def test_simple_lock_body(self):
        got = c1_canonicalize(
            "/api/lock",
            '{"duration_min":60,"mode":"basic"}',
            1_700_000_000_000,
            "abc12345",
        )
        assert got == "focusctl|/api/lock|1700000000000|abc12345|duration_min=60&mode=basic"

    def test_empty_body_signs_raw(self):
        got = c1_canonicalize("/api/unlock", "", 1_700_000_000_000, "abc12345")
        assert got == "focusctl|/api/unlock|1700000000000|abc12345|_raw="

    def test_non_json_body_signs_raw(self):
        got = c1_canonicalize("/api/speak", "hello world", 111, "nonce")
        assert got == "focusctl|/api/speak|111|nonce|_raw=hello%20world"

    def test_keys_are_sorted_lexicographically(self):
        got = c1_canonicalize(
            "/api/lock",
            '{"zebra":1,"apple":2,"mango":3}',
            0,
            "n",
        )
        assert got == "focusctl|/api/lock|0|n|apple=2&mango=3&zebra=1"

    def test_booleans_become_0_or_1(self):
        got = c1_canonicalize(
            "/api/lock",
            '{"shame":true,"silent":false}',
            0,
            "n",
        )
        assert got == "focusctl|/api/lock|0|n|shame=1&silent=0"

    def test_integral_float_loses_decimal(self):
        # JSON "3.0" parses to float in Python and Double in Java; both must emit "3".
        got = c1_canonicalize("/api/set-volume", '{"level":3.0}', 0, "n")
        assert got == "focusctl|/api/set-volume|0|n|level=3"

    def test_non_integral_float_preserves(self):
        got = c1_canonicalize("/api/set-geofence", '{"lat":40.7128}', 0, "n")
        assert got == "focusctl|/api/set-geofence|0|n|lat=40.7128"

    def test_null_values_omitted(self):
        got = c1_canonicalize(
            "/api/lock",
            '{"duration_min":60,"message":null,"mode":"basic"}',
            0,
            "n",
        )
        assert got == "focusctl|/api/lock|0|n|duration_min=60&mode=basic"

    def test_url_encodes_special_chars_in_values(self):
        got = c1_canonicalize(
            "/api/message",
            '{"text":"hi & bye | done"}',
            0,
            "n",
        )
        # & pipe space all %-encoded in values per Java URLEncoder + .replace("+","%20")
        assert got == "focusctl|/api/message|0|n|text=hi%20%26%20bye%20%7C%20done"

    def test_unicode_value_utf8_encoded(self):
        got = c1_canonicalize(
            "/api/message",
            '{"text":"héllo"}',
            0,
            "n",
        )
        # é = U+00E9, UTF-8 = c3 a9 → %C3%A9
        assert got == "focusctl|/api/message|0|n|text=h%C3%A9llo"

    def test_path_is_not_encoded(self):
        # Path is emitted verbatim — callers control it, not an attacker.
        got = c1_canonicalize("/api/add-paywall", '{"amount":500}', 42, "nn")
        assert got == "focusctl|/api/add-paywall|42|nn|amount=500"

    def test_tampered_body_produces_different_canonical(self):
        a = c1_canonicalize("/api/lock", '{"duration_min":60}', 0, "n")
        b = c1_canonicalize("/api/lock", '{"duration_min":61}', 0, "n")
        assert a != b
        # Spot the one byte that changes.
        assert a.endswith("=60")
        assert b.endswith("=61")

    def test_different_path_different_canonical(self):
        a = c1_canonicalize("/api/lock", "{}", 0, "n")
        b = c1_canonicalize("/api/unlock", "{}", 0, "n")
        assert a != b

    def test_different_ts_different_canonical(self):
        a = c1_canonicalize("/api/lock", "{}", 100, "n")
        b = c1_canonicalize("/api/lock", "{}", 101, "n")
        assert a != b

    def test_different_nonce_different_canonical(self):
        a = c1_canonicalize("/api/lock", "{}", 0, "nonceA")
        b = c1_canonicalize("/api/lock", "{}", 0, "nonceB")
        assert a != b

    def test_protocol_tag_pins_cross_protocol_replay(self):
        # A Lion-signed mesh-order blob canonicalJson lacks the "focusctl|" prefix,
        # so a signature captured from a vault blob cannot be replayed as an
        # HTTP direct-post signature (and vice versa).
        got = c1_canonicalize("/api/lock", "{}", 0, "n")
        assert got.startswith("focusctl|")
