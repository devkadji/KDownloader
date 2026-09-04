#!/usr/bin/env python3
"""
kino.watch CLI downloader (same engine as the macOS app, kino_core).

Downloads a chosen resolution + all audio + all subtitles into one .mkv,
copying streams without re-encoding.

Login once (stores cookies in ~/Library/Application Support/KDownloader):
    python3 kino_dl.py --login

Then:
    python3 kino_dl.py https://kino.watch/item/view/125335/s0e1
    python3 kino_dl.py 125335/s0e1 -q 720 -o ~/Movies
    python3 kino_dl.py https://kino.watch/item/view/125335/s0e1 --list
"""
import argparse
import getpass
import os
import sys

import kino_core as core


def do_login():
    s = core.Session()
    user = input("login/email: ").strip()
    pw = getpass.getpass("password: ")
    res = s.login_password(user, pw)
    if res == "code":
        code = input("emailed code: ").strip()
        s.login_code(code)
    cfg = core.load_config(); cfg["email"] = user; core.save_config(cfg)
    core.keychain_set(user, pw)
    print("logged in ✓")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("item", nargs="?", help="item URL or e.g. 125335/s0e1")
    ap.add_argument("-q", "--quality", default="max", help="max|1080|720|406")
    ap.add_argument("-o", "--out", default=os.path.expanduser("~/Downloads"),
                    help="output directory")
    ap.add_argument("-f", "--format", default="mp4", choices=["mp4", "mkv"],
                    help="container (default mp4)")
    ap.add_argument("--audio", default="", help="comma indices of audio tracks to keep "
                    "(0-based, see --list); empty = all")
    ap.add_argument("--no-subs", action="store_true", help="skip subtitles")
    ap.add_argument("--list", action="store_true", help="list tracks and exit")
    ap.add_argument("--login", action="store_true", help="log in and store session")
    args = ap.parse_args()

    if args.login:
        do_login()
        return
    if not args.item:
        ap.error("provide an item URL (or --login first)")

    s = core.Session()
    if not s.is_logged_in():
        sys.exit("not logged in — run:  python3 kino_dl.py --login")

    info = core.stream_info(s, args.item)
    if args.list:
        print(f"title: {info['title']}")
        print("VIDEO:", ", ".join(f"{v['height']}p" for v in info["videos"]))
        print("AUDIO (index: lang — name):")
        group = [a for a in info["audios"]
                 if a["group"] == info["videos"][0]["audio_group"]]
        for i, a in enumerate(group):
            print(f"  {i}: {a['lang']:>3}  {a['name']}")
        print(f"SUBTITLES ({len(info['subs'])}):",
              ", ".join(sorted({x['lang'] for x in info['subs']})))
        return

    height = (max(v["height"] for v in info["videos"])
              if args.quality in ("max", "best") else int(args.quality))
    audio_indices = ({int(x) for x in args.audio.split(",") if x.strip() != ""}
                     if args.audio.strip() else None)

    def prog(frac, msg):
        if frac < 0:
            sys.stdout.write(f"\r  {msg}          ")
        else:
            sys.stdout.write(f"\r  {msg}   ")
        sys.stdout.flush()

    out = core.download(s, args.item, height, args.out, progress_cb=prog,
                        audio_indices=audio_indices, include_subs=not args.no_subs,
                        container=args.format, info=info)
    print(f"\nsaved → {out}")


if __name__ == "__main__":
    main()
