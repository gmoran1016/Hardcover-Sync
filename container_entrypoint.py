"""Observable Docker entrypoint for Chromium's virtual display."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

DISPLAY = os.getenv("DISPLAY", ":99")


def log(message: str) -> None:
    print(f"[hardcover-sync-entrypoint] {message}", flush=True)


def display_paths(display: str) -> tuple[Path, Path]:
    """Return Xvfb's lock and Unix socket paths for a display."""
    display_number = display.removeprefix(":").split(".", 1)[0]
    return (
        Path(f"/tmp/.X{display_number}-lock"),
        Path(f"/tmp/.X11-unix/X{display_number}"),
    )


def clear_display_artifacts(display: str) -> None:
    """Remove stale lock and socket files left by an earlier Xvfb process."""
    for path in display_paths(display):
        try:
            path.unlink()
            log(f"removed stale display artifact {path}")
        except FileNotFoundError:
            pass


def wait_for_display(
    process: subprocess.Popen,
    socket_path: Path,
    timeout: float = 5.0,
) -> None:
    """Wait until Xvfb is alive and has created its Unix socket."""
    deadline = time.monotonic() + timeout
    while True:
        status = process.poll()
        if status is not None:
            raise RuntimeError(f"Xvfb exited with code {status}")
        if socket_path.exists():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Xvfb did not become ready within {timeout:g} seconds")
        time.sleep(0.05)


def stop_process(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """Stop a child process, escalating to kill after a bounded wait."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def start_xvfb() -> subprocess.Popen:
    clear_display_artifacts(DISPLAY)
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
    _, socket_path = display_paths(DISPLAY)
    try:
        wait_for_display(process, socket_path)
    except Exception:
        stop_process(process)
        raise
    return process


def supervise(xvfb: subprocess.Popen, application: subprocess.Popen) -> int:
    """Monitor both children and stop the survivor when either one exits."""
    while True:
        application_status = application.poll()
        if application_status is not None:
            stop_process(xvfb)
            return application_status

        xvfb_status = xvfb.poll()
        if xvfb_status is not None:
            stop_process(application)
            raise RuntimeError(f"Xvfb exited with code {xvfb_status}")

        time.sleep(0.1)


def main() -> None:
    command = sys.argv[1:] or ["python", "-u", "main.py"]
    log(f"container starting; command: {' '.join(command)}")
    os.environ["DISPLAY"] = DISPLAY
    try:
        xvfb = start_xvfb()
    except Exception as exc:
        log(f"fatal display startup error: {exc}")
        raise SystemExit(1) from exc
    log("virtual display is ready")
    log("launching application")
    try:
        application = subprocess.Popen(command)
    except Exception as exc:
        stop_process(xvfb)
        log(f"fatal application startup error: {exc}")
        raise SystemExit(1) from exc
    try:
        status = supervise(xvfb, application)
    except RuntimeError as exc:
        log(f"fatal runtime error: {exc}")
        raise SystemExit(1) from exc
    raise SystemExit(status)


if __name__ == "__main__":
    main()
