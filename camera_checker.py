#!/usr/bin/env python3
"""
camera_checker.py

Protex AI take-home tool.

Reads a list of client-supplied camera stream URLs, checks whether each one
is actually reachable and produces a usable video stream, and records the
result:

  - VALID   -> a screenshot is captured and saved.
  - INVALID -> the reason (network unreachable, protocol/stream error, or
               timeout) is recorded so a support engineer can act on it.

Design notes (kept deliberately simple, see docs/internal_howto.md):
  - Uses the `ffmpeg` CLI (via subprocess) to do the actual stream probing
    and frame grab. ffmpeg understands rtsp://, http:// and https:// MJPEG
    streams out of the box, so one tool covers both "real" RTSP cameras and
    the HTTP/MJPEG cameras clients sometimes mislabel as RTSP.
  - Does a quick TCP reachability check first (socket.create_connection)
    so we can tell "host is down / wrong IP / firewalled" apart from
    "host is up but stream/codec is broken" - these need different next
    steps when talking to a client.
  - No third-party Python dependencies. Only requirement is the `ffmpeg`
    binary on PATH.
"""

import argparse
import csv
import json
import logging
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PORTS = {"rtsp": 554, "http": 80, "https": 443}


@dataclass
class CameraResult:
    camera_name: str
    url: str
    scheme: str
    protocol_note: str
    reachable: bool
    status: str  # "VALID" or "INVALID"
    error: str
    screenshot_path: str
    checked_at: str


def setup_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = output_dir / "scan.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Keep the console less noisy than the log file.
    logging.getLogger().handlers[1].setLevel(logging.INFO)


def sanitize_filename(name: str) -> str:
    keep = [c if c.isalnum() else "_" for c in name.strip()]
    return "".join(keep).strip("_") or "camera"


