"""
SerialClip capture - grab N seconds of live serial data as formatted text.

Built for AI/scripted use: run it, read stdout. No clipboard, no browser.

Primary mode taps the running web_monitor server's websocket as an extra
listener - the browser view and COM port are untouched. If the server isn't
running, falls back to opening the serial port directly.

Usage:
    python capture.py [--seconds 5] [--port COM3] [--baud 115200] [--raw] [--json]

Output (default):
    capture  <start> - <end>  (5.0s)
    == LDR ==  min 3888.0  max 3917.0  last 3904.0  n=50
      t=  0.0s   3904.0  3901.0 ...
    == A35 == ...

    --raw   also prints every raw serial line received
    --json  prints one JSON object instead of text
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime

WS_URL = "ws://localhost:8765"
LINE_RE = re.compile(r"[,\t]+")
VALUES_PER_ROW = 10
MAX_TEXT_POINTS = 300


def parse_line(line):
    line = line.strip()
    if not line:
        return None
    parts = LINE_RE.split(line) if ("," in line or "\t" in line) else line.split()
    result = {}
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, _, val = part.partition(":")
            name = name.strip() or f"ch{i + 1}"
        else:
            name = f"ch{i + 1}"
            val = part
        try:
            result[name] = float(val)
        except ValueError:
            continue
    return result or None


def fetch_visibility():
    """Channels the user has unchecked in the browser UI (name -> bool)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8080/control/status", timeout=2) as resp:
            return json.loads(resp.read()).get("visibility", {})
    except Exception:
        return {}


async def capture_ws(seconds, raw):
    import websockets
    visibility = fetch_visibility()
    channels = {}   # name -> list[(t, v)]
    raw_lines = []
    deadline = time.time() + seconds
    async with websockets.connect(WS_URL) as ws:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            except asyncio.TimeoutError:
                break
            if msg.get("type") == "sample":
                for name, v in msg["values"].items():
                    if visibility.get(name, True):
                        channels.setdefault(name, []).append((msg["t"], v))
            elif msg.get("type") == "log" and raw:
                raw_lines.append((msg["t"], msg["line"]))
    return channels, raw_lines


def capture_serial(seconds, port, baud, raw):
    import serial
    import serial.tools.list_ports
    if port is None:
        candidates = [
            p.device for p in serial.tools.list_ports.comports()
            if re.search(r"CP210|CH340|CH910|USB.SERIAL|Silicon Labs|wch|FTDI|ESP32",
                         (p.description or "") + " " + (p.manufacturer or ""), re.I)
        ]
        if len(candidates) != 1:
            print(f"cannot auto-detect port (found {candidates}); use --port", file=sys.stderr)
            sys.exit(1)
        port = candidates[0]
    channels = {}
    raw_lines = []
    deadline = time.time() + seconds
    with serial.Serial(port, baud, timeout=0.5) as ser:
        while time.time() < deadline:
            line = ser.readline().decode("utf-8", errors="ignore").rstrip("\r\n")
            if not line:
                continue
            now = time.time()
            if raw:
                raw_lines.append((now, line))
            parsed = parse_line(line)
            if parsed:
                for name, v in parsed.items():
                    channels.setdefault(name, []).append((now, v))
    return channels, raw_lines


def downsample(pts, max_points):
    if len(pts) <= max_points:
        return pts
    step = len(pts) / max_points
    return [pts[int(i * step)] for i in range(max_points)]


def format_text(channels, raw_lines, seconds):
    all_ts = [t for pts in channels.values() for t, _ in pts]
    if not all_ts:
        return "no data received - is the device printing, and the monitor connected?"
    t0, t1 = min(all_ts), max(all_ts)
    lines = [
        f"capture  {datetime.fromtimestamp(t0):%H:%M:%S} - "
        f"{datetime.fromtimestamp(t1):%H:%M:%S}  ({seconds:.1f}s requested)"
    ]
    for name in channels:
        pts = channels[name]
        vals = [v for _, v in pts]
        sampled = downsample(pts, MAX_TEXT_POINTS)
        note = f"  [downsampled {len(pts)} -> {len(sampled)}]" if len(sampled) < len(pts) else ""
        lines.append(
            f"== {name} ==  min {min(vals):.1f}  max {max(vals):.1f}  "
            f"last {vals[-1]:.1f}  n={len(pts)}{note}"
        )
        for i in range(0, len(sampled), VALUES_PER_ROW):
            row = sampled[i:i + VALUES_PER_ROW]
            t_rel = f"{row[0][0] - t0:5.1f}"
            lines.append(f"  t={t_rel}s  " + " ".join(f"{v:7.1f}" for _, v in row))
    if raw_lines:
        lines.append("")
        lines.append("== raw serial ==")
        for t, line in raw_lines:
            lines.append(f"  {datetime.fromtimestamp(t):%H:%M:%S}  {line}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5)
    ap.add_argument("--port", default=None, help="serial port for direct fallback mode")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--raw", action="store_true", help="include raw serial lines")
    ap.add_argument("--json", action="store_true", help="JSON output instead of text")
    args = ap.parse_args()

    try:
        channels, raw_lines = asyncio.run(capture_ws(args.seconds, args.raw))
        source = "web_monitor websocket"
    except Exception:
        channels, raw_lines = capture_serial(args.seconds, args.port, args.baud, args.raw)
        source = "direct serial"

    if args.json:
        print(json.dumps({
            "source": source,
            "seconds": args.seconds,
            "channels": {n: [[t, v] for t, v in pts] for n, pts in channels.items()},
            "raw": [[t, l] for t, l in raw_lines],
        }))
    else:
        print(f"[source: {source}]")
        print(format_text(channels, raw_lines, args.seconds))

    sys.exit(0 if channels else 1)


if __name__ == "__main__":
    main()
