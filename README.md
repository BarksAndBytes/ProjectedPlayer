# Projected Player

A Godot 4 addon that turns any 3D surface into a video screen, and a receiver that other
devices on your network can cast open video to, the way you'd cast to a Chromecast.

- **Addon** (`addons/projected_player/`): `ProjectedScreen` plays video onto one surface of a mesh,
  such as a whiteboard in a CAD scene. Drop it into your project and point it at a mesh.
- **DLNA renderer** (`ProjectedDlnaRenderer`): the screen shows up as a TV in phone apps that
  cast to DLNA TVs, such as BubbleUPnP and Web Video Caster. Cast a video from your phone and it
  plays on the surface, with pause, seek and volume from the app. See [docs/DLNA.md](docs/DLNA.md).
- **Receiver** (`ProjectedReceiver`): advertises the screen on your LAN and accepts play, pause,
  resume, stop, and seek commands over HTTP.
- **Sender** (`sender/projected_cast.py`): a stdlib-only command-line client that discovers
  receivers and sends commands. The protocol is documented so you can write your own.
- **Standalone app** (`examples/standalone/`): a receiver scene you can export to Android or
  desktop.

## What it can and can't cast

This plays **open content**: your own videos and other video that apps can hand over as a plain
link. Through DLNA it takes MP4, MOV, MKV, WebM and most other video, converted with FFmpeg; the
JSON receiver plays local `.ogv` files.

It is not a Google Cast receiver, and can't become one: the Cast button in YouTube, Netflix,
Hulu and Google Photos lists only devices running Google's own receiver software. Netflix, Hulu
and other DRM services also play only on devices they have approved. Those apps won't list this
player; DLNA apps will.

## Quick start

1. Install [Godot 4.x](https://godotengine.org/download).
2. Open this folder as a project. Run the project to see `examples/standalone`.
3. Put an `.ogv` file under your Movies folder. This is the receiver's `media_root`.
4. From another machine on the same network:

   ```sh
   python sender/projected_cast.py discover
   python sender/projected_cast.py play --to "Projected Player" --source "/path/to/clip.ogv"
   python sender/projected_cast.py pause --to "Projected Player"
   ```

If the receiver has an `auth_token` set, pass it before the command, for example
`python sender/projected_cast.py --token "$TOKEN" pause --to "Projected Player"`, or set the
`PROJECTED_TOKEN` environment variable.

On Android, the export needs the **INTERNET** permission, and the receiver must be on the same
Wi-Fi network as the sender. `OS.get_system_dir(SYSTEM_DIR_MOVIES)` may return an empty path on
Android. If it does, set `media_root` on the receiver explicitly, or every play request returns 403.

## Using the addon in your project

1. Copy `addons/projected_player/` into your project's `addons/` folder.
2. Add a `ProjectedScreen` node and set `target_mesh` to the mesh that should show video.
3. Optionally add a `ProjectedDlnaRenderer` (set its `screen` and `friendly_name`) so phones
   can cast to it, and/or a `ProjectedReceiver` (set its `screen` and `media_root`) for the JSON
   API.
4. From code, call `load_source(path)` and then `play()`.

To add a format, subclass `PlaybackBackend` and override `ProjectedScreen._create_backend()`.

## Status and known limits

Version 1 is early. The receiver is tested headless on Godot 4.7 (see Tests below).

- **Playback:** the built-in backend plays `.ogv` (Ogg Theora) files only, with seeking and the
  video's duration (Godot 4.4 and later). Files that aren't Theora videos are refused with the
  reason, so the receiver never claims to play something it can't.
- **DLNA casts are converted before they play.** The phone app shows the TV as loading until
  FFmpeg has converted the whole video, which takes a while for long videos, especially when
  the renderer runs on a phone. Converted videos are cached, so casting one again is instant.
- **Security:** set `auth_token` on the receiver and pass `--token` from senders. The token is
  sent in plain HTTP, so it is not protection against someone sniffing your network.
- **Discovery:** the UDP beacon uses broadcast, so it doesn't cross subnets or VLANs.

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the network protocol.

## Tests

```sh
python -m unittest tests/test_sender.py                                   # the sender, against a fake receiver
GODOT=/path/to/godot python -m unittest tests/test_receiver.py           # the real receiver, headless
GODOT=/path/to/godot python -m unittest tests/test_dlna.py               # the DLNA renderer, as a phone app uses it
GODOT=/path/to/godot python -m unittest tests/test_dlna_client.py        # ...and driven by async-upnp-client
```

`test_receiver.py` starts two receivers in headless Godot (one open, one with a token) and
drives them over HTTP: playing, seeking, pausing, refused sources, `media_root`, CORS, tokens and
the beacon. `test_dlna.py` plays the phone: it searches with SSDP, reads the descriptions,
subscribes to events and casts an MP4 (with an index at the end, like phone recordings) from a
local web server. `test_dlna_client.py` does the same with
[async-upnp-client](https://pypi.org/project/async-upnp-client/), Home Assistant's DLNA library,
in strict mode. Without `GODOT` set they are skipped; the casting tests also need an FFmpeg with
libtheora. GitHub Actions runs them all on every push, with Godot 4.7.2, and fails if any
GDScript file doesn't compile.

## License

MIT. See [LICENSE](LICENSE).