def load_cameras(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {csv_path}")

        # Be tolerant of header naming (e.g. "Camera Name", "RTSP URL").
        name_col = next((c for c in reader.fieldnames if "name" in c.lower()), None)
        url_col = next((c for c in reader.fieldnames if "url" in c.lower()), None)
        if not name_col or not url_col:
            raise ValueError(
                f"Could not find name/url columns in {csv_path}. "
                f"Found columns: {reader.fieldnames}"
            )

        rows = []
        for row in reader:
            rows.append({"camera_name": row[name_col].strip(), "url": row[url_col].strip()})
        return rows


def check_reachability(hostname: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((hostname, port), timeout=timeout):
            return True, ""
    except socket.gaierror as e:
        return False, f"DNS/hostname resolution failed for {hostname}: {e}"
    except (ConnectionRefusedError, OSError) as e:
        return False, f"Could not open TCP connection to {hostname}:{port}: {e}"


def capture_frame(url: str, screenshot_path: Path, capture_timeout: float) -> tuple[bool, str]:
    """Ask ffmpeg to grab a single frame from the stream. Returns (success, error_message)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "error",
        "-i", url,
        "-frames:v", "1",
        "-q:v", "2",
        str(screenshot_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=capture_timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg timed out after {capture_timeout}s (stream connected but never produced usable video)"
    except FileNotFoundError:
        return False, "ffmpeg executable not found on PATH. Install ffmpeg and retry."

    if result.returncode == 0 and screenshot_path.exists() and screenshot_path.stat().st_size > 0:
        return True, ""

    # Clean up any empty/partial file ffmpeg may have left behind.
    if screenshot_path.exists() and screenshot_path.stat().st_size == 0:
        screenshot_path.unlink()

    stderr = result.stderr.strip().splitlines()
    error_message = stderr[-1] if stderr else f"ffmpeg exited with code {result.returncode}"
    return False, error_message


def check_camera(camera_name: str, url: str, output_dir: Path,
                  connect_timeout: float, capture_timeout: float) -> CameraResult:
    logging.info("Checking camera '%s' -> %s", camera_name, url)
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    protocol_note = ""
    if scheme in ("http", "https"):
        protocol_note = (
            "URL uses HTTP(S), not RTSP. This is likely an MJPEG-over-HTTP camera "
            "(common with older/legacy models). It can still be tested, but flag "
            "to the client that this is not a true RTSP stream."
        )
    elif scheme != "rtsp":
        protocol_note = f"Unrecognized/unsupported scheme '{scheme}'."

    hostname = parsed.hostname
    port = parsed.port or DEFAULT_PORTS.get(scheme, 80)

    if not hostname:
        return CameraResult(
            camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
            reachable=False, status="INVALID",
            error=f"Could not parse a hostname/IP out of URL: {url}",
            screenshot_path="", checked_at=datetime.now(timezone.utc).isoformat(),
        )

    reachable, reach_error = check_reachability(hostname, port, connect_timeout)
    if not reachable:
        logging.warning("Camera '%s' unreachable: %s", camera_name, reach_error)
        return CameraResult(
            camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
            reachable=False, status="INVALID", error=reach_error,
            screenshot_path="", checked_at=datetime.now(timezone.utc).isoformat(),
        )

    screenshot_path = output_dir / "screenshots" / f"{sanitize_filename(camera_name)}.jpg"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)

    success, error = capture_frame(url, screenshot_path, capture_timeout)
    if success:
        logging.info("Camera '%s' OK -> screenshot saved to %s", camera_name, screenshot_path)
        return CameraResult(
            camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
            reachable=True, status="VALID", error="",
            screenshot_path=str(screenshot_path), checked_at=datetime.now(timezone.utc).isoformat(),
        )

    logging.warning("Camera '%s' stream error: %s", camera_name, error)
    return CameraResult(
        camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
        reachable=True, status="INVALID", error=error,
        screenshot_path="", checked_at=datetime.now(timezone.utc).isoformat(),
    )


def write_reports(results: list[CameraResult], output_dir: Path) -> None:
    csv_path = output_dir / "results.csv"
    json_path = output_dir / "results.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()) if results else [])
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))

    with json_path.open("w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)

    logging.info("Wrote %s and %s", csv_path, json_path)


def print_summary(results: list[CameraResult]) -> None:
    """Print an aligned text table of results, plus any protocol notes below it."""
    name_w = max(len("Camera Name"), max((len(r.camera_name) for r in results), default=0))
    status_w = len("STATUS")
    detail_w = 60

    def detail_for(r: CameraResult) -> str:
        raw = r.screenshot_path if r.status == "VALID" else r.error
        return textwrap.shorten(raw or "-", width=detail_w, placeholder="...")

    header = f"{'CAMERA NAME'.ljust(name_w)}  {'STATUS'.ljust(status_w)}  {'SCREENSHOT / ERROR'.ljust(detail_w)}"
    print("\n=== Camera Scan Summary ===")
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r.camera_name.ljust(name_w)}  {r.status.ljust(status_w)}  {detail_for(r).ljust(detail_w)}")

    notes = [(r.camera_name, r.protocol_note) for r in results if r.protocol_note]
    if notes:
        print("\nProtocol notes:")
        for camera_name, note in notes:
            print(f"  - {camera_name}: {note}")

    valid_count = sum(1 for r in results if r.status == "VALID")
    print(f"\n{valid_count}/{len(results)} cameras validated successfully.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a list of camera stream URLs and validate them.")
    parser.add_argument("--input", default="cameras.csv", help="CSV file with camera_name,url columns")
    parser.add_argument("--output-dir", default="output", help="Directory to write screenshots and reports")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="TCP reachability timeout (seconds)")
    parser.add_argument("--capture-timeout", type=float, default=15.0, help="ffmpeg frame-grab timeout (seconds)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    setup_logging(output_dir)

    input_path = Path(args.input)
    if not input_path.exists():
        logging.error("Input file not found: %s", input_path)
        return 1

    cameras = load_cameras(input_path)
    if not cameras:
        logging.error("No cameras found in %s", input_path)
        return 1

    results = [
        check_camera(c["camera_name"], c["url"], output_dir, args.connect_timeout, args.capture_timeout)
        for c in cameras
    ]

    write_reports(results, output_dir)
    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
