"""Observable Docker entrypoint for Chromium's virtual display."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

DISPLAY = os.getenv("DISPLAY", ":99")
X_SOCKET = Path(f"/tmp/.X11-unix/X{DISPLAY.removeprefix(':')}")


def log(message: str) -> None:
    print(f"[hardcover-sync-entrypoint] {message}", flush=True)


def start_xvfb() -> subprocess.Popen:
    command = [
        "Xvfb",
        DISPLAY,
        "-screen",
        "0",
        "1920x1080x24",
        "-nolisten",
        "tcp",
        "-ac",
    ]
    log(f"starting virtual display {DISPLAY}")
    process = subprocess.Popen(command)
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Xvfb exited with code {process.returncode}")
        if X_SOCKET.exists():
            log("virtual display is ready")
            return process
        time.sleep(0.05)
    process.terminate()
    raise RuntimeError("Xvfb did not become ready within 5 seconds")


def main() -> None:
    command = sys.argv[1:] or ["python", "-u", "main.py"]
    log(f"container starting; command: {' '.join(command)}")
    os.environ["DISPLAY"] = DISPLAY
    try:
        start_xvfb()
    except Exception as exc:
        log(f"fatal display startup error: {exc}")
        raise SystemExit(1) from exc
    log("launching application")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
