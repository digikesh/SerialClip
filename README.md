# SerialClip

Snip your sensor data, paste it to your AI.

Made by [creativetech.wtf](https://www.creativetech.wtf) — demystifying tech in
design, from Singapore. *"Pondering about creativity, tech, and whatever the
f\*ck lies in between."*

A minimal, black-and-white, browser-based live monitor for Arduino/ESP32 serial
data — built to make hardware experiments easy to share with AI coding agents
(Claude Code, Cursor, etc.): select a slice of the timeline, copy, paste. The
agent gets a chart image *and* the raw numbers.

## Install

```
python install.py
```

Installs Python deps (`pyserial`, `websockets`), locates `arduino-cli`
(PATH → `ARDUINO_CLI` env var → Arduino IDE 2.x bundle), and writes
`monitor.bat` / `capture.bat` / `flash.bat` launchers pinned to your Python.

## The pieces

| Tool | What it does |
|---|---|
| `web_monitor/server.py` | Serial ↔ browser bridge. Serves the UI at `http://localhost:8080`, streams parsed samples over `ws://localhost:8765`, exposes HTTP control endpoints. |
| `web_monitor/index.html` | The UI. Live canvas chart, timeline segments, serial terminal with send box. Single file, no runtime dependencies. |
| `capture.py` | CLI: grab N seconds of live data as formatted text/JSON on stdout. Built for AI sessions — taps the websocket without disturbing anything. |
| `flash.py` | CLI: compile + upload a sketch via arduino-cli, auto-pausing the monitor around the upload so the COM port is free. |
| `install.py` | The installer above. |
| `live_plot.py` | Legacy terminal-based plotter (superseded by the web monitor). |

## Using the monitor UI

```
monitor.bat --port COM3        (or: python web_monitor/server.py --port COM3)
```

The sketch just prints `Name:value` pairs, comma-separated, one line per sample
— the same convention as the Arduino IDE Serial Plotter:

```cpp
Serial.println("LDR:512");                      // one channel
Serial.print("LDR:"); Serial.print(a);
Serial.print(",A35:"); Serial.println(b);       // several channels
```

Anything that doesn't parse as data (debug prints) shows in the terminal panel
but isn't plotted.

- **The chart is a tape recorder.** Click it (or the pause button) to stop the
  tape: the display freezes and incoming samples are dropped — what you see is
  exactly what's stored. Resume splices the timeline back together; paused
  stretches don't exist on the time axis.
- **Drag on the chart** to create a labeled, colored segment (Audacity-style).
  Rename it in its chip below the chart; delete with the ×. Segments that
  scroll fully off the buffer are auto-deleted.
- **COPY SEGMENTS** puts one chart image (all segments highlighted) plus a
  per-segment text block — stats and raw value rows — on the clipboard.
  Paste the text into a terminal agent, the image into anything that takes
  images. (Terminal agents read only the text side of the clipboard.)
- **Legend checkboxes** show/hide channels. Hidden channels are excluded from
  auto-scaling, segment exports, and `capture.py`.
- **buffer (sec)** sets both the visible window and the retention limit.
- **DISCONNECT** (chain icon, next to the status badge) releases the COM port
  so a flash can use it — the only control that touches the port.
- The **terminal panel** shows every raw line; the input box sends a line to
  the device (newline-terminated).

## Control API (for scripts and AI agents)

```
GET http://localhost:8080/control/status   -> {"paused": bool, "port": "COM3", "visibility": {...}}
GET http://localhost:8080/control/pause    -> release the COM port
GET http://localhost:8080/control/resume   -> reopen the COM port
```

## capture.py

```
capture.bat --seconds 5          formatted stats + value rows
capture.bat --seconds 5 --raw    + raw serial lines
capture.bat --seconds 5 --json   machine-readable
```

Exit code 1 means no data arrived. Falls back to opening the serial port
directly when the monitor isn't running (no visibility filtering there).

## flash.py

```
flash.bat --sketch "path\to\SketchDir" [--port COM3] [--fqbn esp32:esp32:esp32]
```

Pauses the monitor (if running), compiles, uploads, resumes — win or lose.

## Troubleshooting

- **`Wrong boot mode detected (0x13)`** on upload: the board didn't auto-enter
  its bootloader. Hold the BOOT button as the upload starts.
- **Chart frozen after resume**: it stays frozen until the first fresh sample
  arrives (that's what splices the gap) — check the device is actually printing.
- **Clipboard paste shows text but no image**: terminal apps only read the text
  clipboard format; paste the image into Cursor/Paint/etc. instead.
- **Port busy**: only one process can hold a COM port — use DISCONNECT (or
  `/control/pause`) before opening it elsewhere.

## Architecture notes

- One Python thread owns the serial port and broadcasts every line as
  `{type:"log"}` plus, when parseable, `{type:"sample"}` over the websocket.
  Any number of listeners (browser, capture.py) can attach.
- The chart runs on a spliced "data time" axis: gaps from pause/disconnect are
  subtracted out (deliberate stops splice exactly; unexpected dropouts > 0.5s
  are compressed to one sample interval).
- UI design tokens (colors, type, spacing) live once in the CSS `:root`; the
  canvas reads the same tokens at runtime. Icons are inlined pixelarticons
  (`npm i pixelarticons` in `web_monitor/` is only needed to source new ones).
