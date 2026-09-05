#!/usr/bin/env python3
"""
kino_core — engine shared by the CLI (kino_dl.py) and the macOS app (KDownloader.py).

kino.watch is a Kinopub-based install. Streams are plain HLS (no DRM / no
EXT-X-KEY). The item page embeds the master manifest URL in
`window.PLAYER_PLAYLIST`; the master lists video variants (1080/720/480),
several audio tracks, and many subtitle tracks. Manifest tokens are short-lived
(~24h), so fetch the manifest right before downloading.

This module handles: persistent session/cookies, the two-step login
(password + emailed code), Keychain-backed credential storage, master parsing,
and building the ffmpeg command that muxes a chosen resolution + all audio +
all subtitles into one .mkv.
"""
import http.cookiejar
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

# A frozen .app inherits the build machine's OpenSSL CA path (e.g. a Homebrew
# path) which does not exist on other Macs → every HTTPS call fails with
# CERTIFICATE_VERIFY_FAILED. Use certifi's bundled CA so TLS works everywhere.
try:
    import certifi
    _CA_FILE = certifi.where()
    if not os.path.exists(_CA_FILE):
        _CA_FILE = None
except Exception:
    _CA_FILE = None


def ssl_context():
    if _CA_FILE:
        return ssl.create_default_context(cafile=_CA_FILE)
    return ssl.create_default_context()


def subprocess_env():
    """Env for the bundled ffmpeg so its TLS also trusts the certifi bundle."""
    env = os.environ.copy()
    if _CA_FILE:
        env["SSL_CERT_FILE"] = _CA_FILE
        env["CA_BUNDLE"] = _CA_FILE
    return env

BASE = "https://kino.watch"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
KEYCHAIN_SERVICE = "kino.watch-downloader"


# ---------------------------------------------------------------- storage ----
def app_support_dir():
    base = os.path.expanduser("~/Library/Application Support")
    d = os.path.join(base, "KDownloader")
    old = os.path.join(base, "KinoWatchDL")   # migrate pre-rename state, once
    if os.path.isdir(old) and not os.path.exists(d):
        try:
            os.rename(old, d)
        except OSError:
            pass
    os.makedirs(d, exist_ok=True)
    return d


def cookies_path():
    return os.path.join(app_support_dir(), "cookies.txt")


def config_path():
    return os.path.join(app_support_dir(), "config.json")


