"""Tests the Godot receiver itself, headless, over its real HTTP API.

Needs a Godot 4.4+ binary: set GODOT to its path, then from the repo root run

    GODOT=/path/to/godot python -m unittest tests/test_receiver.py

Without GODOT the tests are skipped. The harness (tests/godot/receiver_harness.gd) starts
an open receiver and one that needs a token, both limited to a temporary media folder.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sender"))
import projected_cast  # noqa: E402

GODOT = os.environ.get("GODOT", "")
TOKEN = "t0ken"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(GODOT and Path(GODOT).exists(), "set GODOT to a Godot 4.4+ binary to run these")
class ReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="projected-"))
        cls.media = cls.tmp / "media"
        cls.media.mkdir()
        for name in ("clip.ogv", "sound_only.ogv", "junk.ogv"):
            shutil.copy(ROOT / "tests" / "media" / name, cls.media / name)
        sibling = cls.tmp / "media2"
        sibling.mkdir()
        shutil.copy(ROOT / "tests" / "media" / "clip.ogv", sibling / "clip.ogv")
        cls.sibling = sibling
        # The global class names (ProjectedScreen, ProjectedReceiver) need an import pass.
        subprocess.run([GODOT, "--headless", "--path", str(ROOT), "--import"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        port = free_port()
        while True:  # the locked receiver takes the next port too
            try:
                with socket.socket() as s:
                    s.bind(("127.0.0.1", port + 1))
                break
            except OSError:
                port = free_port()
        cls.beacon = free_port()
        cls.open_url = f"http://127.0.0.1:{port}"
        cls.locked_url = f"http://127.0.0.1:{port + 1}"
        env = dict(os.environ, PORT=str(port), BEACON_PORT=str(cls.beacon), MEDIA_ROOT=str(cls.media),
                   TOKEN=TOKEN, RUN_SECONDS="240")
        cls.log = open(cls.tmp / "godot.log", "w+")
        cls.proc = subprocess.Popen([GODOT, "--headless", "--path", str(ROOT), "--script",
                                     "res://tests/godot/receiver_harness.gd"],
                                    env=env, stdout=cls.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            cls.log.seek(0)
            text = cls.log.read()
            if "READY" in text:
                break
            if cls.proc.poll() is not None or "SCRIPT ERROR" in text:
                raise RuntimeError("the receiver harness failed to start:\n" + text)
            time.sleep(0.2)
        else:
            raise RuntimeError("the receiver harness did not start in time")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers --

    def request(self, method, path, body=None, base=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request((base or self.open_url) + path, data=data, method=method,
                                     headers=dict({"Content-Type": "application/json"}, **(headers or {})))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text else {}), r.headers
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            return e.code, (json.loads(text) if text else {}), e.headers

    def play(self, path):
        return self.request("POST", "/play", {"source": str(path)})

    def wait_state(self, state, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            _, s, _ = self.request("GET", "/status")
            if s["state"] == state:
                return s
            time.sleep(0.1)
        self.fail(f"state never became {state}: {s}")

    def setUp(self):
        self.request("POST", "/stop")

    # -- the API --

    def test_info_and_idle_status(self):
        code, info, _ = self.request("GET", "/info")
        self.assertEqual((code, info["app"], info["v"], info["name"]), (200, "projected_player", 1, "Test Open"))
        code, status, _ = self.request("GET", "/status")
        self.assertEqual((code, status["state"], status["position"]), (200, "idle", 0.0))

    def test_play_reports_duration_and_advances(self):
        code, s, _ = self.play(self.media / "clip.ogv")
        self.assertEqual((code, s["state"]), (202, "playing"))
        self.assertAlmostEqual(s["duration"], 6.0, delta=0.2)
        time.sleep(1.2)
        _, s, _ = self.request("GET", "/status")
        self.assertGreater(s["position"], 0.5)

    def test_seek_while_playing_and_paused(self):
        self.play(self.media / "clip.ogv")
        code, s, _ = self.request("POST", "/seek", {"position": 4})
        self.assertEqual(code, 200)
        self.assertGreaterEqual(s["position"], 3.9)
        self.request("POST", "/pause")
        code, s, _ = self.request("POST", "/seek", {"position": 1.5})
        self.assertEqual((code, s["state"]), (200, "paused"))
        self.assertAlmostEqual(s["position"], 1.5, delta=0.1)

    def test_seek_needs_a_number_and_something_playing(self):
        code, _, _ = self.request("POST", "/seek", {"position": "soon"})
        self.assertEqual(code, 400)
        code, err, _ = self.request("POST", "/seek", {"position": 2})
        self.assertEqual(code, 422)
        self.assertIn("play something", err["error"])

    def test_pause_resume_only_change_real_playback(self):
        _, s, _ = self.request("POST", "/pause")
        self.assertEqual(s["state"], "idle")  # nothing playing yet
        _, s, _ = self.request("POST", "/resume")
        self.assertEqual(s["state"], "idle")
        self.play(self.media / "clip.ogv")
        self.assertEqual(self.request("POST", "/pause")[1]["state"], "paused")
        self.assertEqual(self.request("POST", "/resume")[1]["state"], "playing")
        self.request("POST", "/seek", {"position": 5.8})
        self.wait_state("idle", 3)  # played to the end
        self.assertEqual(self.request("POST", "/pause")[1]["state"], "idle")
        self.assertEqual(self.request("POST", "/resume")[1]["state"], "idle")

    def test_bad_sources_are_refused_and_nothing_plays(self):
        for name, words in (("nope.ogv", "no such file"), ("junk.ogv", "not an Ogg file"),
                            ("sound_only.ogv", "no Theora video")):
            code, err, _ = self.play(self.media / name)
            self.assertEqual(code, 422, name)
            self.assertIn(words, err["error"])
            self.assertEqual(self.request("GET", "/status")[1]["state"], "idle", name)

    def test_media_root_is_enforced(self):
        for source in (self.sibling / "clip.ogv", f"{self.media}/../media2/clip.ogv", "media/clip.ogv"):
            code, _, _ = self.play(source)
            self.assertEqual(code, 403, source)
        code, _, _ = self.request("POST", "/play", {})
        self.assertEqual(code, 400)

    def test_http_errors(self):
        self.assertEqual(self.request("GET", "/nowhere")[0], 404)
        self.assertEqual(self.request("POST", "/play", raw=b"x" * 70000)[0], 413)

    def test_cors_preflight_allows_authorization(self):
        code, _, headers = self.request("OPTIONS", "/play", headers={
            "Origin": "http://example.com", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type"})
        self.assertEqual(code, 204)
        self.assertIn("authorization", headers["Access-Control-Allow-Headers"].lower())
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_token(self):
        self.assertEqual(self.request("GET", "/status", base=self.locked_url)[0], 401)
        code, _, _ = self.request("GET", "/status", base=self.locked_url, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(code, 200)

    def test_sender_cli_controls_the_receiver(self):
        host = self.open_url.removeprefix("http://")
        result = projected_cast.call(f"http://{host}", "POST", "/play", {"source": str(self.media / "clip.ogv")})
        self.assertEqual(result["state"], "playing")
        self.assertEqual(projected_cast.call(f"http://{host}", "POST", "/stop")["state"], "idle")

    def test_beacon_is_heard(self):
        old = projected_cast.BEACON_PORT
        projected_cast.BEACON_PORT = self.beacon
        try:
            found = projected_cast.discover(3.0)
        finally:
            projected_cast.BEACON_PORT = old
        if not found:
            self.skipTest("this machine doesn't deliver UDP broadcasts back to itself")
        self.assertIn("Test Open", found)


if __name__ == "__main__":
    unittest.main()
