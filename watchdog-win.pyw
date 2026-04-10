# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Watchdog — Windows
Monitors the main collar process and restarts it if killed.
Runs hidden (.pyw = no console window).
The main collar process monitors this watchdog in return (mutual watchdog).
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

COLLAR_HEALTH_URL = "http://127.0.0.1:8436/health"
WATCHDOG_PORT = 8437
CHECK_INTERVAL = 3  # seconds
RESTART_DELAY = 1

INSTALL_DIR = r"C:\focuslock"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_collar_script():
    """Find the main collar script."""
    for path in [
        os.path.join(INSTALL_DIR, "focuslock-desktop-win.py"),
        os.path.join(SCRIPT_DIR, "focuslock-desktop-win.py"),
    ]:
        if os.path.exists(path):
            return path
    return None


def is_collar_alive():
    """Check if the main collar process is responding."""
    try:
        req = urllib.request.Request(COLLAR_HEALTH_URL)
        resp = urllib.request.urlopen(req, timeout=2)
        data = json.loads(resp.read().decode())
        return data.get("alive", False)
    except:
        return False


def restart_collar():
    """Restart the main collar process."""
    script = find_collar_script()
    if not script:
        return False
    try:
        # Use pythonw to run without console window
        subprocess.Popen(
            ["pythonw", script],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
        return True
    except:
        try:
            # Fallback: try python instead of pythonw
            subprocess.Popen(
                ["python", script],
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            )
            return True
        except:
            return False


class WatchdogHealthHandler(BaseHTTPRequestHandler):
    """Health endpoint so the main collar can check if we're alive."""
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "alive": True,
                "role": "watchdog",
                "pid": os.getpid(),
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass


def main():
    # Start health server in background
    import threading
    try:
        server = HTTPServer(("127.0.0.1", WATCHDOG_PORT), WatchdogHealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except:
        pass  # Port may be in use if another watchdog is running — that's fine

    consecutive_failures = 0

    while True:
        time.sleep(CHECK_INTERVAL)

        if is_collar_alive():
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= 2:  # 2 consecutive failures = ~6s dead
                time.sleep(RESTART_DELAY)
                if not is_collar_alive():  # Double check
                    restarted = restart_collar()
                    if restarted:
                        consecutive_failures = 0
                        time.sleep(5)  # Give it time to start


if __name__ == "__main__":
    main()
