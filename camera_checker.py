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

Design notes:
  - Uses the `ffmpeg` CLI (via subprocess) to do the actual stream probing
    and frame grab, instead of a Python video library like OpenCV. ffmpeg
    understands rtsp://, http:// and https:// MJPEG streams out of the box,
    so one tool covers both "real" RTSP cameras and the HTTP/MJPEG cameras
    clients sometimes mislabel as RTSP, with no extra codec setup required.
  - Does a quick TCP reachability check first (socket.create_connection)
    before asking ffmpeg to decode anything, so we can tell "host is down /
    wrong IP / firewalled" apart from "host is up but stream/codec is
    broken" - these two failure modes need different next steps when
    talking to a client, so the tool records them differently.
  - No third-party Python dependencies - only the standard library plus the
    `ffmpeg` binary on PATH. Kept intentionally simple: no database, no web
    UI, just files a support engineer (or another script) can act on.
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

# Fallback ports used when a URL doesn't specify one explicitly
# (e.g. "rtsp://host/stream" with no ":554").
DEFAULT_PORTS = {"rtsp": 554, "http": 80, "https": 443}


@dataclass
class CameraResult:
    """The outcome of checking a single camera, written out to results.csv/json."""
    camera_name: str
    url: str
    scheme: str            # URL scheme actually used, e.g. "rtsp", "http"
    protocol_note: str     # non-empty if the scheme isn't "rtsp" (see check_camera)
    reachable: bool        # True once the TCP reachability check passes
    status: str            # "VALID" or "INVALID"
    error: str             # human-readable reason when status == "INVALID"
    screenshot_path: str   # populated when status == "VALID"
    checked_at: str        # UTC timestamp (ISO 8601) of when this check ran


def setup_logging(output_dir: Path) -> None:
    """Log everything (DEBUG+) to <output_dir>/scan.log, but keep the
    console output at INFO so it doesn't drown out the summary table."""
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
    # handlers[1] is the console StreamHandler above; keep it less noisy
    # than the log file, which keeps full DEBUG detail.
    logging.getLogger().handlers[1].setLevel(logging.INFO)


def sanitize_filename(name: str) -> str:
    """Turn a camera name into a safe filename, e.g. 'Loading Dock #1' -> 'Loading_Dock_1'."""
    keep = [c if c.isalnum() else "_" for c in name.strip()]
    return "".join(keep).strip("_") or "camera"


