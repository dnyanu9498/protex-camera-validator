# Internal How-To: Camera Stream Validator

**Audience:** Protex engineers/support staff validating a client's camera
list during onboarding.

**Purpose:** When a client sends over a list of camera URLs, we need to
quickly confirm which ones actually work before an engineer spends time on
a manual install. This tool automates that first pass.

## 1. Prerequisites

- Python 3.9 or later.
- `ffmpeg` installed and on your `PATH`.
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install -y ffmpeg`
  - Windows: `choco install ffmpeg`, or download from ffmpeg.org.
  - Verify with: `ffmpeg -version`
- Network access (VPN/on-site network access, or Protex Edge device access)
  to the client's camera network. The tool needs to be run somewhere that
  can actually reach the camera IPs — usually the Protex Edge device itself,
  or a jump host on the client's LAN.

No Python packages need to be installed (standard library only).

## 2. Preparing the input file

Ask the client (via Client Success) for their camera list in the standard
installation doc format — camera name + URL. Copy that into a CSV with two
columns:

```csv
camera_name,url
Loading Dock Camera 1,rtsp://192.168.1.50:554/stream1
Warehouse Entrance,http://192.168.1.60/mjpg/video.mjpg
```

Save it as e.g. `client_acme_cameras.csv` in the project folder (or anywhere,
and pass the path with `--input`).

## 3. Running the tool

From the project directory:

```bash
python3 camera_checker.py --input client_acme_cameras.csv --output-dir output_acme
```

Useful flags:

| Flag | Default | Purpose |
|---|---|---|
| `--input` | `cameras.csv` | Path to the camera CSV |
| `--output-dir` | `output` | Where screenshots/reports/log get written |
| `--connect-timeout` | `5` | Seconds to wait for the initial TCP reachability check |
| `--capture-timeout` | `15` | Seconds to wait for ffmpeg to grab a frame before giving up |

If you're on a slow or high-latency client network (e.g. cameras behind a
VPN tunnel), increase `--connect-timeout` and `--capture-timeout` — a low
timeout on a genuinely slow link will produce false "unreachable"/"timed
out" results.

## 4. Reading the output

In `<output-dir>/`:

- **`screenshots/<camera_name>.jpg`** — proof the stream works. Open a few
  to sanity-check exposure/focus/orientation, not just that a file exists.
- **`results.csv`** / **`results.json`** — machine-readable summary, one row
  per camera. Columns: `camera_name, url, scheme, protocol_note, reachable,
  status, error, screenshot_path, checked_at`.
- **`scan.log`** — full debug log if you need to dig into exactly what
  ffmpeg said.

### Interpreting `status` and `error`

| Situation | `reachable` | `status` | Typical `error` text |
|---|---|---|---|
| Everything works | `True` | `VALID` | (empty) |
| Host/IP not on the network, wrong IP, firewall blocking the port | `False` | `INVALID` | `Could not open TCP connection to ...` |
| DNS name doesn't resolve (rare for camera IPs, common for hostnames) | `False` | `INVALID` | `DNS/hostname resolution failed ...` |
| Host is up, but the path/credentials/stream is wrong | `True` | `INVALID` | ffmpeg's own error, e.g. `Server returned 404 Not Found`, `401 Unauthorized`, `Invalid data found when processing input` |
| Host is up, connection opens, but never returns usable video | `True` | `INVALID` | `ffmpeg timed out after Ns ...` — worth re-running with a higher `--capture-timeout` before concluding it's broken |

### `protocol_note`

If a client sends an `http://` or `https://` URL labeled as "RTSP," the
tool still tests it (many older/legacy cameras only expose MJPEG-over-HTTP),
but flags it in `protocol_note`. Mention this to the client — it's worth
confirming whether they meant to give us a genuine `rtsp://` URL, since some
NVR/camera firmware exposes both, and RTSP is generally the better/more
efficient path for us.

## 5. What to do with the results

- All `VALID` → attach `results.csv` and a couple of representative
  screenshots to the client-facing report (copy
  `docs/client_report_template.md` and fill it in with that client's real
  results) and proceed with the install.
- Some `INVALID` → don't just forward the raw error to the client. Translate
  it:
  - Unreachable/DNS failure → ask the client to confirm the IP/hostname and
    that the camera and Edge device are on the same network/VLAN, and that
    no firewall rule blocks the port.
  - 404 / stream path error → ask the client (or their camera vendor docs)
    to confirm the correct stream path — this varies a lot per manufacturer.
  - 401 Unauthorized → ask for credentials, or confirm the URL should
    include them (e.g. `rtsp://user:pass@host/stream1`).
  - Timeout → could be a genuinely slow/overloaded camera or network link;
    re-run with a longer `--capture-timeout` before flagging to the client.

### Example: how an INVALID result looks in the client report

Using the tool's own error-demo data (`demo/cameras_with_errors_demo.csv`)
as a stand-in, a "Cameras needing attention" section in the client report
would look like this:

| Camera Name | Issue | What we need from you |
|---|---|---|
| Unreachable Host | We couldn't reach this camera's IP address on your network. | Please confirm the IP address is correct and that no firewall rule is blocking access from our device. |
| Wrong Path | The camera responded, but the video path in the URL doesn't exist on the camera. | Could you confirm the correct streaming URL/path, or share the camera's make/model so we can look it up? |
| Bad Hostname | The hostname provided doesn't resolve to any address. | Please confirm the hostname or IP address is correct. |

This is only a reference for translating raw errors into client-friendly
language — never send a client a report containing example/demo data.
Always fill `docs/client_report_template.md` with that specific client's
real results.

## 6. Extending the tool

Kept intentionally simple. If a real need comes up:

- **Bulk re-runs / scheduling**: wrap the script in a cron job or Pipelines
  step; it already returns a clean exit code and writes machine-readable
  JSON.
- **Credentials in URLs**: already supported since ffmpeg accepts
  `user:pass@host` in the URL itself — no code change needed.
- **Parallelism**: for very large camera lists, the per-camera checks could
  be run concurrently (e.g. `concurrent.futures.ThreadPoolExecutor`). Not
  implemented here since typical client lists are small and sequential runs
  are easier to read logs for.
