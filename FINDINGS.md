# kino.watch — how the streaming works (investigation notes)

Target example: https://kino.watch/item/view/125335/s0e1
Date: 2026-09-04

## Platform

- nginx / HTTP2. Russian UI. The whole site is login-walled (`/` and
  `/item/view/...` 302 → `/user/login` when unauthenticated).
- **Kinopub-based** install (homepage title "Кинопаб"), Yii2 framework
  (`_csrf` field, `login-form[...]` naming, csrf meta tags).

## Auth flow (two-step, no captcha)

1. `POST /user/login` with `_csrf`, `login-form[login]`, `login-form[password]`,
   `login-form[rememberMe]=1` → validates creds and **emails an 8-char code**,
   re-renders the page now showing `login-form[formcode]` ("Код из письма").
   State is held in the **session cookie**.
2. `POST /user/login` with a fresh `_csrf` + `login-form[formcode]` on the SAME
   session → logged in, redirects to `/`.

`rememberMe=1` + persisted cookies means you 2FA only occasionally.

**Resend:** while a code is pending, re-POSTing credentials does NOT send a new
email — the site replies "код уже отправлен" (already sent). To get a fresh
email, POST `_csrf` + `login-form[resend]=1` on the same session (the
"Выслать новый код" button). The app exposes this as a "Resend email" button.

## Where the stream lives

- The item page embeds, in `window.PLAYER_PLAYLIST`, the master manifest:
  `https://kino.watch/manifest/hls4/<token>/<media_id>.m3u8?loc=..`
  (player is video.js + hls.js).
- The master is **plain HLS, no DRM** (no `#EXT-X-KEY`). For item 125335/s0e1:
  - **Video variants:** 1920x1080, 1280x720, 720x406 (H.264).
  - **Audio:** 4 tracks per variant group (RUS dubs ×3 incl. an AC3, ENG
    original), AAC/AC3.
  - **Subtitles:** 36 tracks (SRT delivered as HLS), many languages.
- Video/audio/subtitle segments are served from `*.cdntogo.net`. Their auth is
  the **token baked into the URL path** (not the session cookie) — so segment
  fetches don't need cookies, only the manifest fetch (via the page) does.
- The manifest token carries an expiry (`e=<unixtime>`), ~**24h** after issue.
  Fetch the manifest right before downloading.

## Downloading

Feed ffmpeg the chosen **video variant playlist** + each **audio** + each
**subtitle** as explicit inputs, then `-map` them and `-c copy` into an **.mkv**:

- MKV is the only common container that cleanly holds all audio + all SRT subs
  in one file (MP4 can't).
- `-c copy` = no re-encode; original bitstreams preserved (lossless).
- Do **not** feed ffmpeg the master with `-map 0` → the master also advertises
  I-frame/data streams that Matroska rejects (`Only audio, video, and subtitles
  are supported for Matroska`). Explicit per-track inputs avoid that.
- ffmpeg itself does all the chunk fetching + remux (HLS demuxer downloads each
  segment, stitches across boundaries, fixes timestamps). No custom chunk code
  needed.

Verified: 1 video + 4 audio + 36 subtitles muxed to MKV, correct per-track
`language`/`title` tags, exit 0.

## Deliverable

A standalone macOS app (`dist/KDownloader.app`) + matching CLI — see `README.md`.
Bundles a static arm64 ffmpeg; credentials in Keychain; resolution picker; all
audio + all subs by default.