def load_config():
    try:
        with open(config_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(config_path(), "w") as f:
        json.dump(cfg, f, indent=2)


# --- credentials in the macOS Keychain via the built-in `security` CLI -------
def keychain_set(account, password):
    subprocess.run(
        ["security", "add-generic-password", "-U", "-a", account,
         "-s", KEYCHAIN_SERVICE, "-w", password],
        check=True, capture_output=True)


def keychain_get(account):
    r = subprocess.run(
        ["security", "find-generic-password", "-a", account,
         "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def keychain_delete(account):
    subprocess.run(
        ["security", "delete-generic-password", "-a", account,
         "-s", KEYCHAIN_SERVICE],
        capture_output=True)


# ---------------------------------------------------------------- ffmpeg -----
def ffmpeg_path():
    """Bundled ffmpeg (inside the .app) takes priority, else system ffmpeg."""
    if getattr(sys, "frozen", False):
        cand = os.path.join(sys._MEIPASS, "ffmpeg")
        if os.path.exists(cand):
            return cand
    return shutil.which("ffmpeg") or "ffmpeg"


# --------------------------------------------------------------- session -----
class Session:
    def __init__(self):
        self.jar = http.cookiejar.MozillaCookieJar(cookies_path())
        if os.path.exists(cookies_path()):
            try:
                self.jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                pass
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=ssl_context()))

    def save(self):
        # ignore_discard keeps session cookies (PHPSESSID); ignore_expires keeps
        # them loadable. The rememberMe `_identity` cookie is what keeps you
        # logged in long-term (like the browser), so always persist the jar.
        try:
            self.jar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    def get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": BASE + "/"})
        with self.op.open(req, timeout=30) as r:
            data = r.read().decode("utf-8", "replace")
        self.save()  # persist any refreshed/rotated cookies
        return data, r.geturl()

    def post(self, url, data):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url})
        with self.op.open(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace"), r.geturl()

    # -- login ---------------------------------------------------------------
    @staticmethod
    def _csrf(html):
        m = re.search(r'name="_csrf"\s+value="([^"]*)"', html)
        if not m:
            raise RuntimeError("no CSRF token on login page")
        return m.group(1)

    def is_logged_in(self):
        try:
            html, final = self.get(BASE + "/")
        except Exception:
            return False
        return not final.endswith("/user/login")

    def logout(self):
        """Clear the local session so a fresh (or different-account) login can
        start clean. Removes the persisted cookie jar too."""
        self.jar.clear()
        try:
            if os.path.exists(cookies_path()):
                os.remove(cookies_path())
        except OSError:
            pass

    def login_password(self, user, password):
        """Step 1. Returns 'code' if an emailed code is required, 'ok' if fully
        logged in already, raises on bad credentials."""
        page, final = self.get(BASE + "/user/login")
        if not final.endswith("/user/login"):
            # Already authenticated — the login page redirected away. Don't try
            # to re-post (there is no login form / CSRF on the landing page).
            return "ok"
        html, final = self.post(BASE + "/user/login", {
            "_csrf": self._csrf(page),
            "login-form[login]": user,
            "login-form[password]": password,
            "login-form[rememberMe]": "1"})
        if "login-form[formcode]" in html:
            return "code"
        if not final.endswith("/user/login"):
            self.save()
            return "ok"
        raise RuntimeError("login failed: check email/password")

    def login_resend(self):
        """Ask the site to email a fresh code (the 'Выслать новый код' button:
        POST login-form[resend]=1). Needed because after the first attempt the
        site won't re-send on another login click — it says 'code already sent'.
        Must be called on a session that already did login_password."""
        page, _ = self.get(BASE + "/user/login")
        html, _ = self.post(BASE + "/user/login", {
            "_csrf": self._csrf(page),
            "login-form[resend]": "1"})
        if "login-form[formcode]" not in html:
            raise RuntimeError("resend failed — start login again")
        return "code"

    def login_code(self, code):
        """Step 2. Submits the emailed code on the same session."""
        page, _ = self.get(BASE + "/user/login")
        html, final = self.post(BASE + "/user/login", {
            "_csrf": self._csrf(page),
            "login-form[formcode]": code})
        if "login-form[formcode]" in html or final.endswith("/user/login"):
            raise RuntimeError("code rejected (wrong or expired)")
        self.save()
        return "ok"


# ------------------------------------------------------------ item / master --
def normalize_item(item):
    item = item.strip()
    if item.startswith("http"):
        return item
    return f"{BASE}/item/view/{item.lstrip('/')}"


def get_item(session, url):
    html, final = session.get(normalize_item(url))
    if final.endswith("/user/login"):
        raise RuntimeError("session expired — log in again")
    m = re.search(r"window\.PLAYER_PLAYLIST\s*=\s*(\[.*?\]);", html, re.S)
    if not m:
        raise RuntimeError("no playable stream found on that page")
    playlist = json.loads(m.group(1))
    return playlist


def parse_master(text, base_url):
    def absu(u):
        return urllib.parse.urljoin(base_url, u)

    videos, audios, subs = [], [], []
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-MEDIA:"):
            a = {k: (x or y) for k, x, y in
                 re.findall(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))', ln)}
            rec = {"name": a.get("NAME", ""), "lang": a.get("LANGUAGE", "und"),
                   "group": a.get("GROUP-ID", ""),
                   "default": a.get("DEFAULT") == "YES",
                   "forced": a.get("FORCED") == "YES",
                   "uri": absu(a.get("URI", ""))}
            if a.get("TYPE") == "AUDIO":
                audios.append(rec)
            elif a.get("TYPE") == "SUBTITLES":
                subs.append(rec)
        elif ln.startswith("#EXT-X-STREAM-INF:"):
            a = {k: (x or y) for k, x, y in
                 re.findall(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))', ln)}
            res = a.get("RESOLUTION", "0x0")
            h = int(res.split("x")[1]) if "x" in res else 0
            uri = ""
            for j in range(i + 1, len(lines)):
                if lines[j] and not lines[j].startswith("#"):
                    uri = lines[j].strip()
                    break
            videos.append({"height": h, "resolution": res,
                           "bandwidth": int(a.get("BANDWIDTH", 0)),
                           "audio_group": a.get("AUDIO", ""),
                           "uri": absu(uri)})
    videos.sort(key=lambda v: v["height"], reverse=True)
    return videos, audios, subs


def stream_info(session, item_url):
    """Fetch item + master, return a dict describing available tracks."""
    playlist = get_item(session, item_url)
    entry = playlist[0]
    master, mfinal = session.get(entry["manifest"])
    videos, audios, subs = parse_master(master, mfinal)
    return {"title": entry.get("title", "video"),
            "episode": entry.get("episode_title", ""),
            "manifest": entry["manifest"],
            "videos": videos, "audios": audios, "subs": subs}


def variant_duration(session, media_playlist_url):
    """Sum #EXTINF durations of a media playlist -> total seconds (for progress)."""
    try:
        text, _ = session.get(media_playlist_url)
    except Exception:
        return 0.0
    return sum(float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text))


