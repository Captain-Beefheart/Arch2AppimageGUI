#!/usr/bin/env python3
"""Arch2AppimageGUI — Etcher-style GUI to search prebuilt Arch repos and build
AppImages with one click.

Run:  python3 gui/arch2appimage_gui.py     (or double-click the launcher)
All repo syncing, downloading and building happens on a worker thread; the UI
thread only polls a queue, so the window never freezes during a build.
"""

from __future__ import annotations

import os
import queue
import sys
import threading

# Make the sibling `a2a` package importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from a2a import __version__, config
from a2a.builder import build_appimage
from a2a.repos import Index, build_index

# ---- palette (Etcher-ish) --------------------------------------------------
BG = "#1e2233"
BG2 = "#262b40"
FG = "#e7e9f2"
MUTED = "#9aa0b9"
ACCENT = "#4f7cff"
ACCENT_HOVER = "#6a90ff"
OK = "#3ecf8e"
WARN = "#ffb454"


class Bus:
    """Thread-safe channel from worker -> UI."""

    def __init__(self) -> None:
        self.q: queue.Queue[tuple] = queue.Queue()

    def post(self, kind: str, *payload) -> None:
        self.q.put((kind,) + payload)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = config.Config.load()
        self.bus = Bus()
        self.index: Index | None = None
        self.results = []
        self.selected = None
        self.busy = False

        root.title(f"Arch2AppimageGUI {__version__}")
        root.geometry("880x620")
        root.minsize(760, 540)
        root.configure(bg=BG)

        self._init_style()
        self._build_ui()

        self.root.after(80, self._pump)
        self.set_status("Syncing repositories…")
        self._run_bg(self._task_sync, force=False)

    # ---------------------------------------------------------------- styling
    def _init_style(self) -> None:
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=BG2,
                     bordercolor=BG2, focuscolor=ACCENT)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=BG2)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED)
        st.configure("Title.TLabel", background=BG, foreground=FG,
                     font=("Segoe UI", 20, "bold"))
        st.configure("TEntry", fieldbackground=BG2, foreground=FG,
                     insertcolor=FG, padding=6)
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("Treeview", background=BG2, fieldbackground=BG2,
                     foreground=FG, rowheight=26, borderwidth=0)
        st.configure("Treeview.Heading", background=BG, foreground=MUTED,
                     relief="flat")
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "#ffffff")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     font=("Segoe UI", 11, "bold"), borderwidth=0, padding=(18, 10))
        st.map("Accent.TButton",
               background=[("active", ACCENT_HOVER), ("disabled", "#3a3f57")],
               foreground=[("disabled", MUTED)])
        st.configure("TButton", background=BG2, foreground=FG, borderwidth=0,
                     padding=(12, 7))
        st.map("TButton", background=[("active", "#333a55")])
        st.configure("A2A.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor=BG2, borderwidth=0)

    # ------------------------------------------------------------------- build
    def _build_ui(self) -> None:
        pad = 16
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=pad, pady=(pad, 6))
        ttk.Label(header, text="Arch2AppimageGUI", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="⚙ Repos", command=self._open_repos).pack(side="right")
        ttk.Label(header, text="prebuilt Arch package → AppImage",
                  style="Muted.TLabel").pack(side="right", padx=12)

        # search row
        srow = ttk.Frame(self.root)
        srow.pack(fill="x", padx=pad, pady=6)
        self.query = tk.StringVar()
        ent = ttk.Entry(srow, textvariable=self.query, font=("Segoe UI", 12))
        ent.pack(side="left", fill="x", expand=True, ipady=3)
        ent.bind("<Return>", lambda _e: self.on_search())
        ent.focus_set()
        self.search_btn = ttk.Button(srow, text="Search", command=self.on_search)
        self.search_btn.pack(side="left", padx=(8, 0))

        # results table
        mid = ttk.Frame(self.root, style="Card.TFrame")
        mid.pack(fill="both", expand=True, padx=pad, pady=6)
        cols = ("name", "version", "repo", "desc")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for key, txt, w, stretch in (
            ("name", "Package", 200, False),
            ("version", "Version", 140, False),
            ("repo", "Repo", 110, False),
            ("desc", "Description", 380, True),
        ):
            self.tree.heading(key, text=txt)
            self.tree.column(key, width=w, stretch=stretch, anchor="w")
        vs = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self.on_build())

        # options row
        orow = ttk.Frame(self.root)
        orow.pack(fill="x", padx=pad, pady=(6, 0))
        self.bundle_var = tk.BooleanVar(value=self.cfg.bundle_deps)
        ttk.Checkbutton(orow, text="Bundle shared-library dependencies",
                        variable=self.bundle_var,
                        command=self._toggle_bundle).pack(side="left")
        ttk.Button(orow, text="Output: " + self._short(self.cfg.output_dir),
                   command=self._pick_output).pack(side="right")
        self.out_btn = orow.winfo_children()[-1]

        # build button
        brow = ttk.Frame(self.root)
        brow.pack(fill="x", padx=pad, pady=(10, 4))
        self.build_btn = ttk.Button(brow, text="Build AppImage",
                                    style="Accent.TButton",
                                    command=self.on_build, state="disabled")
        self.build_btn.pack(side="right")

        # progress + status
        self.progress = ttk.Progressbar(self.root, style="A2A.Horizontal.TProgressbar",
                                        mode="determinate", maximum=1.0)
        self.progress.pack(fill="x", padx=pad, pady=(4, 2))
        self.status = ttk.Label(self.root, text="", style="Muted.TLabel", anchor="w")
        self.status.pack(fill="x", padx=pad, pady=(0, pad))

    # ------------------------------------------------------------- UI helpers
    def _short(self, path: str, n: int = 28) -> str:
        return path if len(path) <= n else "…" + path[-(n - 1):]

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def set_progress(self, frac: float | None) -> None:
        if frac is None:
            self.progress.configure(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=max(0.0, min(1.0, frac)))

    def _busy(self, on: bool) -> None:
        self.busy = on
        state = "disabled" if on else "normal"
        self.search_btn.configure(state=state)
        self.build_btn.configure(state="disabled" if (on or not self.selected) else "normal")

    # ----------------------------------------------------------- worker glue
    def _run_bg(self, target, **kw) -> None:
        self._busy(True)
        threading.Thread(target=target, kwargs=kw, daemon=True).start()

    def _progress_cb(self, msg: str, frac: float | None) -> None:
        self.bus.post("progress", msg, frac)

    def _task_sync(self, force: bool) -> None:
        try:
            index = build_index(self.cfg, progress=self._progress_cb, force=force)
            self.bus.post("index_ready", index)
        except Exception as exc:  # noqa: BLE001
            self.bus.post("error", f"Sync failed: {exc}")

    def _task_build(self, pkg) -> None:
        try:
            self.cfg.bundle_deps = self.bundle_var.get()
            out = build_appimage(pkg, self.index, self.cfg,
                                 progress=self._progress_cb)
            self.bus.post("build_done", out)
        except Exception as exc:  # noqa: BLE001
            self.bus.post("error", f"Build failed: {exc}")

    def _pump(self) -> None:
        try:
            while True:
                kind, *payload = self.bus.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _handle(self, kind: str, payload: list) -> None:
        if kind == "progress":
            msg, frac = payload
            self.set_status(msg)
            self.set_progress(frac)
        elif kind == "index_ready":
            self.index = payload[0]
            self._busy(False)
            self.set_status(f"Ready — {self.index.count():,} packages "
                            f"across {len(self.cfg.enabled_repos())} repos.")
            self.set_progress(1.0)
            if self.query.get().strip():
                self.on_search()
        elif kind == "build_done":
            self._busy(False)
            out = payload[0]
            self.set_status(f"Built {os.path.basename(out)}")
            self.set_progress(1.0)
            messagebox.showinfo("Done", f"AppImage created:\n\n{out}")
        elif kind == "error":
            self._busy(False)
            self.set_progress(0.0)
            self.set_status(payload[0])
            messagebox.showerror("Arch2AppimageGUI", payload[0])

    # -------------------------------------------------------------- actions
    def on_search(self) -> None:
        q = self.query.get().strip()
        if not q:
            return
        if self.index is None:
            self.set_status("Still syncing repositories… search will run when ready.")
            return
        self.results = self.index.search(q, limit=500)
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.results):
            self.tree.insert("", "end", iid=str(i),
                             values=(p.name, p.version, p.repo, p.desc))
        self.selected = None
        self.build_btn.configure(state="disabled")
        self.set_status(f"{len(self.results)} result(s) for “{q}”"
                        if self.results else f"No matches for “{q}”.")

    def _on_select(self, _e) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.selected = self.results[int(sel[0])]
        if not self.busy:
            self.build_btn.configure(state="normal")

    def on_build(self) -> None:
        if self.busy or not self.selected:
            return
        pkg = self.selected
        self.set_status(f"Building {pkg.name} {pkg.version}…")
        self._run_bg(self._task_build, pkg=pkg)

    def _toggle_bundle(self) -> None:
        self.cfg.bundle_deps = self.bundle_var.get()
        self.cfg.save()

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(initialdir=self.cfg.output_dir,
                                    title="Choose output folder")
        if d:
            self.cfg.output_dir = d
            self.cfg.save()
            self.out_btn.configure(text="Output: " + self._short(d))

    # ------------------------------------------------------------ repo dialog
    def _open_repos(self) -> None:
        RepoDialog(self.root, self)


