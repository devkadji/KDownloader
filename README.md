# KDownloader

Standalone macOS app (+ CLI) that logs into kino.watch and downloads videos.
Paste one or many URLs; for each you choose resolution, which audio tracks, and
whether to include all subtitles, then it muxes them into one file (**MP4** by
default, **MKV** optional). Streams are copied without re-encoding
(`ffmpeg -c copy`): the original H.264 / AAC / AC3 bitstreams; subtitles become
mov_text (MP4) or SRT (MKV). Only the container is remuxed.

> For personal use with your own kino.watch account. The site serves plain HLS
> (no DRM); this tool just remuxes streams your account can already play.

Features:
- **Batch queue** — paste multiple URLs; each becomes a row with its own
  resolution / audio / subtitle / container settings ("Apply to all" copies one
  row's settings to every row). Right-click a row to remove it.
- **Per-item audio selection** — checkboxes pick which audio tracks; subtitles
  are an all-or-nothing toggle.
- **Real progress** — a size-and-time progress bar from ffmpeg's own `-progress`
  stream ("311 / ~517 MB · 44%"), plus per-row status.
- **Persistent login** — the site's 90-day `_identity`/`token` cookies are saved,
  so after the one-time email token you stay logged in for ~90 days (like the
  browser). Password in Keychain, session cookies on disk.

## The app

- **`dist/KDownloader.app`** — double-clickable. ffmpeg is bundled inside, so
  nothing else is needed. (Build it with `./build.sh`; see Rebuild below.)
- First launch is unsigned → **right-click the app → Open**, then confirm the
  Gatekeeper prompt (only needed once). Built for Apple Silicon (arm64).

### Using it
1. Enter your email + password → **Log in**. The site emails a token → paste it
   in the field that appears → **Submit token** (use **Resend email** if it
   doesn't arrive; check Spam). Credentials go to the macOS **Keychain** and the
   session persists ~90 days, so you normally do this once. The button then reads
   **Log out** (for switching accounts).
2. Paste one or more video URLs (one per line) → **Add to queue**. Each becomes a
   row once its tracks are fetched.
3. Select a row to set its **resolution**, **audio tracks**, **subtitles**, and
   **container** (mp4/mkv). **Apply these to all items** copies the settings
   across the batch.
4. Choose a destination folder → **Download all**. Progress shows in the bar and
   per row; files land as `Title [1080p].mp4`.

## The CLI (same engine)

```bash
python3 kino_dl.py --login                              # once
python3 kino_dl.py https://kino.watch/item/view/125335/s0e1 --list   # shows audio indices
python3 kino_dl.py https://kino.watch/item/view/125335/s0e1 -q 1080 -o ~/Movies
python3 kino_dl.py 125335/s0e1 -q 720 -f mkv --audio 0,2 --no-subs   # keep audio tracks 0 & 2
```

## Files
- `KDownloader.py` — Tkinter queue GUI
- `kino_core.py`   — engine: login/2FA + resend, session, master parsing, ffmpeg mux
- `kino_dl.py`     — CLI front-end
- `KDownloader.spec` / `build.sh` — packaging (PyInstaller + bundled ffmpeg)
- `FINDINGS.md`    — how the site/stream works (the investigation notes)

`vendor/ffmpeg` (a static arm64 ffmpeg) and `dist/` are not committed — `build.sh`
downloads ffmpeg and produces the app.

## Rebuild
```bash
pip install pyinstaller
./build.sh          # downloads ffmpeg if missing, produces dist/KDownloader.app
```

## Notes / limits
- Streams are **plain HLS, no DRM** — a straightforward remux, not a bypass.
- Manifest tokens expire ~24h after the page is opened; the app always fetches a
  fresh manifest at download time, so this is a non-issue.
- ffmpeg fetches segments sequentially, and is silent for ~20–30 s at the start
  of a many-track download while it opens every track (the bar shows
  "Preparing…"). A parallel HLS grabber (`N_m3u8DL-RE`, yt-dlp) could speed the
  fetch up later without changing the mux step.
- **Persistent state** lives in `~/Library/Application Support/KDownloader/`
  (migrated automatically from a prior `KinoWatchDL/` folder): `cookies.txt`
  (session, ~90 days) and `config.json` (email + output folder). The **password
  is in the macOS Keychain** under service `kino.watch-downloader`.
- **CA certificates:** the app bundles certifi's `cacert.pem` and points both
  Python and the bundled ffmpeg at it. Without this a frozen app inherits the
  build machine's Homebrew OpenSSL cert path (absent on other Macs) and every
  HTTPS call fails with `CERTIFICATE_VERIFY_FAILED` — which surfaces as
  "Login failed". Run a headless check any time with:
  `KDownloader.app/Contents/MacOS/KDownloader --selftest`