# --------------------------------------------------------------- download ----
def safe_name(s):
    # Keep it human-readable: only replace characters illegal in a macOS
    # filename ('/' and ':') plus control chars; trim edge dots/spaces.
    s = re.sub(r"[/:\x00-\x1f]+", "_", s).strip(" .")
    return s[:150] or "video"


def audio_bitrate_guess(a):
    """Rough per-track bitrate (bps) for size estimation."""
    return 448_000 if "AC3" in a.get("name", "").upper() else 256_000


def estimate_size(video, audios, duration):
    """Estimate final byte size. STREAM-INF BANDWIDTH already covers video plus
    one audio rendition, so add a guess only for each *extra* selected audio."""
    if duration <= 0:
        return 0
    extra = max(0, len(audios) - 1)
    bits = video.get("bandwidth", 0) + extra * 256_000
    return int(duration * bits / 8)


def choose_tracks(info, height, audio_indices=None, include_subs=True):
    """Resolve the requested resolution + audio subset + subtitles from a
    stream_info() result. audio_indices = indices into the resolution's audio
    group (None → all). Returns (video, audios, subs)."""
    videos = info["videos"]
    if not videos:
        raise RuntimeError("no video variants in manifest")
    chosen = next((v for v in videos if v["height"] == height), None)
    if chosen is None:
        le = [v for v in videos if v["height"] <= height]
        chosen = (le or videos)[-1]
    group = [a for a in info["audios"] if a["group"] == chosen["audio_group"]]
    if audio_indices is None:
        auds = group
    else:
        auds = [group[i] for i in sorted(audio_indices) if 0 <= i < len(group)]
    subz = info["subs"] if include_subs else []
    return chosen, auds, subz


