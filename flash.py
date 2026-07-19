"""
SerialClip flash - compile + upload an Arduino/ESP32 sketch via arduino-cli,
automatically pausing the running SerialClip monitor (if any) around the
upload so it isn't holding the COM port, then resuming it afterward.

Usage:
    python flash.py --sketch "path\\to\\sketch_dir" [--fqbn esp32:esp32:esp32] [--port COM3]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import serial.tools.list_ports

MONITOR_URL = "http://localhost:8080"


def find_arduino_cli():
    env = os.environ.get("ARDUINO_CLI")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("arduino-cli")
    if on_path:
        return on_path
    bundled = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Programs/arduino-ide/resources/app/lib/backend/resources/arduino-cli.exe"
    )
    if bundled.exists():
        return str(bundled)
    sys.exit(
        "arduino-cli not found. Install Arduino IDE 2.x, add arduino-cli to PATH, "
        "or set the ARDUINO_CLI environment variable to its full path."
    )


ARDUINO_CLI = find_arduino_cli()


def autodetect_port():
    candidates = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if re.search(r"CP210|CH340|CH910|USB.SERIAL|Silicon Labs|wch|FTDI|ESP32", desc, re.I):
            candidates.append(p.device)
    if len(candidates) == 1:
        return candidates[0]
    print("Could not auto-detect a single board; pass --port COMx", file=sys.stderr)
    sys.exit(1)


def monitor_control(action):
    try:
        with urllib.request.urlopen(f"{MONITOR_URL}/control/{action}", timeout=2) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError):
        return None  # web_monitor isn't running - nothing to pause/resume


def run(cmd):
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sketch", required=True, help="path to the sketch directory")
    ap.add_argument("--fqbn", default="esp32:esp32:esp32")
    ap.add_argument("--port", default=None)
    args = ap.parse_args()

    port = args.port or autodetect_port()

    if monitor_control("pause") is not None:
        print(f"[monitor] paused - {port} released")

    ok = run([ARDUINO_CLI, "compile", "--fqbn", args.fqbn, args.sketch])
    if ok:
        ok = run([ARDUINO_CLI, "upload", "-p", port, "--fqbn", args.fqbn, args.sketch])
    else:
        print("compile failed, skipping upload", file=sys.stderr)

    if monitor_control("resume") is not None:
        print("[monitor] resumed")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
