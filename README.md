# Camera Stream Validator

A command-line tool that takes a list of client-supplied camera stream
URLs, checks whether each one is actually reachable and streaming valid
video, and produces:

- A **screenshot** for every stream that works.
- A **clear error reason** for every stream that doesn't (unreachable host,
  bad path/404, DNS failure, timeout, unsupported codec, etc).
- A **CSV report** and a **log file** summarizing the run.

Built for the Protex AI take-home exercise (Technical Support & Solutions
Engineer, Round 3). See `docs/` for:
- `internal_howto.md` — how a Protex engineer runs this on future installs
- `client_report_template.md` — blank template used to write up the status
  of a client's camera list after running the tool

## Quick start

```bash
# 1. Install ffmpeg (the only dependency - no Python packages needed)
brew install ffmpeg          # macOS
# sudo apt-get install ffmpeg  # Ubuntu/Debian

# 2. Run against the sample camera list from the brief
python3 camera_checker.py --input cameras.csv --output-dir output

# 3. Check the results
cat output/results.csv
open output/screenshots/          # macOS
```

## Input format

A CSV file with a name column and a URL column (header names are matched
loosely, so "Camera Name" / "RTSP URL" also work):

```csv
camera_name,url
Exterior Street View,http://61.211.241.239/nphMotionJpeg?Resolution=320x240&Quality=Standard
Outside Junction,http://217.180.234.228/mjpg/video.mjpg
```

## Output

Running the tool creates (under `--output-dir`, default `output/`):

- `screenshots/<camera_name>.jpg` — one frame captured from each valid stream.
- `results.csv` / `results.json` — one row per camera: status, protocol
  notes, error message, screenshot path, timestamp.
- `scan.log` — detailed debug log of the whole run.

Saved results from a real run against the two URLs in the brief are in
`sample_output/`. Deliberately broken test URLs and their results (used to
demonstrate error handling) are kept separately in `test_data/`.

## How it works (short version)

```
CSV list of camera URLs
        │
        ▼
  Parse URL (scheme, host, port)
        │
        ▼
  TCP reachability check ──── unreachable ──▶ INVALID (host/DNS error recorded)
        │ reachable
        ▼
  ffmpeg: grab 1 frame ──────── fails/timeout ──▶ INVALID (ffmpeg error recorded)
        │ succeeds
        ▼
  VALID — screenshot saved
        │
        ▼
  results.csv / results.json / scan.log
```

1. Parse the URL and flag if it's HTTP(S) rather than true RTSP (a common
   client submission mistake — the two sample URLs in the brief are actually
   HTTP MJPEG streams, not RTSP).
2. Do a quick TCP reachability check on the host/port so a "server is down"
   failure is reported differently from a "server is up but stream is
   broken" failure.
3. If reachable, ask `ffmpeg` to grab a single frame. ffmpeg natively
   understands `rtsp://`, `http://` and `https://` MJPEG streams, so one
   code path covers both cases.
4. Record a VALID/INVALID result with the reason, and save the screenshot
   if there was one.

Full architecture notes are in `docs/internal_howto.md`.

## Testing error handling

`test_data/cameras_with_errors_demo.csv` contains three intentionally broken
URLs (unreachable host, 404 path, unresolvable hostname) to demonstrate each
failure mode. This is test data only, kept separate from the real
`cameras.csv` input:

```bash
python3 camera_checker.py --input test_data/cameras_with_errors_demo.csv --output-dir test_data/output_errors
```

A saved result from this exact command is in `test_data/results_error_demo.csv`.
