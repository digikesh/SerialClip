"""
Live terminal serial plotter for Arduino/ESP32 boards, with a "record" key that
copies a real PNG chart of the recent data straight onto the Windows clipboard
so it can be pasted (Ctrl+V) directly into a chat as an image attachment.

Usage:
    python live_plot.py [--port COM5] [--baud 115200] [--window 30]

Serial line formats accepted (same convention as the Arduino IDE Serial Plotter):
    "Label1:1.23,Label2:4.56"
    "1.23 4.56 7.89"          (unnamed columns become ch1, ch2, ch3, ...)

Keys (while the window is focused):
    r  - record: copies a PNG chart of everything since the last record
         (or since start) to the clipboard as an image.
    p  - pause / resume: closes the serial port so another tool (e.g.
         arduino-cli during a flash) can use it; press again to reconnect.
    q  - quit.
"""

import argparse
import io
import msvcrt
import os
import re
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime

if sys.platform == "win32":
    # plotext draws with Unicode braille/box characters that crash under the
    # default Windows console codepage (cp1252) - force UTF-8 for this process
    # and the console it's running in.
    os.system("chcp 65001 >NUL")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as mpl
import plotext as plt
import serial
import serial.tools.list_ports
import win32clipboard
from PIL import Image

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


class SerialReader:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.data = defaultdict(list)  # name -> list[(epoch_time, value)]
        self.lock = threading.Lock()
        self.paused = False
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
        print(f"\n[paused] {self.port} released - safe to flash. Press 'p' again to resume.")

    def resume(self):
        self.paused = False
        print(f"\n[resuming] reopening {self.port} ...")

    def _run(self):
        while not self._stop:
            if self.paused:
                time.sleep(0.3)
                continue
            try:
                if self.ser is None or not self.ser.is_open:
                    self.ser = serial.Serial(self.port, self.baud, timeout=1)
                line = self.ser.readline().decode("utf-8", errors="ignore")
                parsed = parse_line(line)
                if parsed:
                    now = time.time()
                    with self.lock:
                        for name, val in parsed.items():
                            self.data[name].append((now, val))
            except (serial.SerialException, OSError):
                self.ser = None
                time.sleep(0.5)

    def snapshot(self, since):
        with self.lock:
            return {
                name: [(t, v) for t, v in points if t >= since]
                for name, points in self.data.items()
            }


def render_live(reader, window_seconds, port_label):
    now = time.time()
    plt.clt()
    plt.cld()
    snap = reader.snapshot(now - window_seconds)
    for name, points in snap.items():
        if not points:
            continue
        xs = [t - now for t, _ in points]
        ys = [v for _, v in points]
        plt.plot(xs, ys, label=name)
    status = "PAUSED" if reader.paused else "live"
    plt.title(f"{port_label}  [{status}]  (r=record  p=pause  q=quit)")
    plt.xlabel("seconds ago")
    plt.plotsize(100, 30)
    plt.show()


def copy_chart_to_clipboard(data_since_last, port_label, start_dt, end_dt):
    fig, ax = mpl.subplots(figsize=(9, 5), dpi=150)
    n_samples = 0
    for name, points in data_since_last.items():
        if not points:
            continue
        xs = [datetime.fromtimestamp(t) for t, _ in points]
        ys = [v for _, v in points]
        n_samples = max(n_samples, len(points))
        ax.plot(xs, ys, label=name, linewidth=1.2)
    ax.set_title(
        f"{port_label}  {start_dt:%H:%M:%S} - {end_dt:%H:%M:%S}  ({n_samples} samples/ch)"
    )
    ax.set_xlabel("time")
    ax.set_ylabel("value")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    mpl.close(fig)
    buf.seek(0)

    img = Image.open(buf).convert("RGB")
    out = io.BytesIO()
    img.save(out, "BMP")
    dib = out.getvalue()[14:]  # strip the 14-byte BMP file header -> DIB

    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
    win32clipboard.CloseClipboard()
    return n_samples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--window", type=float, default=30, help="seconds of history shown live")
    args = ap.parse_args()

    port = args.port or autodetect_port()
    print(f"Connecting to {port} @ {args.baud}...")

    reader = SerialReader(port, args.baud)
    reader.start()

    last_record_time = time.time()
    start_time = last_record_time

    try:
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                if key == "q":
                    break
                elif key == "p":
                    if reader.paused:
                        reader.resume()
                    else:
                        reader.pause()
                elif key == "r":
                    now = time.time()
                    snap = reader.snapshot(last_record_time)
                    if not any(snap.values()):
                        print("\n[record] no new data since last record.")
                    else:
                        n = copy_chart_to_clipboard(
                            snap, port, datetime.fromtimestamp(last_record_time), datetime.fromtimestamp(now)
                        )
                        print(f"\n[record] copied chart to clipboard ({n} samples/channel). "
                              f"Switch to Claude Code and paste (Ctrl+V).")
                    last_record_time = now

            render_live(reader, args.window, port)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        print("\nStopped.")


if __name__ == "__main__":
    main()
