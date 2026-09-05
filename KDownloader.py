#!/usr/bin/env python3
"""
KDownloader — standalone macOS app to download kino.watch videos.

Stores your login (macOS Keychain + persistent session cookies), handles the
emailed token, and downloads videos into a single file. Supports a batch queue:
paste one or many URLs, and set per-row resolution / audio tracks / subtitles /
container (MP4 default, MKV optional). Streams are copied without re-encoding
(-c copy); only the container is remuxed.
"""
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox

import kino_core as core


class App(ttk.Frame):
    def __init__(self, root):
        super().__init__(root, padding=12)
        self.root = root
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.session = core.Session()
        self.q = queue.Queue()
        self.items = []          # queue of dicts (one per URL)
        self.logged_in = False
        cfg = core.load_config()
        self.outdir = cfg.get("outdir", os.path.expanduser("~/Downloads"))

        self._build_account(cfg)
        self._build_queue()
        self._build_footer()

        self.after(100, self._pump)
        self._set_status("Checking session…", "gray")
        threading.Thread(target=self._check_session, daemon=True).start()

    # ============================================================== ACCOUNT ==
    def _build_account(self, cfg):
        f = ttk.LabelFrame(self, text="Account", padding=10)
        f.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Email / login").grid(row=0, column=0, sticky="w")
        self.email = ttk.Entry(f)
        self.email.grid(row=0, column=1, columnspan=2, sticky="ew", padx=6, pady=2)
        self.email.insert(0, cfg.get("email", ""))

        ttk.Label(f, text="Password").grid(row=1, column=0, sticky="w")
        self.pw = ttk.Entry(f, show="•")
        self.pw.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=2)
        pw = core.keychain_get(cfg.get("email", "")) if cfg.get("email") else None
        if pw:
            self.pw.insert(0, pw)

        self.login_btn = ttk.Button(f, text="Log in", command=self._do_login)
        self.login_btn.grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))
        self.acct_status = ttk.Label(f, text="", foreground="gray")
        self.acct_status.grid(row=2, column=2, sticky="e")

        self.code_frame = ttk.Frame(f)
        self.code_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(self.code_frame, text="Token from email").pack(side="left")
        self.code = ttk.Entry(self.code_frame, width=14)
        self.code.pack(side="left", padx=6)
        ttk.Button(self.code_frame, text="Submit token",
                   command=self._do_code).pack(side="left")
        self.resend_btn = ttk.Button(self.code_frame, text="Resend email",
                                     command=self._do_resend)
        self.resend_btn.pack(side="left", padx=(6, 0))
        self.code_frame.grid_remove()

    # ================================================================ QUEUE ==
    def _build_queue(self):
        f = ttk.LabelFrame(self, text="Downloads", padding=10)
        f.grid(row=1, column=0, sticky="ew")
        f.columnconfigure(0, weight=1)

        ttk.Label(f, text="Paste one or more video URLs (one per line):").grid(
            row=0, column=0, sticky="w")
        self.urls = tk.Text(f, height=3, wrap="none", font=("Menlo", 10))
        self.urls.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        ttk.Button(f, text="Add to queue", command=self._add_urls).grid(
            row=1, column=1, sticky="n", padx=(6, 0))

        cols = ("res", "audio", "subs", "fmt", "status")
        self.tree = ttk.Treeview(f, columns=cols, show="tree headings", height=6)
        self.tree.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self.tree.heading("#0", text="Title")
        self.tree.column("#0", width=280, anchor="w")
        for c, t, w in (("res", "Res", 60), ("audio", "Audio", 60),
                        ("subs", "Subs", 60), ("fmt", "Fmt", 55),
                        ("status", "Status", 130)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        # double-click: on a heading separator -> auto-fit that column;
        #               on a Title cell -> edit the title (output filename)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self._tree_font = tkfont.nametofont("TkDefaultFont")

        # right-click / control-click context menu on the queue
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Rename (edit filename)…",
                                   command=self._rename_sel)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Remove", command=self._remove_sel)
        self.tree_menu.add_command(label="Clear finished", command=self._clear_done)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Fit columns to content",
                                   command=self._autofit_all)
        for seq in ("<Button-2>", "<Button-3>", "<Control-Button-1>"):
            self.tree.bind(seq, self._popup_menu)

        btns = ttk.Frame(f)
        btns.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Button(btns, text="Remove", command=self._remove_sel).pack(side="left")
        ttk.Button(btns, text="Clear finished", command=self._clear_done).pack(side="left", padx=6)

        # per-row options panel
        self.opts = ttk.LabelFrame(f, text="Options for selected item", padding=8)
        self.opts.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.opts.columnconfigure(1, weight=1)

        ttk.Label(self.opts, text="Resolution").grid(row=0, column=0, sticky="w")
        self.res_var = tk.StringVar()
        self.res_menu = ttk.OptionMenu(self.opts, self.res_var, "")
        self.res_menu.grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(self.opts, text="Container").grid(row=0, column=2, sticky="e")
        self.fmt_var = tk.StringVar(value="mp4")
        ttk.OptionMenu(self.opts, self.fmt_var, "mp4", "mp4", "mkv",
                       command=lambda *_: self._write_back()).grid(
            row=0, column=3, sticky="w", padx=6)

        ttk.Label(self.opts, text="Audio tracks").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.audio_box = ttk.Frame(self.opts)
        self.audio_box.grid(row=1, column=1, columnspan=3, sticky="w", pady=(6, 0))

        self.subs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.opts, text="Include all subtitles",
                        variable=self.subs_var,
                        command=self._write_back).grid(row=2, column=1,
                                                       columnspan=3, sticky="w", pady=(6, 0))
        self.res_var.trace_add("write", lambda *_: self._write_back())
        ttk.Button(self.opts, text="Apply these to all items",
                   command=self._apply_to_all).grid(row=3, column=1,
                                                     columnspan=3, sticky="w", pady=(8, 0))
        self._set_opts_enabled(False)

    def _build_footer(self):
        f = ttk.Frame(self)
        f.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        f.columnconfigure(0, weight=1)

        out = ttk.Frame(f)
        out.grid(row=0, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Save to").grid(row=0, column=0, sticky="w")
        self.outdir_lbl = ttk.Label(out, text=self.outdir, foreground="gray")
        self.outdir_lbl.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(out, text="Choose…", command=self._choose_dir).grid(row=0, column=2)

        self.dl_btn = ttk.Button(f, text="Download all", command=self._download_all,
                                 state="disabled")
        self.dl_btn.grid(row=1, column=0, sticky="ew", pady=(8, 2))
        self.progress = ttk.Progressbar(f, mode="determinate", maximum=1.0)
        self.progress.grid(row=2, column=0, sticky="ew", pady=2)
        self.dl_status = ttk.Label(f, text="", foreground="gray")
        self.dl_status.grid(row=3, column=0, sticky="w")

        self.log = tk.Text(f, height=5, wrap="word", state="disabled",
                           font=("Menlo", 10))
        self.log.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        f.rowconfigure(4, weight=1)

    # ============================================================== helpers ==
    def _set_status(self, text, color="black"):
        self.acct_status.configure(text=text, foreground=color)

    def _set_dl(self, text, color="gray"):
        self.dl_status.configure(text=text, foreground=color)

    def _logmsg(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _post(self, fn):
        self.q.put(fn)

    def _title_of(self, it):
        # default = original (English) title + (year); the shown title is also
        # the output filename base, and the user can edit it.
        return core.default_basename(it["info"])

    def _refresh_row(self, it):
        vals = (f"{it['height']}p",
                f"{len(it['audio_idx'])}/{len(it['ref_audios'])}",
                "all" if it["subs"] else "none",
                it["container"], it["status"])
        self.tree.item(it["iid"], text=it.get("title", ""), values=vals)

    def _sel_item(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((x for x in self.items if x["iid"] == sel[0]), None)

    # ---- column auto-fit (pure tkinter; no deps) ---------------------------
    def _autofit_column(self, col):
        f = self._tree_font
        rows = self.tree.get_children()
        if col == "#0":
            texts = [self.tree.item(i, "text") for i in rows]
            texts.append(self.tree.heading("#0", "text"))
            pad = 28  # room for the disclosure indent
        else:
            texts = [self.tree.set(i, col) for i in rows]
            texts.append(self.tree.heading(col, "text"))
            pad = 20
        width = max((f.measure(t) for t in texts if t), default=40) + pad
        self.tree.column(col, width=max(width, 40))

    def _autofit_all(self):
        for col in ("#0",) + self.tree["columns"]:
            self._autofit_column(col)

    # ---- inline title editing (= output filename) -------------------------
    def _on_tree_double(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "separator":
            col = self.tree.identify_column(event.x)
            if col:
                self._autofit_column(col)
            return "break"
        if region in ("tree", "cell"):
            col = self.tree.identify_column(event.x)
            row = self.tree.identify_row(event.y)
            if row and col == "#0":
                self._begin_edit_title(row)
                return "break"

    def _rename_sel(self):
        it = self._sel_item()
        if it:
            self._begin_edit_title(it["iid"])

    def _begin_edit_title(self, iid):
        it = next((x for x in self.items if x["iid"] == iid), None)
        if not it:
            return
        bbox = self.tree.bbox(iid, "#0")
        if not bbox:
            return
        x, y, w, h = bbox
        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=max(w, 200), height=h)
        entry.insert(0, it.get("title", ""))
        entry.select_range(0, "end")
        entry.focus_set()

        def commit(_evt=None):
            new = entry.get().strip()
            entry.destroy()
            if new:
                it["title"] = new
                self._refresh_row(it)
                self._autofit_column("#0")

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    # ============================================================== ACCOUNT ==
    def _set_logged_in(self, ok):
        self.logged_in = ok
        if ok:
            self.login_btn.configure(text="Log out", command=self._do_logout,
                                     state="normal")
            self.code_frame.grid_remove()
        else:
            self.login_btn.configure(text="Log in", command=self._do_login,
                                     state="normal")

    def _check_session(self):
        ok = self.session.is_logged_in()
        self._post(lambda: (self._set_logged_in(ok),
                            self._set_status("Logged in ✓" if ok else "Not logged in",
                                             "green" if ok else "gray")))

    def _do_logout(self):
        self.session.logout()
        self._set_logged_in(False)
        self._set_status("Logged out — enter credentials to sign in", "gray")

    def _do_login(self):
        user, password = self.email.get().strip(), self.pw.get()
        if not user or not password:
            messagebox.showwarning("Missing", "Enter email and password.")
            return
        self.login_btn.configure(state="disabled")
        self._set_status("Logging in…", "gray")

        def work():
            try:
                res = self.session.login_password(user, password)
                cfg = core.load_config(); cfg["email"] = user; core.save_config(cfg)
                core.keychain_set(user, password)
                if res == "code":
                    self._post(lambda: (self.code_frame.grid(),
                                        self._set_status("Paste token from email → (or Resend)", "orange"),
                                        self.login_btn.configure(state="normal")))
                else:
                    self._post(lambda: (self._set_logged_in(True),
                                        self._set_status("Logged in ✓", "green")))
            except Exception as e:
                self._post(lambda e=e: (self._set_status("Login failed", "red"),
                                        self._logmsg(f"Login error: {e}"),
                                        self.login_btn.configure(state="normal")))
        threading.Thread(target=work, daemon=True).start()

    def _do_resend(self):
        self.resend_btn.configure(state="disabled")
        self._set_status("Requesting new code…", "gray")

        def work():
            try:
                self.session.login_resend()
                self._post(lambda: (self._set_status("New code emailed — check inbox/Spam", "orange"),
                                    self.resend_btn.configure(state="normal")))
            except Exception as e:
                self._post(lambda e=e: (self._set_status("Resend failed", "red"),
                                        self._logmsg(f"Resend error: {e}"),
                                        self.resend_btn.configure(state="normal")))
        threading.Thread(target=work, daemon=True).start()

    def _do_code(self):
        code = self.code.get().strip()
        if not code:
            return
        self._set_status("Verifying token…", "gray")

        def work():
            try:
                self.session.login_code(code)
                self._post(lambda: (self._set_logged_in(True),
                                    self._set_status("Logged in ✓", "green")))
            except Exception as e:
                self._post(lambda e=e: (self._set_status("Token rejected", "red"),
                                        self._logmsg(f"Token error: {e}")))
        threading.Thread(target=work, daemon=True).start()

    # ================================================================ QUEUE ==
    def _add_urls(self):
        raw = self.urls.get("1.0", "end").strip()
        self.urls.delete("1.0", "end")
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        existing = {x["url"] for x in self.items}
        for u in urls:
            if u in existing:
                continue
            iid = self.tree.insert("", "end", text=u[:60], values=("", "", "", "", "fetching…"))
            threading.Thread(target=self._fetch_item, args=(u, iid), daemon=True).start()

    def _fetch_item(self, url, iid):
        try:
            info = core.stream_info(self.session, url)
            ref = [a for a in info["audios"]
                   if a["group"] == info["videos"][0]["audio_group"]]
            it = {"url": url, "info": info, "iid": iid,
                  "height": info["videos"][0]["height"],
                  "ref_audios": ref, "audio_idx": set(range(len(ref))),
                  "subs": True, "container": "mp4", "status": "ready"}
            it["title"] = self._title_of(it)
            def add():
                self.items.append(it)
                self._refresh_row(it)
                self._autofit_all()
                self.dl_btn.configure(state="normal")
            self._post(add)
        except Exception as e:
            self._post(lambda e=e: (self.tree.item(iid, values=("", "", "", "", "error")),
                                    self._logmsg(f"{url}: {e}")))

    def _on_select(self, _evt=None):
        it = self._sel_item()
        if not it:
            self._set_opts_enabled(False)
            return
        self._loading = True
        # resolution menu
        menu = self.res_menu["menu"]; menu.delete(0, "end")
        for v in it["info"]["videos"]:
            h = v["height"]
            menu.add_command(label=f"{h}p", command=lambda s=f"{h}p": self.res_var.set(s))
        self.res_var.set(f"{it['height']}p")
        self.fmt_var.set(it["container"])
        self.subs_var.set(it["subs"])
        # audio checkboxes
        for w in self.audio_box.winfo_children():
            w.destroy()
        it["_audio_vars"] = []
        for i, a in enumerate(it["ref_audios"]):
            var = tk.BooleanVar(value=(i in it["audio_idx"]))
            it["_audio_vars"].append(var)
            ttk.Checkbutton(self.audio_box,
                            text=f"{a['lang']} — {a['name']}",
                            variable=var, command=self._write_back).pack(anchor="w")
        self._set_opts_enabled(True)
        self._loading = False

    def _set_opts_enabled(self, on):
        state = "normal" if on else "disabled"
        for w in self.opts.winfo_children():
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _write_back(self):
        if getattr(self, "_loading", False):
            return
        it = self._sel_item()
        if not it:
            return
        try:
            it["height"] = int(self.res_var.get().rstrip("p"))
        except ValueError:
            pass
        it["container"] = self.fmt_var.get()
        it["subs"] = self.subs_var.get()
        it["audio_idx"] = {i for i, v in enumerate(it.get("_audio_vars", []))
                           if v.get()}
        self._refresh_row(it)

    def _apply_to_all(self):
        src = self._sel_item()
        if not src:
            return
        for it in self.items:
            heights = [v["height"] for v in it["info"]["videos"]]
            it["height"] = src["height"] if src["height"] in heights else max(heights)
            it["container"] = src["container"]
            it["subs"] = src["subs"]
            n = len(it["ref_audios"])
            it["audio_idx"] = {i for i in src["audio_idx"] if i < n} or set(range(n))
            self._refresh_row(it)
        self._autofit_all()

    def _popup_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
        try:
            self.tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.tree_menu.grab_release()

    def _remove_sel(self):
        # Operate on the tree selection directly so errored rows (which never
        # made it into self.items) can also be removed.
        for iid in self.tree.selection():
            it = next((x for x in self.items if x["iid"] == iid), None)
            if it and it["status"].startswith("downloading"):
                continue  # don't yank an in-progress download
            self.tree.delete(iid)
            if it:
                self.items.remove(it)

    def _clear_done(self):
        for it in list(self.items):
            if it["status"].startswith("done"):
                self.tree.delete(it["iid"])
                self.items.remove(it)

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.outdir)
        if d:
            self.outdir = d
            self.outdir_lbl.configure(text=d)
            cfg = core.load_config(); cfg["outdir"] = d; core.save_config(cfg)

    # ============================================================= DOWNLOAD ==
    def _download_all(self):
        pending = [it for it in self.items if not it["status"].startswith("done")]
        if not pending:
            return
        self.dl_btn.configure(state="disabled")

        def work():
            total = len(pending)
            for n, it in enumerate(pending, 1):
                self._post(lambda it=it: (it.update(status="downloading"),
                                          self._refresh_row(it)))
                self._post(lambda n=n: self._set_dl(f"Item {n}/{total}…", "gray"))

                def prog(frac, msg, it=it, n=n):
                    def upd():
                        if frac >= 0:
                            self.progress.configure(mode="determinate", value=frac)
                            self.progress.stop()
                        else:
                            if str(self.progress["mode"]) != "indeterminate":
                                self.progress.configure(mode="indeterminate")
                                self.progress.start(12)
                        pct = f"{frac*100:.0f}% · " if frac >= 0 else ""
                        self._set_dl(f"Item {n}/{total} · {pct}{msg}", "gray")
                        it["status"] = ("downloading " + msg) if frac < 1 else "done"
                        self._refresh_row(it)
                    self._post(upd)

                try:
                    out = core.download(
                        self.session, it["url"], it["height"], self.outdir,
                        progress_cb=prog, audio_indices=it["audio_idx"],
                        include_subs=it["subs"], container=it["container"],
                        info=it["info"], out_name=it.get("title"))
                    self._post(lambda it=it, out=out: (it.update(status="done ✓"),
                                                       self._refresh_row(it),
                                                       self._logmsg(f"Saved → {out}")))
                except Exception as e:
                    self._post(lambda it=it, e=e: (it.update(status="error"),
                                                   self._refresh_row(it),
                                                   self._logmsg(f"{it['url']}: {e}")))
            self._post(lambda: (self.progress.stop(),
                                self.progress.configure(mode="determinate", value=0),
                                self._set_dl("All done", "green"),
                                self.dl_btn.configure(state="normal")))
        threading.Thread(target=work, daemon=True).start()


def main():
    if "--selftest" in sys.argv:
        try:
            _, final = core.Session().get(core.BASE + "/")
            print("SELFTEST-OK", final, "| CA:", core._CA_FILE)
        except Exception as e:
            print("SELFTEST-FAIL", type(e).__name__, e)
        return

    root = tk.Tk()
    root.title("KDownloader")
    root.geometry("720x760")
    try:
        root.call("tk", "scaling", 2.0)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
