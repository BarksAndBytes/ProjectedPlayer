# Projected Player protocol (version 1)

Any device on the same local network can find a receiver and control it. Senders can be
the included Python CLI, a web page, or an app written in any language.

## Discovery: UDP beacon

Each receiver broadcasts a JSON datagram every 2 seconds to `255.255.255.255:47800`.

```json
{"app": "projected_player", "v": 1, "name": "Living Room", "http_port": 8765}
```

- `app` must equal `"projected_player"`. Ignore other messages.
- `name` is the receiver's display name and must be unique on your network.
- `http_port` is the TCP port for the control API. The sender's IP comes from the packet source.

## Control: HTTP JSON API

Base URL: `http://<receiver-ip>:<http_port>`. Requests and responses are JSON. Every
response includes `Access-Control-Allow-Origin: *`, so browser senders work. `OPTIONS`
returns 204 for preflight.

| Method | Path       | Body                    | Success | Notes                                  |
|--------|------------|-------------------------|---------|----------------------------------------|
| GET    | `/info`    |                         | 200     | `{app, v, name}`                       |
| GET    | `/status`  |                         | 200     | See status object below                |
| POST   | `/play`    | `{"source": "<path>"}`  | 202     | Starts playback, returns status        |
| POST   | `/pause`   |                         | 200     | Returns status                         |
| POST   | `/resume`  |                         | 200     | Returns status                         |
| POST   | `/stop`    |                         | 200     | Returns status                         |
| POST   | `/seek`    | `{"position": 42.5}`    | 200     | Position in seconds                    |

Status object:

```json
{"name": "Living Room", "state": "playing", "source": "C:/Videos/clip.ogv",
 "position": 12.3, "duration": 0.0}
```

- `state` is one of `idle`, `playing`, `paused`.
- `duration` is 0 when the backend cannot report it. The built-in Theora backend always does.

### Errors

| Code | Meaning                                                              |
|------|----------------------------------------------------------------------|
| 400  | Malformed request, or `source` missing                                |
| 401  | Auth token missing or wrong (only when the receiver has a token set)  |
| 403  | `source` is outside the receiver's `media_root`, or `media_root` is unset |
| 404  | Unknown endpoint                                                     |
| 408  | Request not completed within 5 seconds                                |
| 413  | Request body over 64 KB                                              |
| 422  | Backend cannot play this source, or cannot seek (the built-in backend only plays `.ogv` files and does not seek) |

Error bodies are `{"error": "message"}`.

## Sources

Version 1 accepts local file paths only, and only inside the receiver's `media_root`. Seek
requests return 422 with the built-in backend.
URLs are not supported until a backend that can stream them is added. Keep that in mind
when you write senders.

## Authentication

Set `auth_token` on the receiver to require `Authorization: Bearer <token>` on every request
except `OPTIONS`. Senders pass it with `--token` or the `PROJECTED_TOKEN` environment variable.
The beacon does not include the token.

With `auth_token` empty, the API is **open**. Anyone on the same network can play or stop
content, so run an open receiver only on a network you trust. The token is a shared secret
sent in plain HTTP. It keeps out casual access on a home LAN, but it does not protect against
anyone who can sniff the network. Use it with that limit in mind.

## Versioning

The `v` field is the protocol version. Receivers and senders should ignore unknown fields
and reject a `v` they do not support.