def build_ffmpeg_cmd(video, audios, subs, out_path, container="mp4"):
    """Explicit multi-input mux: chosen video + selected audio + subs into one
    file. container: 'mp4' (subs → mov_text) or 'mkv' (subs → srt).
    (Do not feed ffmpeg the master — its I-frame/data streams break muxing.)"""
    sub_codec = "mov_text" if container == "mp4" else "srt"
    ff = ffmpeg_path()
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-user_agent", UA,
           "-i", video["uri"]]
    for a in audios:
        cmd += ["-i", a["uri"]]
    for s in subs:
        cmd += ["-i", s["uri"]]

    cmd += ["-map", "0:v:0"]
    for k in range(len(audios)):
        cmd += ["-map", f"{1 + k}:a:0"]
    for k in range(len(subs)):
        cmd += ["-map", f"{1 + len(audios) + k}:s:0"]

    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec]
    for k, a in enumerate(audios):
        cmd += [f"-metadata:s:a:{k}", f"language={a['lang']}",
                f"-metadata:s:a:{k}", f"title={a['name']}"]
        if a.get("default"):
            cmd += [f"-disposition:a:{k}", "default"]
    for k, s in enumerate(subs):
        title = s["name"] + (" (forced)" if s.get("forced") else "")
        cmd += [f"-metadata:s:s:{k}", f"language={s['lang']}",
                f"-metadata:s:s:{k}", f"title={title}"]
        if s.get("forced"):
            cmd += [f"-disposition:s:{k}", "forced"]
    cmd += ["-movflags", "+faststart", "-y", out_path]
    return cmd


def download(session, item_url, height, out_dir, progress_cb=None,
             audio_indices=None, include_subs=True, container="mp4",
             out_name=None, info=None):
    """Download one item. audio_indices selects which audio tracks (None=all),
    container is 'mp4' or 'mkv'. progress_cb(fraction, message) gets 0..1 during
    the download (or -1 while starting). Returns the output file path.
    `info` may be a pre-fetched stream_info() to skip re-fetching."""
    if info is None:
        info = stream_info(session, item_url)
    chosen, auds, subz = choose_tracks(info, height, audio_indices, include_subs)

    if out_name:
        base = safe_name(out_name)            # user-edited title = full filename base
    else:
        base = safe_name(info["title"].split("/")[0])
        if info.get("episode"):
            base += " - " + safe_name(info["episode"])
    ext = "mp4" if container == "mp4" else "mkv"
    out_path = os.path.join(out_dir, base + f" [{chosen['height']}p].{ext}")

    dur = variant_duration(session, chosen["uri"])
    est = estimate_size(chosen, auds, dur)
    if progress_cb:
        progress_cb(-1, f"Starting {chosen['height']}p · {len(auds)} audio · "
                        f"{len(subz)} subs …")

    cmd = build_ffmpeg_cmd(chosen, auds, subz, out_path, container)
    errf = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf,
                            text=True, bufsize=1, env=subprocess_env())

    # Progress from ffmpeg's own -progress stream. With many inputs ffmpeg is
    # silent for the first ~20-30s while it opens every track, so show a
    # "preparing" state until the first update, then a real % from out_time and
    # actual bytes muxed from total_size (works for MP4 faststart too, unlike
    # polling the on-disk file).
    if progress_cb:
        progress_cb(-1, "Preparing (opening tracks)…")
    out_time = None
    total_size = 0
    for line in proc.stdout:
        line = line.strip()
        if line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                out_time = int(line.split("=")[1]) / 1_000_000
            except ValueError:
                pass
        elif line.startswith("total_size="):
            try:
                total_size = int(line.split("=")[1])
            except ValueError:
                pass
        elif line.startswith("progress=") and progress_cb:
            mb = total_size / 1e6
            if dur > 0 and out_time is not None:
                frac = min(out_time / dur, 0.999)
                approx = f" / ~{est/1e6:.0f}" if est else ""
                progress_cb(frac, f"{mb:.0f}{approx} MB · {frac*100:.0f}%")
            else:
                progress_cb(-1, f"{mb:.0f} MB")

    ret = proc.wait()
    errf.flush(); errf.seek(0)
    err_tail = errf.read().splitlines()[-8:]
    errf.close()
    try:
        os.unlink(errf.name)
    except OSError:
        pass
    if ret != 0:
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(err_tail))
    if progress_cb:
        final = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        progress_cb(1.0, f"Done · {final/1e6:.0f} MB")
    return out_path
