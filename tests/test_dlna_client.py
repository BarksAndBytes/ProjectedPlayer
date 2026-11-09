"""Drives the DLNA renderer with a third-party control point: async-upnp-client, the library Home
Assistant uses for DLNA TVs. Its strict mode checks the descriptions as well as the commands.

    pip install async-upnp-client
    GODOT=/path/to/godot python -m unittest tests/test_dlna_client.py

Skipped unless GODOT is set, async-upnp-client is installed and FFmpeg has libtheora.
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dlna as base  # noqa: E402

try:
    from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpRequester
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.profiles.dlna import DmrDevice, TransportState
    from async_upnp_client.search import async_search
    HAVE_CLIENT = True
except ImportError:
    HAVE_CLIENT = False


@unittest.skipUnless(base.GODOT and Path(base.GODOT).exists(), "set GODOT to a Godot 4.4+ binary to run these")
@unittest.skipUnless(HAVE_CLIENT, "pip install async-upnp-client to run these")
@unittest.skipUnless(base.has_theora(base.FFMPEG), "needs an FFmpeg with libtheora")
class ThirdPartyClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="projected-dlna-client-"))
        cls.media = cls.tmp / "media"
        cls.media.mkdir()
        subprocess.run([base.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=5:size=320x180:rate=15",
                        "-f", "lavfi", "-i", "sine=duration=5", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(cls.media / "clip.mp4")],
                       check=True, timeout=120)
        base.import_project()
        cls.ssdp_port = base.free_udp_port()
        cls.harness = base.Harness(cls.tmp, cls.ssdp_port, base.FFMPEG)
        cls.files = base.MediaServer(cls.media)

    @classmethod
    def tearDownClass(cls):
        cls.harness.close()
        cls.files.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_search_finds_the_renderers(self):
        async def run():
            found = []

            async def on_reply(headers):
                found.append(dict(headers))

            await async_search(on_reply, timeout=2, search_target="urn:schemas-upnp-org:device:MediaRenderer:1",
                               target=(base.GROUP, self.ssdp_port))
            return found

        found = asyncio.run(run())
        usns = {h.get("USN", h.get("usn", "")) for h in found}
        for r in self.harness.renderers.values():
            self.assertIn(r["udn"] + "::urn:schemas-upnp-org:device:MediaRenderer:1", usns, found)

    def test_cast_play_pause_seek_volume(self):
        r = self.harness.renderers["Board One"]
        url = self.files.url("clip.mp4")

        async def wait_state(dmr, want, seconds=60):
            for _ in range(seconds * 10):
                await dmr.async_update()
                if dmr.transport_state == want:
                    return
                await asyncio.sleep(0.1)
            raise AssertionError(f"never reached {want}: {dmr.transport_state}")

        async def run():
            requester = AiohttpRequester()
            device = await UpnpFactory(requester, non_strict=False).async_create_device(r["base"] + "/dlna/device.xml")
            server = AiohttpNotifyServer(requester, source=("127.0.0.1", 0))
            await server.async_start_server()
            dmr = DmrDevice(device, server.event_handler)
            try:
                self.assertEqual(device.friendly_name, "Board One")
                await dmr.async_subscribe_services(auto_resubscribe=False)
                await dmr.async_update()
                self.assertEqual(dmr.transport_state, TransportState.NO_MEDIA_PRESENT)

                await dmr.async_set_transport_uri(url, "Client clip")
                await dmr.async_play()
                await wait_state(dmr, TransportState.PLAYING)
                self.assertEqual(dmr.media_duration, 5)
                self.assertEqual(dmr.current_track_uri, url)
                self.assertTrue(dmr.can_pause and dmr.can_seek_rel_time)

                await dmr.async_pause()
                await wait_state(dmr, TransportState.PAUSED_PLAYBACK)
                await dmr.async_seek_rel_time(timedelta(seconds=3))
                await dmr.async_update()
                self.assertEqual(dmr.media_position, 3)

                await dmr.async_set_volume_level(0.4)
                await dmr.async_mute_volume(True)
                await dmr.async_update()
                self.assertAlmostEqual(dmr.volume_level, 0.4, places=2)
                self.assertTrue(dmr.is_volume_muted)
                await dmr.async_mute_volume(False)

                await dmr.async_play()
                await wait_state(dmr, TransportState.PLAYING)
                await dmr.async_stop()
                await wait_state(dmr, TransportState.STOPPED, 10)
            finally:
                await dmr.async_unsubscribe_services()
                await server.async_stop_server()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
