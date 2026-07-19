"""
SerialClip installer - one-shot setup for the toolchain.

    python install.py

- verifies Python >= 3.9
- installs Python dependencies (pyserial, websockets)
- locates arduino-cli (PATH, ARDUINO_CLI env var, or the Arduino IDE bundle)
- writes monitor.bat / capture.bat / flash.bat launchers pinned to this
  Python interpreter, next to this script
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPS = ["pyserial", "websockets"]


def step(label, ok, detail=""):
    mark = "OK " if ok else "!! "
    print(f"  [{mark}] {label}" + (f"  {detail}" if detail else ""))
    return ok


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
    return None


def write_launcher(name, target, extra_args=""):
    bat = HERE / f"{name}.bat"
    bat.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "%~dp0{target}"{extra_args} %*\r\n',
        encoding="ascii",
    )
    return bat


def main():
    print("SerialClip installer")
    print(f"  python: {sys.executable}")

    if not step(
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        sys.version_info >= (3, 9),
        "(need >= 3.9)",
    ):
        sys.exit(1)

    print(f"  installing: {', '.join(DEPS)} ...")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", *DEPS],
        capture_output=True, text=True,
    )
    if not step("python dependencies", r.returncode == 0):
        print(r.stderr)
        sys.exit(1)

    cli = find_arduino_cli()
    step(
        "arduino-cli", cli is not None,
        cli or "NOT FOUND - install Arduino IDE 2.x, or put arduino-cli on PATH, "
               "or set ARDUINO_CLI. Flashing won't work until then; monitoring will.",
    )
    if cli:
        r = subprocess.run([cli, "core", "list"], capture_output=True, text=True)
        has_esp32 = "esp32" in r.stdout.lower()
        step(
            "esp32 board core", has_esp32,
            "" if has_esp32 else "not installed - run: arduino-cli core install esp32:esp32",
        )

    for name, target in [
        ("monitor", "web_monitor\\server.py"),
        ("capture", "capture.py"),
        ("flash", "flash.py"),
    ]:
        bat = write_launcher(name, target)
        step(f"launcher {bat.name}", True, str(bat))

    print()
    print("done. quickstart:")
    print(f'  "{HERE}\\monitor.bat" --port COM3     start the live monitor + browser UI')
    print(f'  "{HERE}\\capture.bat" --seconds 5     grab 5s of data as text (for AI use)')
    print(f'  "{HERE}\\flash.bat" --sketch <dir>    compile + upload, auto port handoff')


if __name__ == "__main__":
    main()
