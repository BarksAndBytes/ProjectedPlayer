"""Tests the DLNA renderer headless, the way a phone app would use it.

Needs a Godot 4.4+ binary and, for casting, an FFmpeg with the libtheora encoder:

    GODOT=/path/to/godot python -m unittest tests/test_dlna.py

The harness (tests/godot/dlna_harness.gd) starts two renderers, "Board One" and "Board Two".
This file plays the phone: it searches with SSDP, reads the descriptions, sends SOAP commands,
subscribes to events, and serves an MP4 over HTTP for the renderer to fetch.
"""

import http.server
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
GODOT = os.environ.get("GODOT", "")
FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or "")
GROUP = "239.255.255.250"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RCS = "urn:schemas-upnp-org:service:RenderingControl:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"
DEVICE = "{urn:schemas-upnp-org:device-1-0}"


def has_theora(ffmpeg: str) -> bool:
    if not ffmpeg:
        return False
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except OSError:
        return False
    return "libtheora" in out


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def import_project():
    # The global class names (ProjectedScreen, ProjectedDlnaRenderer) need an import pass.
    subprocess.run([GODOT, "--headless", "--path", str(ROOT), "--import"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


class Harness:
    """The Godot harness process and its log."""

    def __init__(self, tmp: Path, ssdp_port: int, ffmpeg: str):
        self.log = open(tmp / f"godot-{ssdp_port}.log", "w+")
        env = dict(os.environ, SSDP_PORT=str(ssdp_port), CACHE_DIR=str(tmp / "cache"), FFMPEG=ffmpeg, RUN_SECONDS="300")
        self.proc = subprocess.Popen([GODOT, "--headless", "--path", str(ROOT), "--script", "res://tests/godot/dlna_harness.gd"],
                                     env=env, stdout=self.log, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            text = self.text()
            if "READY" in text:
                break
            if self.proc.poll() is not None or "SCRIPT ERROR" in text:
                raise RuntimeError("the DLNA harness failed to start:\n" + text)
            time.sleep(0.2)
        else:
            raise RuntimeError("the DLNA harness never got ready:\n" + self.text())
        self.renderers = {}
        for name, port, udn in re.findall(r"^RENDERER (.+) (\d+) (uuid:\S+)$", self.text(), re.M):
            self.renderers[name] = {"port": int(port), "udn": udn, "base": f"http://127.0.0.1:{port}"}

    def text(self) -> str:
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def soap(base: str, short: str, service: str, action: str, **args):
    """Sends a SOAP action; returns (HTTP status, {out argument: value} or {"errorCode", "errorDescription"})."""
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
    envelope = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>')
    req = urllib.request.Request(f"{base}/dlna/{short}/control", data=envelope.encode(), method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{service}#{action}"'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    root = ET.fromstring(text)
    out = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el is not root and len(el) == 0 and tag not in ("faultcode", "faultstring"):
            out[tag] = el.text or ""
    return status, out


def gena(base: str, short: str, method: str, headers: dict):
    req = urllib.request.Request(f"{base}/dlna/{short}/event", method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


class EventSink(http.server.ThreadingHTTPServer):
    """Receives the renderer's NOTIFY events."""

    def __init__(self):
        self.events = []  # (SID, SEQ, {variable: value})
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), self._Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_NOTIFY(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            values = {}
            for prop in ET.fromstring(body):
                for var in prop:
                    name = var.tag.split("}")[-1]
                    if name == "LastChange":
                        for item in ET.fromstring(var.text).iter():
                            if "val" in item.attrib:
                                values[item.tag.split("}")[-1]] = item.attrib["val"]
                    else:
                        values[name] = var.text or ""
            with self.server.lock:
                self.server.events.append((self.headers.get("SID"), int(self.headers.get("SEQ")), values))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/event"

    def wait_for(self, sid: str, test, timeout=30.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for event in self.events:
                    if event[0] == sid and test(event[2]):
                        return event
            time.sleep(0.1)
        raise AssertionError(f"no matching event; got {self.events}")


class MediaServer(http.server.ThreadingHTTPServer):
    """Serves the test videos, as a phone's DLNA server would."""

    def __init__(self, folder: Path):
        handler = lambda *a, **kw: _QuietHandler(*a, directory=str(folder), **kw)  # noqa: E731
        super().__init__(("127.0.0.1", 0), handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/{name}"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static files with HTTP Range support, which FFmpeg needs to read an MP4 whose index is at
    the end (as phone recordings' often are). Phones' DLNA servers support it too."""

    def send_head(self):
        rng = self.headers.get("Range", "")
        path = Path(self.translate_path(self.path))
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng)
        if not m or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self._remaining = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._remaining))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def end_headers(self):
        if not self.headers.get("Range"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def log_message(self, *args):
        pass


def msearch(port: int, st: str, wait=2.0):
    """Sends an SSDP M-SEARCH to the multicast group on port; returns the replies' headers."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.bind(("0.0.0.0", 0))
    s.settimeout(0.3)
    msg = f'M-SEARCH * HTTP/1.1\r\nHOST: {GROUP}:{port}\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: {st}\r\n\r\n'
    for _ in range(2):  # UDP may drop one
        s.sendto(msg.encode(), (GROUP, port))
    replies = []
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            continue
        lines = data.decode().split("\r\n")
        headers = {k.strip().upper(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if ":" in l)}
        headers["_status"] = lines[0]
        replies.append(headers)
    s.close()
    return replies


@unittest.skipUnless(GODOT and Path(GODOT).exists(), "set GODOT to a Godot 4.4+ binary to run these")
class DlnaUnitTest(unittest.TestCase):
    def test_helpers(self):
        import_project()
        out = subprocess.run([GODOT, "--headless", "--path", str(ROOT), "--script", "res://tests/godot/dlna_unit.gd"],
                             capture_output=True, text=True, timeout=120)
        text = out.stdout + out.stderr
        self.assertIn("DONE 0", text, text)


@unittest.skipUnless(GODOT and Path(GODOT).exists(), "set GODOT to a Godot 4.4+ binary to run these")
class DlnaRendererTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="projected-dlna-"))
        cls.media = cls.tmp / "media"
        cls.media.mkdir()
        cls.ffmpeg = FFMPEG if has_theora(FFMPEG) else ""
        if cls.ffmpeg:
            # A phone-style MP4: MPEG-4 video and AAC sound, 4 seconds.
            subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=320x180:rate=15",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "mpeg4", "-c:a", "aac", "-shortest",
                            str(cls.media / "clip.mp4")], check=True, timeout=120)
            subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=2",
                            "-c:a", "aac", str(cls.media / "sound.m4a")], check=True, timeout=120)
        (cls.media / "page.html").write_text("<html><body>not a video</body></html>")
        import_project()
        cls.ssdp_port = free_udp_port()
        cls.harness = Harness(cls.tmp, cls.ssdp_port, cls.ffmpeg)
        cls.one = cls.harness.renderers["Board One"]
        cls.two = cls.harness.renderers["Board Two"]
        cls.files = MediaServer(cls.media)
        cls.sink = EventSink()
        cls.first_state = {name: soap(r["base"], "AVTransport", AVT, "GetTransportInfo", InstanceID=0)[1] for name, r in cls.harness.renderers.items()}

    @classmethod
    def tearDownClass(cls):
        cls.harness.close()
        cls.files.shutdown()
        cls.sink.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need_ffmpeg(self):
        if not self.ffmpeg:
            self.skipTest("needs an FFmpeg with libtheora (set FFMPEG or put ffmpeg on the PATH)")

    def state(self, r):
        return soap(r["base"], "AVTransport", AVT, "GetTransportInfo", InstanceID=0)[1]

    def wait_state(self, r, want, timeout=60.0):
        deadline = time.monotonic() + timeout
        info = {}
        while time.monotonic() < deadline:
            info = self.state(r)
            if info["CurrentTransportState"] == want:
                return info
            time.sleep(0.1)
        self.fail(f"never reached {want}: {info}\n{self.harness.text()[-3000:]}")

    def cast(self, r, url, title="Test & clip"):
        meta = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="1" parentID="0" restricted="1">'
                f'<dc:title>{escape(title)}</dc:title><upnp:class>object.item.videoItem</upnp:class>'
                f'<res protocolInfo="http-get:*:video/mp4:*">{escape(url)}</res></item></DIDL-Lite>')
        status, out = soap(r["base"], "AVTransport", AVT, "SetAVTransportURI", InstanceID=0, CurrentURI=url, CurrentURIMetaData=meta)
        self.assertEqual(status, 200, out)
        return meta

    # --- discovery ------------------------------------------------------------------------

    def test_search_finds_both_renderers(self):
        replies = msearch(self.ssdp_port, "urn:schemas-upnp-org:device:MediaRenderer:1")
        usns = {r["USN"] for r in replies}
        for r in (self.one, self.two):
            self.assertIn(r["udn"] + "::urn:schemas-upnp-org:device:MediaRenderer:1", usns, replies)
        for reply in replies:
            self.assertEqual(reply["_status"], "HTTP/1.1 200 OK")
            self.assertTrue(reply["LOCATION"].endswith("/dlna/device.xml"))
            self.assertIn("max-age=", reply["CACHE-CONTROL"])
            self.assertIn("EXT", reply)

    def test_search_all_lists_every_service(self):
        replies = msearch(self.ssdp_port, "ssdp:all")
        sts = {r["ST"] for r in replies if r["USN"].startswith(self.one["udn"])}
        self.assertEqual(sts, {"upnp:rootdevice", self.one["udn"], "urn:schemas-upnp-org:device:MediaRenderer:1", AVT, RCS, CM})

    def test_search_for_other_devices_gets_no_reply(self):
        self.assertEqual(msearch(self.ssdp_port, "urn:schemas-upnp-org:device:MediaServer:1", wait=1.0), [])

    def test_location_works(self):
        replies = msearch(self.ssdp_port, "upnp:rootdevice")
        location = next(r["LOCATION"] for r in replies if r["USN"].startswith(self.two["udn"]))
        with urllib.request.urlopen(location, timeout=10) as resp:
            root = ET.fromstring(resp.read())
        self.assertEqual(root.find(f"{DEVICE}device/{DEVICE}friendlyName").text, "Board Two")

    # --- description ----------------------------------------------------------------------

    def test_description_and_service_lists(self):
        with urllib.request.urlopen(self.one["base"] + "/dlna/device.xml", timeout=10) as resp:
            self.assertIn("text/xml", resp.headers["Content-Type"])
            root = ET.fromstring(resp.read())
        device = root.find(f"{DEVICE}device")
        self.assertEqual(device.find(f"{DEVICE}deviceType").text, "urn:schemas-upnp-org:device:MediaRenderer:1")
        self.assertEqual(device.find(f"{DEVICE}friendlyName").text, "Board One")
        self.assertEqual(device.find(f"{DEVICE}UDN").text, self.one["udn"])
        services = {s.find(f"{DEVICE}serviceType").text: s for s in device.iter(f"{DEVICE}service")}
        self.assertEqual(set(services), {AVT, RCS, CM})
        want = {AVT: {"SetAVTransportURI", "Play", "Pause", "Stop", "Seek", "GetPositionInfo", "GetTransportInfo", "GetMediaInfo"},
                RCS: {"GetVolume", "SetVolume", "GetMute", "SetMute"}, CM: {"GetProtocolInfo", "GetCurrentConnectionIDs"}}
        for type_, service in services.items():
            with urllib.request.urlopen(self.one["base"] + service.find(f"{DEVICE}SCPDURL").text, timeout=10) as resp:
                scpd = ET.fromstring(resp.read())
            ns = "{urn:schemas-upnp-org:service-1-0}"
            actions = {a.find(f"{ns}name").text for a in scpd.iter(f"{ns}action")}
            self.assertTrue(want[type_] <= actions, (type_, actions))
            variables = {v.find(f"{ns}name").text for v in scpd.iter(f"{ns}stateVariable")}
            for arg in scpd.iter(f"{ns}argument"):
                self.assertIn(arg.find(f"{ns}relatedStateVariable").text, variables)

    def test_unknown_paths_are_404(self):
        for path in ("/", "/dlna/nothing.xml", "/info", "/dlna/AVTransport/nothing"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.one["base"] + path, timeout=10)
            self.assertIn(cm.exception.code, (404, 405), path)

    # --- commands ---------------------------------------------------------------------------

    def clear(self, r):
        """Back to no video, as an app does with an empty SetAVTransportURI."""
        status, out = soap(r["base"], "AVTransport", AVT, "SetAVTransportURI", InstanceID=0, CurrentURI="", CurrentURIMetaData="")
        self.assertEqual(status, 200, out)
        self.assertEqual(self.state(r)["CurrentTransportState"], "NO_MEDIA_PRESENT")

    def test_starts_with_no_media(self):
        for info in self.first_state.values():
            self.assertEqual(info["CurrentTransportState"], "NO_MEDIA_PRESENT")
            self.assertEqual(info["CurrentTransportStatus"], "OK")

    def test_protocol_info_lists_mp4(self):
        status, out = soap(self.two["base"], "ConnectionManager", CM, "GetProtocolInfo")
        self.assertEqual(status, 200)
        self.assertIn("http-get:*:video/mp4:*", out["Sink"])
        self.assertEqual(soap(self.two["base"], "ConnectionManager", CM, "GetCurrentConnectionIDs")[1]["ConnectionIDs"], "0")

    def test_volume_and_mute(self):
        base = self.two["base"]
        self.assertEqual(soap(base, "RenderingControl", RCS, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=30)[0], 200)
        self.assertEqual(soap(base, "RenderingControl", RCS, "GetVolume", InstanceID=0, Channel="Master")[1]["CurrentVolume"], "30")
        status, out = soap(base, "RenderingControl", RCS, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=150)
        self.assertEqual((status, out["errorCode"]), (500, "402"))
        soap(base, "RenderingControl", RCS, "SetMute", InstanceID=0, Channel="Master", DesiredMute="true")
        self.assertEqual(soap(base, "RenderingControl", RCS, "GetMute", InstanceID=0, Channel="Master")[1]["CurrentMute"], "1")
        soap(base, "RenderingControl", RCS, "SelectPreset", InstanceID=0, PresetName="FactoryDefaults")
        self.assertEqual(soap(base, "RenderingControl", RCS, "GetVolume", InstanceID=0, Channel="Master")[1]["CurrentVolume"], "100")
        self.assertEqual(soap(base, "RenderingControl", RCS, "GetMute", InstanceID=0, Channel="Master")[1]["CurrentMute"], "0")

    def test_bad_commands_get_upnp_errors(self):
        base = self.two["base"]
        self.assertEqual(soap(base, "AVTransport", AVT, "Explode", InstanceID=0)[1]["errorCode"], "401")
        self.assertEqual(soap(base, "AVTransport", AVT, "GetTransportInfo", InstanceID=3)[1]["errorCode"], "718")
        status, out = soap(base, "AVTransport", AVT, "SetAVTransportURI", InstanceID=0, CurrentURI="file:///etc/passwd", CurrentURIMetaData="")
        self.assertEqual((status, out["errorCode"]), (500, "716"))
        self.assertNotIn("passwd", self.harness.text())
        self.assertEqual(soap(base, "AVTransport", AVT, "Seek", InstanceID=0, Unit="TRACK_NR", Target="1")[1]["errorCode"], "710")
        self.assertEqual(soap(base, "AVTransport", AVT, "Seek", InstanceID=0, Unit="REL_TIME", Target="soon")[1]["errorCode"], "711")
        self.assertEqual(soap(base, "AVTransport", AVT, "Next", InstanceID=0)[1]["errorCode"], "701")

    def test_play_without_media_is_refused(self):
        self.clear(self.two)
        self.assertEqual(soap(self.two["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)[1]["errorCode"], "701")

    def test_malformed_soap(self):
        req = urllib.request.Request(self.two["base"] + "/dlna/AVTransport/control", data=b"not xml", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 500)

    # --- events ---------------------------------------------------------------------------

    def test_subscribe_renew_unsubscribe(self):
        base = self.two["base"]
        status, headers = gena(base, "RenderingControl", "SUBSCRIBE", {"CALLBACK": f"<{self.sink.url}>", "NT": "upnp:event", "TIMEOUT": "Second-300"})
        self.assertEqual(status, 200)
        sid = headers["SID"]
        self.assertEqual(headers["TIMEOUT"], "Second-300")
        first = self.sink.wait_for(sid, lambda v: "Volume" in v)
        self.assertEqual(first[1], 0)  # the first event is SEQ 0, with every value
        soap(base, "RenderingControl", RCS, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=55)
        self.sink.wait_for(sid, lambda v: v.get("Volume") == "55")
        soap(base, "RenderingControl", RCS, "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=100)
        self.assertEqual(gena(base, "RenderingControl", "SUBSCRIBE", {"SID": sid, "TIMEOUT": "Second-600"})[0], 200)
        self.assertEqual(gena(base, "AVTransport", "SUBSCRIBE", {"SID": sid})[0], 412)  # another service's SID
        self.assertEqual(gena(base, "RenderingControl", "UNSUBSCRIBE", {"SID": sid})[0], 200)
        self.assertEqual(gena(base, "RenderingControl", "UNSUBSCRIBE", {"SID": sid})[0], 412)
        self.assertEqual(gena(base, "RenderingControl", "SUBSCRIBE", {"CALLBACK": f"<{self.sink.url}>", "NT": "wrong"})[0], 412)
        self.assertEqual(gena(base, "RenderingControl", "SUBSCRIBE", {"NT": "upnp:event"})[0], 412)

    def test_connection_manager_events_plain_properties(self):
        status, headers = gena(self.two["base"], "ConnectionManager", "SUBSCRIBE", {"CALLBACK": f"<{self.sink.url}>", "NT": "upnp:event"})
        self.assertEqual(status, 200)
        event = self.sink.wait_for(headers["SID"], lambda v: "SinkProtocolInfo" in v)
        self.assertIn("video/mp4", event[2]["SinkProtocolInfo"])

    # --- casting ----------------------------------------------------------------------------

    def test_cast_an_mp4(self):
        self.need_ffmpeg()
        r = self.one
        status, headers = gena(r["base"], "AVTransport", "SUBSCRIBE", {"CALLBACK": f"<{self.sink.url}>", "NT": "upnp:event"})
        sid = headers["SID"]
        self.sink.wait_for(sid, lambda v: v.get("TransportState") == "NO_MEDIA_PRESENT")

        url = self.files.url("clip.mp4")
        meta = self.cast(r, url)
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)[0], 200)
        self.assertIn(self.state(r)["CurrentTransportState"], ("TRANSITIONING", "PLAYING"))
        self.wait_state(r, "PLAYING")
        self.sink.wait_for(sid, lambda v: v.get("TransportState") == "PLAYING")
        self.assertIn(f"SIGNAL Board One cast_received {url}|Test & clip", self.harness.text())

        media = soap(r["base"], "AVTransport", AVT, "GetMediaInfo", InstanceID=0)[1]
        self.assertEqual(media["CurrentURI"], url)
        self.assertEqual(media["CurrentURIMetaData"], meta)
        self.assertEqual(media["MediaDuration"], "0:00:04")
        actions = soap(r["base"], "AVTransport", AVT, "GetCurrentTransportActions", InstanceID=0)[1]["Actions"]
        self.assertEqual(set(actions.split(",")), {"Pause", "Stop", "Seek"})

        time.sleep(1.2)
        pos = soap(r["base"], "AVTransport", AVT, "GetPositionInfo", InstanceID=0)[1]
        self.assertEqual(pos["TrackDuration"], "0:00:04")
        self.assertEqual(pos["TrackURI"], url)
        self.assertIn(pos["RelTime"], ("0:00:01", "0:00:02"))

        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Pause", InstanceID=0)[0], 200)
        self.wait_state(r, "PAUSED_PLAYBACK")
        self.sink.wait_for(sid, lambda v: v.get("TransportState") == "PAUSED_PLAYBACK")
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Seek", InstanceID=0, Unit="REL_TIME", Target="0:00:03")[0], 200)
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "GetPositionInfo", InstanceID=0)[1]["RelTime"], "0:00:03")
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)[0], 200)
        self.wait_state(r, "PLAYING")
        self.wait_state(r, "STOPPED", timeout=10)  # runs to the end
        self.sink.wait_for(sid, lambda v: v.get("TransportState") == "STOPPED")

        # Casting it again uses the converted copy: it plays at once.
        self.cast(r, url)
        self.assertEqual(self.state(r)["CurrentTransportState"], "STOPPED")
        soap(r["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)
        self.assertEqual(self.state(r)["CurrentTransportState"], "PLAYING")
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Stop", InstanceID=0)[0], 200)
        self.assertEqual(self.state(r)["CurrentTransportState"], "STOPPED")
        gena(r["base"], "AVTransport", "UNSUBSCRIBE", {"SID": sid})

    def test_seek_before_it_plays(self):
        self.need_ffmpeg()
        r = self.two
        self.cast(r, self.files.url("clip.mp4") + "?again")
        self.assertEqual(soap(r["base"], "AVTransport", AVT, "Seek", InstanceID=0, Unit="REL_TIME", Target="0:00:02")[0], 200)
        soap(r["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)
        self.wait_state(r, "PLAYING")
        rel = soap(r["base"], "AVTransport", AVT, "GetPositionInfo", InstanceID=0)[1]["RelTime"]
        self.assertIn(rel, ("0:00:02", "0:00:03"))
        soap(r["base"], "AVTransport", AVT, "Stop", InstanceID=0)

    def test_failures_are_reported(self):
        self.need_ffmpeg()
        r = self.two
        for name, why in (("missing.mp4", "couldn't download"), ("page.html", "isn't a video"), ("sound.m4a", "no picture")):
            self.cast(r, self.files.url(name))
            soap(r["base"], "AVTransport", AVT, "Play", InstanceID=0, Speed=1)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and self.state(r)["CurrentTransportStatus"] != "ERROR_OCCURRED":
                time.sleep(0.1)
            info = self.state(r)
            self.assertEqual((info["CurrentTransportState"], info["CurrentTransportStatus"]), ("STOPPED", "ERROR_OCCURRED"), name)
            self.assertRegex(self.harness.text(), rf"SIGNAL Board Two cast_failed .*{re.escape(why)}", name)


@unittest.skipUnless(GODOT and Path(GODOT).exists(), "set GODOT to a Godot 4.4+ binary to run these")
class DlnaPortTakenTest(unittest.TestCase):
    """When another program holds the SSDP port, the renderers announce themselves instead."""

    def test_announces_when_it_cannot_listen(self):
        port = free_udp_port()
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", port))
        listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
        listener.settimeout(0.5)
        tmp = Path(tempfile.mkdtemp(prefix="projected-dlna-taken-"))
        import_project()
        harness = Harness(tmp, port, "")
        try:
            self.assertIn("SSDP listening=false", harness.text())
            seen = set()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(seen) < 2:
                try:
                    data = listener.recv(4096).decode()
                except socket.timeout:
                    continue
                if data.startswith("NOTIFY") and "NTS: ssdp:alive" in data and "NT: upnp:rootdevice" in data:
                    seen.add(re.search(r"USN: (uuid:[^:\r]+)", data).group(1))
            self.assertEqual(seen, {r["udn"] for r in harness.renderers.values()})
        finally:
            harness.close()
            listener.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