def load_cameras(csv_path: Path) -> list[dict]:
    """Read the input CSV into a list of {"camera_name": ..., "url": ...} dicts.

    Header names are matched loosely (case-insensitive substring match for
    "name" and "url") so files like "Camera Name,RTSP URL" work without the
    caller needing to rename columns first.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"No header row found in {csv_path}")

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
    """Quick TCP connect test - answers "is the host even on the network"
    before we bother asking ffmpeg to decode anything from it.

    Returns (True, "") if the port accepted a connection, otherwise
    (False, <reason>). Separating this from the actual stream check lets us
    tell "host is down / wrong IP / firewalled" apart from "host is up but
    the stream itself is broken" - two problems with very different fixes.
    """
    try:
        with socket.create_connection((hostname, port), timeout=timeout):
            return True, ""
    except socket.gaierror as e:
        # Hostname didn't resolve at all (bad DNS name, typo, etc).
        return False, f"DNS/hostname resolution failed for {hostname}: {e}"
    except (ConnectionRefusedError, OSError) as e:
        # Host resolved but refused/timed out - wrong port, firewall, host down, etc.
        return False, f"Could not open TCP connection to {hostname}:{port}: {e}"


def capture_frame(url: str, screenshot_path: Path, capture_timeout: float) -> tuple[bool, str]:
    """Ask ffmpeg to grab a single frame from the stream. Returns (success, error_message).

    ffmpeg is run as a subprocess rather than using a Python video library
    (e.g. OpenCV) because it natively understands rtsp://, http:// and
    https:// MJPEG streams with no extra codec setup, and keeps this tool
    at zero third-party Python dependencies.
    """
    cmd = [
        "ffmpeg",
        "-y",                  # overwrite screenshot_path if it already exists
        "-loglevel", "error",  # keep ffmpeg's own logging to just real errors
        "-i", url,
        "-frames:v", "1",      # grab exactly one frame...
        "-q:v", "2",           # ...at high JPEG quality
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
        # Connection opened but never produced a usable frame in time -
        # different from a hard failure, so worth calling out separately.
        return False, f"ffmpeg timed out after {capture_timeout}s (stream connected but never produced usable video)"
    except FileNotFoundError:
        # ffmpeg itself isn't installed / not on PATH.
        return False, "ffmpeg executable not found on PATH. Install ffmpeg and retry."

    if result.returncode == 0 and screenshot_path.exists() and screenshot_path.stat().st_size > 0:
        return True, ""

    # ffmpeg can leave behind a zero-byte file on failure - clean it up so a
    # broken camera never gets mistaken for a captured screenshot.
    if screenshot_path.exists() and screenshot_path.stat().st_size == 0:
        screenshot_path.unlink()

    # ffmpeg's last stderr line is usually the actual reason (e.g. "Server
    # returned 404 Not Found"), so surface that instead of just the exit code.
    stderr = result.stderr.strip().splitlines()
    error_message = stderr[-1] if stderr else f"ffmpeg exited with code {result.returncode}"
    return False, error_message


def check_camera(camera_name: str, url: str, output_dir: Path,
                  connect_timeout: float, capture_timeout: float) -> CameraResult:
    """Run the full check for one camera: parse -> reachability -> frame grab.

    Returns a CameraResult with status VALID (screenshot saved) or INVALID
    (error explains exactly what went wrong and where).
    """
    logging.info("Checking camera '%s' -> %s", camera_name, url)
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()

    # Flag (but don't reject) URLs that aren't true RTSP. Clients often send
    # HTTP/MJPEG URLs while calling them "RTSP URLs" - the two sample URLs in
    # this brief are exactly that case. A working HTTP/MJPEG camera is still
    # a working camera, so we test it and just note the mismatch rather than
    # marking it INVALID outright.
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
        # Malformed URL - nothing to even attempt a connection to.
        return CameraResult(
            camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
            reachable=False, status="INVALID",
            error=f"Could not parse a hostname/IP out of URL: {url}",
            screenshot_path="", checked_at=datetime.now(timezone.utc).isoformat(),
        )

    # Step 1: is the host even reachable on the network?
    reachable, reach_error = check_reachability(hostname, port, connect_timeout)
    if not reachable:
        logging.warning("Camera '%s' unreachable: %s", camera_name, reach_error)
        return CameraResult(
            camera_name=camera_name, url=url, scheme=scheme, protocol_note=protocol_note,
            reachable=False, status="INVALID", error=reach_error,
            screenshot_path="", checked_at=datetime.now(timezone.utc).isoformat(),
        )

    # Step 2: host is reachable, so try to actually pull a frame from it.
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
    """Write the full result set to both results.csv and results.json."""
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
    """Print an aligned text table of results, plus any protocol notes below it.

    Protocol notes are printed separately (not as a table column) because
    they're full sentences and would break the table's column alignment.
    """
    name_w = max(len("Camera Name"), max((len(r.camera_name) for r in results), default=0))
    status_w = len("STATUS")
    detail_w = 60

    def detail_for(r: CameraResult) -> str:
        # Show the screenshot path for a VALID result, or the error for an
        # INVALID one - whichever is the useful "so what" for that row.
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
    """CLI entry point - this is the whole tool's flow, top to bottom:

    1. Parse CLI args (which CSV, where to write output, timeouts).
    2. Set up logging (console + scan.log).
    3. Load the camera list from the input CSV.
    4. Check every camera (reachability -> ffmpeg frame grab).
    5. Write results.csv / results.json and print the summary table.
    """
    # --- 1. CLI args -----------------------------------------------------
    parser = argparse.ArgumentParser(description="Scan a list of camera stream URLs and validate them.")
    parser.add_argument("--input", default="cameras.csv", help="CSV file with camera_name,url columns")
    parser.add_argument("--output-dir", default="output", help="Directory to write screenshots and reports")
    parser.add_argument("--connect-timeout", type=float, default=5.0, help="TCP reachability timeout (seconds)")
    parser.add_argument("--capture-timeout", type=float, default=15.0, help="ffmpeg frame-grab timeout (seconds)")
    args = parser.parse_args()

    # --- 2. Logging: everything to scan.log, INFO+ to the console --------
    output_dir = Path(args.output_dir)
    setup_logging(output_dir)

    # --- 3. Load the camera list ------------------------------------------
    input_path = Path(args.input)
    if not input_path.exists():
        logging.error("Input file not found: %s", input_path)
        return 1

    cameras = load_cameras(input_path)
    if not cameras:
        logging.error("No cameras found in %s", input_path)
        return 1

    # --- 4. Check every camera (this is where the real work happens) -----
    # Each call to check_camera() does: parse URL -> TCP reachability check
    # -> ffmpeg frame grab -> return a VALID/INVALID CameraResult.
    results = [
        check_camera(c["camera_name"], c["url"], output_dir, args.connect_timeout, args.capture_timeout)
        for c in cameras
    ]

    # --- 5. Write the report files and print the console summary ---------
    write_reports(results, output_dir)
    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
