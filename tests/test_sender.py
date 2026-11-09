"""Tests the Python sender against a fake receiver that follows docs/PROTOCOL.md.

Run from the repo root:  python -m unittest tests/test_sender.py
This checks the sender and the protocol shape. It does not exercise the Godot
receiver, which needs the editor to run.
"""

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sender"))
import projected_cast  # noqa: E402


class FakeReceiver(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def _send(self, code, payload=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        FakeReceiver.calls.append(("GET", self.path, None))
        self._send(200, {"name": "Test", "state": "idle", "source": "", "position": 0, "duration": 0})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else None
        FakeReceiver.calls.append(("POST", self.path, body))
        if self.path == "/play" and not body.get("source"):
            self._send(400, {"error": "missing 'source'"})
        else:
            self._send(202 if self.path == "/play" else 200, {"ok": True})


class SenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeReceiver)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeReceiver.calls.clear()

    def test_play_posts_source(self):
        with mock.patch("sys.stdout"):
            projected_cast.main(["play", "--host", self.base, "--source", "/media/a.ogv"])
        self.assertEqual(FakeReceiver.calls[-1], ("POST", "/play", {"source": "/media/a.ogv"}))

    def test_seek_posts_position(self):
        with mock.patch("sys.stdout"):
            projected_cast.main(["seek", "--host", self.base, "12.5"])
        self.assertEqual(FakeReceiver.calls[-1], ("POST", "/seek", {"position": 12.5}))

    def test_pause_and_status(self):
        with mock.patch("sys.stdout"):
            projected_cast.main(["pause", "--host", self.base])
            projected_cast.main(["status", "--host", self.base])
        self.assertEqual(FakeReceiver.calls[0], ("POST", "/pause", None))
        self.assertEqual(FakeReceiver.calls[1], ("GET", "/status", None))

    def test_error_from_receiver_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            projected_cast.main(["play", "--host", self.base, "--source", ""])
        self.assertIn("400", str(ctx.exception))

    def test_discover_parses_beacon(self):
        # Send a beacon to ourselves on a free port, then point discover at it.
        beacon = json.dumps({"app": "projected_player", "v": 1, "name": "Den", "http_port": 8765}).encode()
        original_port = projected_cast.BEACON_PORT
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()
        projected_cast.BEACON_PORT = free_port
        try:
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            timer = threading.Timer(0.2, lambda: sender.sendto(beacon, ("127.0.0.1", free_port)))
            timer.start()
            found = projected_cast.discover(seconds=1.0)
            timer.join()
            sender.close()
        finally:
            projected_cast.BEACON_PORT = original_port
        self.assertIn("Den", found)
        self.assertEqual(found["Den"]["port"], 8765)


if __name__ == "__main__":
    unittest.main()
