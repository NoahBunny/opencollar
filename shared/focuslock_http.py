# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock HTTP Helpers — JSON response mixin for BaseHTTPRequestHandler.

Provides a clean respond_json() method that handles Content-Type,
Content-Length, optional CORS headers, and body encoding.
"""

import json


class JSONResponseMixin:
    """Mixin for BaseHTTPRequestHandler that adds JSON response helpers.

    Usage:
        class MyHandler(JSONResponseMixin, BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond_json(200, {"status": "ok"})
    """

    def respond_json(self, code, data, cors=False):
        """Send a JSON response with proper headers.

        Args:
            code: HTTP status code.
            data: Dict/list to serialize as JSON.
            cors: If True, add Access-Control-Allow-Origin: * header.
        """
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Clickjacking protection — deny framing regardless of origin
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
