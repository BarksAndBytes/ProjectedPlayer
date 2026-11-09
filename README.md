# Projected Player

A Godot 4 addon that turns any 3D surface into a video screen, and a receiver that other
devices on your network can cast open video to, the way you'd cast to a Chromecast.

- **Addon** (`addons/projected_player/`): `ProjectedScreen` plays video onto one surface of a mesh,
  such as a whiteboard in a CAD scene. Drop it into your project and point it at a mesh.
- **Receiver** (`ProjectedReceiver`): advertises the screen on your LAN and accepts play, pause,
  resume, stop, and seek commands over HTTP.
- **Sender** (`sender/projected_cast.py`): a stdlib-only command-line client that discovers
  receivers and sends commands. The protocol is documented so you can write your own.
- **Standalone app** (`examples/standalone/`): a receiver scene you can export to Android or
  desktop.

## What it can and can't cast

This plays **open content**: local `.ogv` files in version 1, with URL and MP4 support planned
through a pluggable backend. Netflix, Hulu, and other DRM services play only inside their own
certified apps and cannot be routed through a custom player. Those services also don't offer
third-party receivers.

It is not a Google Cast receiver. Phones and apps like YouTube won't list it in their Cast menu.
Use the sender CLI or your own sender instead. A registered Cast receiver is a possible
follow-up.

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
3. Optionally add a `ProjectedReceiver` node, set its `screen` and `media_root`, and it will be
   controllable over the network.
4. From code, call `load_source(path)` and then `play()`.

To add a format, subclass `PlaybackBackend` and override `ProjectedScreen._create_backend()`.

## Status and known limits

Version 1 is early. It has not been run in the Godot editor yet, so expect small fixes.

- **Playback:** the built-in backend plays `.ogv` (Theora) only. It cannot seek, because Godot's
  Theora playback ignores seek requests, and it reports duration as 0.
- **Security:** set `auth_token` on the receiver and pass `--token` from senders. The token is
  sent in plain HTTP, so it is not protection against someone sniffing your network.
- **Discovery:** the UDP beacon uses broadcast, so it doesn't cross subnets or VLANs.

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the network protocol.

## Tests

```sh
python -m unittest tests/test_sender.py
```

These cover the Python sender. The GDScript receiver needs the Godot editor to test.

## License

MIT. See [LICENSE](LICENSE).