class RepoDialog(tk.Toplevel):
    def __init__(self, parent, app: App) -> None:
        super().__init__(parent)
        self.app = app
        self.title("Repositories")
        self.configure(bg=BG)
        self.geometry("560x420")
        self.transient(parent)
        self.grab_set()

        ttk.Label(self, text="Enable prebuilt-binary repositories",
                  style="Title.TLabel").pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(self, text="Every enabled repo is searched; each result is one-click buildable.",
                  style="Muted.TLabel").pack(anchor="w", padx=16)

        body = ttk.Frame(self, style="Card.TFrame")
        body.pack(fill="both", expand=True, padx=16, pady=12)
        self.vars = {}
        for r in app.cfg.repos:
            v = tk.BooleanVar(value=r.enabled)
            self.vars[r.name] = v
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill="x", padx=10, pady=3)
            ttk.Checkbutton(row, text=r.name, variable=v).pack(side="left")
            ttk.Label(row, text=r.db_url, style="Muted.TLabel",
                      background=BG2).pack(side="right")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save & re-sync", style="Accent.TButton",
                   command=self._save).pack(side="right", padx=8)

    def _save(self) -> None:
        for r in self.app.cfg.repos:
            r.enabled = self.vars[r.name].get()
        self.app.cfg.save()
        self.destroy()
        self.app.set_status("Re-syncing repositories…")
        self.app._run_bg(self.app._task_sync, force=False)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
