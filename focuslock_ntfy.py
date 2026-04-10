# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock ntfy Push Notifications

Lightweight push notifications via ntfy (https://ntfy.sh or self-hosted).
Used as a latency optimization — gossip remains the consistency layer.

ntfy carries ONLY version numbers ("wake up, v42 available"), never order
content. Zero-knowledge by construction: the ntfy server learns nothing
beyond timing and a monotonic counter.
"""

import json
import threading
import time
import urllib.request
import urllib.error


def ntfy_publish(topic: str, version: int,
                 server: str = "https://ntfy.sh") -> None:
    """Publish a wake-up signal to an ntfy topic. Fire-and-forget in a thread.

    Body is just {"v": <version>}. Runs asynchronously — never blocks the caller.
    No retry — gossip is the fallback.
    """
    def _post():
        url = f"{server.rstrip('/')}/{topic}"
        payload = json.dumps({"v": version}).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
            print(f"[ntfy] Published v{version} to {topic}")
        except Exception as e:
            print(f"[ntfy] Publish failed: {e}")

    threading.Thread(target=_post, daemon=True).start()


class NtfySubscribeThread(threading.Thread):
    """Daemon thread that subscribes to an ntfy topic via HTTP long-poll.

    Calls on_wake(version) when a message arrives. Reconnects with
    exponential backoff on failure. Gossip continues independently.
    """

    def __init__(self, topic: str, on_wake, server: str = "https://ntfy.sh"):
        super().__init__(daemon=True, name="ntfy-subscribe")
        self.topic = topic
        self.server = server.rstrip("/")
        self.on_wake = on_wake
        self._stop_event = threading.Event()
        self._backoff = 1  # seconds, doubles on failure up to 60

    def stop(self):
        self._stop_event.set()

    def run(self):
        print(f"[ntfy] Subscribing to {self.server}/{self.topic}")
        # Start with since=60s ago — catches very recent messages without
        # replaying full history. Gossip handles anything older.
        self._since = str(int(time.time()) - 60)

        while not self._stop_event.is_set():
            try:
                self._stream_messages()
                # Normal return (server closed connection) — reset backoff
                self._backoff = 1
            except Exception as e:
                print(f"[ntfy] Subscribe error: {e}")
                wait = self._backoff
                self._backoff = min(self._backoff * 2, 60)
                if self._stop_event.wait(wait):
                    break

    def _stream_messages(self):
        """Stream the ntfy JSON feed. Blocks until disconnected or stopped.

        Uses HTTP streaming (no poll=1) so the connection stays open and
        messages arrive in real time. Tracks the last message ID for
        seamless reconnection.
        """
        url = f"{self.server}/{self.topic}/json?since={self._since}"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=90)

        for line in resp:
            if self._stop_event.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Track last message ID for reconnection resumption
            msg_id = msg.get("id")
            if msg_id:
                self._since = msg_id

            # Skip non-message events (open, keepalive)
            if msg.get("event") in ("open", "keepalive"):
                continue

            # Extract version from message body
            version = None
            body = msg.get("message", "")
            if body:
                try:
                    data = json.loads(body)
                    version = data.get("v")
                except (json.JSONDecodeError, AttributeError):
                    pass

            if version is not None:
                print(f"[ntfy] Received wake-up v{version}")
                try:
                    self.on_wake(version)
                except Exception as e:
                    print(f"[ntfy] on_wake error: {e}")
