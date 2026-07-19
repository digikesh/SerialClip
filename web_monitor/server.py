"""
SerialClip - web-based live serial monitor for Arduino/ESP32 boards.
Snip your sensor data, paste it to your AI.

Serves a black-background, high-contrast live chart at http://localhost:8080
and streams parsed serial samples to it over a websocket. The page has its
own Record button that snapshots the chart canvas straight to the OS
clipboard as a PNG (via the browser's Clipboard API) - no files are read or
written as part of that flow. A Pause button releases the COM port so
another tool (e.g. arduino-cli during a flash) can use it.

Usage:
    python server.py [--port COM5] [--baud 115200]

Serial line formats accepted (same convention as the Arduino IDE Serial Plotter):
    "Label1:1.23,Label2:4.56"
    "1.23 4.56 7.89"          (unnamed columns become ch1, ch2, ch3, ...)
"""

import argparse
import asyncio
import http.server
import json
import re
import sys
import threading
import time
import webbrowser
from pathlib import Path

import serial
import serial.tools.list_ports
import websockets

HTTP_PORT = 8080
WS_PORT = 8765
STATIC_DIR = Path(__file__).parent

LINE_RE = re.compile(r"[,\t]+")


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


def autodetect_port():
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if re.search(r"CP210|CH340|CH910|USB.SERIAL|Silicon Labs|wch|FTDI|ESP32", desc, re.I):
            candidates.append(p.device)
    if len(candidates) == 1:
        return candidates[0]
    all_ports = [p.device for p in serial.tools.list_ports.comports()]
    print("Could not auto-detect a single board. Available ports:", all_ports)
    print("Re-run with --port COMx")
    sys.exit(1)


class SerialBridge:
    def __init__(self, port, baud, loop, clients):
        self.port = port
        self.baud = baud
        self.loop = loop
        self.clients = clients
        self.paused = False
        self.visibility = {}  # channel name -> bool, mirrored from the browser UI
        self._stop = False
        self.ser = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop = True
        if self.ser and self.ser.is_open:
            self.ser.close()

    def pause(self):
        self.paused = True
        if self.ser and self.ser.is_open:
            self.ser.close()
        print(f"[paused] {self.port} released - safe to flash.")
        self._broadcast({"type": "status", "paused": True})

    def resume(self):
        self.paused = False
        print(f"[resuming] reopening {self.port} ...")
        self._broadcast({"type": "status", "paused": False})

    def write_line(self, text):
        if self.ser and self.ser.is_open and not self.paused:
            try:
                self.ser.write((text + "\n").encode("utf-8"))
            except (serial.SerialException, OSError):
                pass

    def _broadcast(self, msg):
        data = json.dumps(msg)
        asyncio.run_coroutine_threadsafe(self._send_all(data), self.loop)

    async def _send_all(self, data):
        dead = set()
        for ws in list(self.clients):
            try:
                await ws.send(data)
            except Exception:
                dead.add(ws)
        self.clients.difference_update(dead)

    def _run(self):
        while not self._stop:
            if self.paused:
                time.sleep(0.3)
                continue
            try:
                if self.ser is None or not self.ser.is_open:
                    self.ser = serial.Serial(self.port, self.baud, timeout=1)
                raw = self.ser.readline().decode("utf-8", errors="ignore")
                stripped = raw.rstrip("\r\n")
                if stripped:
                    now = time.time()
                    self._broadcast({"type": "log", "t": now, "line": stripped})
                    parsed = parse_line(stripped)
                    if parsed:
                        self._broadcast({"type": "sample", "t": now, "values": parsed})
            except (serial.SerialException, OSError):
                self.ser = None
                time.sleep(0.5)


async def handler(websocket, bridge, clients):
    clients.add(websocket)
    bridge._broadcast({"type": "status", "paused": bridge.paused})
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue
            if msg.get("cmd") == "pause":
                bridge.pause()
            elif msg.get("cmd") == "resume":
                bridge.resume()
            elif msg.get("cmd") == "write":
                bridge.write_line(str(msg.get("data", "")))
            elif msg.get("cmd") == "visibility":
                channels = msg.get("channels", {})
                if isinstance(channels, dict):
                    bridge.visibility = {str(k): bool(v) for k, v in channels.items()}
    finally:
        clients.discard(websocket)


def make_handler(bridge):
    class ControlHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(STATIC_DIR), **kw)

        def _send_json(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/control/pause":
                bridge.pause()
                self._send_json({"paused": True, "port": bridge.port})
            elif self.path == "/control/resume":
                bridge.resume()
                self._send_json({"paused": False, "port": bridge.port})
            elif self.path == "/control/status":
                self._send_json({
                    "paused": bridge.paused,
                    "port": bridge.port,
                    "visibility": bridge.visibility,
                })
            else:
                super().do_GET()

    return ControlHandler


def serve_http(bridge):
    httpd = http.server.ThreadingHTTPServer(("localhost", HTTP_PORT), make_handler(bridge))
    httpd.serve_forever()


async def main_async(args):
    loop = asyncio.get_running_loop()
    clients = set()
    port = args.port or autodetect_port()
    bridge = SerialBridge(port, args.baud, loop, clients)
    bridge.start()

    threading.Thread(target=serve_http, args=(bridge,), daemon=True).start()
    url = f"http://localhost:{HTTP_PORT}/index.html"
    print(f"Serial: {port} @ {args.baud}")
    print(f"Open:   {url}")
    webbrowser.open(url)

    async with websockets.serve(lambda ws: handler(ws, bridge, clients), "localhost", WS_PORT):
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
