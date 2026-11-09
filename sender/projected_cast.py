#!/usr/bin/env python3
"""Command-line sender for Projected Player receivers.

Standard library only, so it runs on Windows, macOS, and Linux with Python 3.8+.

Examples:
    python projected_cast.py discover
    python projected_cast.py play --to "Living Room" --source "C:/Videos/clip.ogv"
    python projected_cast.py pause --host 192.168.1.20
    python projected_cast.py seek --to "Living Room" 42.5
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

BEACON_PORT = 47800
DEFAULT_HTTP_PORT = 8765


def discover(seconds: float = 3.0) -> dict:
    """Listen for receiver beacons. Returns {name: {"host": ip, "port": int}}."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", BEACON_PORT))
    sock.settimeout(0.5)
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                data, (ip, _) = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if message.get("app") != "projected_player":
                continue
            found[message["name"]] = {"host": ip, "port": int(message["http_port"])}
    finally:
        sock.close()
    return found


def base_url(args) -> str:
    if args.host:
        host = args.host
        if ":" not in host:
            host = f"{host}:{DEFAULT_HTTP_PORT}"
        return f"http://{host}"
    if args.to:
        receivers = discover(args.discover_seconds)
        if args.to not in receivers:
            known = ", ".join(sorted(receivers)) or "none"
            sys.exit(f"No receiver named '{args.to}' found. Heard: {known}")
        target = receivers[args.to]
        return f"http://{target['host']}:{target['port']}"
    sys.exit("Give a receiver with --to NAME or --host IP[:PORT].")


def call(base: str, method: str, path: str, payload: "dict | None" = None,
         token: "str | None" = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        sys.exit(f"Receiver returned {error.code}: {detail}")
    except urllib.error.URLError as error:
        sys.exit(f"Could not reach receiver at {base}: {error.reason}")
    except (socket.timeout, OSError) as error:
        sys.exit(f"Receiver at {base} did not respond: {error}")
    if not body:
        return {}
    try:
        return json.loads(body)
    except ValueError:
        sys.exit(f"Receiver at {base} sent a non-JSON response")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Control Projected Player receivers.")
    parser.add_argument("--discover-seconds", type=float, default=3.0,
                        help="how long to listen for beacons when using --to")
    parser.add_argument("--token", help="receiver auth token (or set PROJECTED_TOKEN)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="list receivers on the network")

    for name in ("status", "pause", "resume", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--to", help="receiver name from discovery")
        p.add_argument("--host", help="receiver address, IP[:PORT]")

    play = sub.add_parser("play", help="play a source on a receiver")
    play.add_argument("--to")
    play.add_argument("--host")
    play.add_argument("--source", required=True,
                      help="path on the receiver's disk (must be under its media_root)")

    seek = sub.add_parser("seek", help="jump to a position in seconds")
    seek.add_argument("--to")
    seek.add_argument("--host")
    seek.add_argument("position", type=float)

    args = parser.parse_args(argv)

    if args.command == "discover":
        receivers = discover(args.discover_seconds)
        print(json.dumps(receivers, indent=2) if receivers else "No receivers found.")
        return

    base = base_url(args)
    token = args.token or os.environ.get("PROJECTED_TOKEN")
    if args.command == "status":
        result = call(base, "GET", "/status", token=token)
    elif args.command in ("pause", "resume", "stop"):
        result = call(base, "POST", f"/{args.command}", token=token)
    elif args.command == "play":
        result = call(base, "POST", "/play", {"source": args.source}, token=token)
    elif args.command == "seek":
        result = call(base, "POST", "/seek", {"position": args.position}, token=token)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
