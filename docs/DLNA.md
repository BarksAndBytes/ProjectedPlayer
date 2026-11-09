# Casting from phone apps (DLNA)

`ProjectedDlnaRenderer` makes a `ProjectedScreen` show up as a TV in phone apps that cast to
DLNA TVs. Pick it from the app's list of TVs, choose a video, and it plays on the surface. The
app's play, pause, stop, seek and volume controls work.

## Which apps

Apps that cast to DLNA or UPnP TVs (often listed as "DLNA", "UPnP", "smart TV" or by TV brand):

- **BubbleUPnP** (Android): casts the phone's own videos, and videos from media servers.
- **Web Video Caster** (Android, iPhone): casts open videos found on web pages.
- Many gallery, file-manager and media-server apps (Plex, Jellyfin, Kodi remotes and others).

Apps that only use Google Cast won't list it: YouTube, Netflix, Hulu, Google Photos and other apps
whose cast button only shows Chromecasts. That list is limited to devices running Google's own
receiver software, so no homemade receiver can be on it.

## How a cast works

1. **Discovery.** The renderer answers SSDP searches on UDP port 1900 (multicast group
   239.255.255.250) and announces itself every 5 minutes. Phone apps then fetch
   `http://<device>:<port>/dlna/device.xml`, where `<port>` is `http_port`, or a free port when it is 0.
2. **The app sends a link** (`SetAVTransportURI`): usually to the video on the phone itself, served
   by the app. Only `http` and `https` links are accepted.
3. **FFmpeg converts it** to Ogg Theora (the format Godot plays) in the background, scaled to at
   most 1280 pixels wide (854 on phones). It is downloaded by FFmpeg, which may only use network
   protocols, so a link can't make it read files on the device. Meanwhile the app shows the TV as
   loading (`TRANSITIONING`) and `converting(fraction)` reports progress.
4. **It plays** on the screen. The last 5 converted videos are kept in `cache_dir`, so casting one
   again plays at once.

If FFmpeg can't use the link, the app is told (`TransportStatus` `ERROR_OCCURRED`) and
`cast_failed(reason)` gives the reason in words: the download failed, the link isn't a video,
or the video has no picture.

## Setting it up

```gdscript
var renderer := ProjectedDlnaRenderer.new()
renderer.screen = $Whiteboard/ProjectedScreen
renderer.friendly_name = "Whiteboard 1"   # what phones list
renderer.ffmpeg_path = ""                 # "" looks beside the program, then on the PATH
add_child(renderer)                       # starts by itself (auto_start)
renderer.cast_failed.connect(func(reason): show_message(reason))
```

| Property | Default | |
| --- | --- | --- |
| `screen` | | The `ProjectedScreen` to play on. |
| `friendly_name` | `Projected Player` | The name phones list. Give each board its own. |
| `http_port` | `0` | Port for descriptions, commands and events. 0 picks a free one. |
| `ffmpeg_path` | `""` | FFmpeg with the libtheora encoder. Required to cast. |
| `cache_dir` | `user://projected_player/cast` | Where converted videos are kept. |
| `uuid` | `""` | Identity. Empty: made from the device, name and node path, stable between runs. |
| `auto_start` | `true` | Start when added to the tree; otherwise call `start()`. |

Signals: `cast_received(uri, title)` when a phone picks a video, `converting(fraction)` while it
converts, `cast_failed(reason)`. `get_transport_state()` gives `NO_MEDIA_PRESENT`, `STOPPED`,
`TRANSITIONING`, `PLAYING` or `PAUSED_PLAYBACK`.

Several renderers can run at once, one per board: they share the SSDP port and each gets its own
HTTP port.

### Screens that play links themselves

If your game already downloads and converts videos (with its own progress display or library),
subclass `ProjectedScreen` and return `true` from `plays_links()`. The renderer then skips
FFmpeg: it calls `load_source(link)` and `play()` with the phone's link as is, reports
`TRANSITIONING` while `get_state_name()` is `"loading"`, and passes `"error"` (with
`get_last_error()`) to the phone. Seeks sent while loading are applied once it plays.
`tests/godot/link_screen.gd` is a minimal example.

### FFmpeg

Casting needs the FFmpeg program built with libtheora (and libvorbis for sound). Without it,
`SetAVTransportURI` is refused with UPnP error 714 and the reason. On desktop, put `ffmpeg` (or
`ffmpeg.exe`) beside the game or on the PATH. On Android, ship an FFmpeg executable in the APK's
native library folder and pass its path in `ffmpeg_path`. For `https` links, FFmpeg must be
built with TLS.

### Android

The export needs the **INTERNET** and **CHANGE_WIFI_MULTICAST_STATE** permissions. Without the last one, Android drops the phone's
searches and the renderer can't be found. Godot takes the multicast lock itself when the
renderer joins the SSDP group.

### When port 1900 is taken

Godot can't share a UDP port with another program. If something else on the device already holds
port 1900 (on Windows the "SSDP Discovery" service may), the renderer can't hear searches. It
logs a warning and announces itself every 15 seconds instead, and apps that listen for
announcements (most do) still find it within that time.

## Security

DLNA has no passwords. Anyone on the same network can cast to a running renderer, and it will
download any http(s) link it is sent. Run renderers only on networks you trust, and stop them
(`stop()`, or remove the node) when they aren't wanted.

## What's implemented

UPnP MediaRenderer:1 (DLNA DMR-1.50) with:

- **AVTransport:1**: SetAVTransportURI, Play, Pause, Stop, Seek (`REL_TIME`, `ABS_TIME`),
  GetTransportInfo, GetPositionInfo, GetMediaInfo, GetCurrentTransportActions,
  GetDeviceCapabilities, GetTransportSettings. Next and Previous answer error 701 (one video at a
  time). A seek sent before the video is ready is applied when it starts.
- **RenderingControl:1**: GetVolume, SetVolume (0 to 100), GetMute, SetMute, ListPresets,
  SelectPreset (`FactoryDefaults`).
- **ConnectionManager:1**: GetProtocolInfo (video types FFmpeg converts), GetCurrentConnectionIDs,
  GetCurrentConnectionInfo.
- **Events**: SUBSCRIBE, renewal and UNSUBSCRIBE. Changes are sent as `LastChange` (AVTransport and
  RenderingControl) at most 5 times a second; the first event has every value.

Not implemented: playlists (SetNextAVTransportURI), playback speeds other than 1, photos, and
audio-only casts (refused as "no picture").
