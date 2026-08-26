"""Melodex - desktop music downloader.

Paste a link from Tidal, Spotify, Deezer, YouTube Music, SoundCloud, Bandcamp
or any other site yt-dlp supports, pick a folder and an audio format, and the
tracks land on disk tagged and ready to play.

Run from source:   python app.py
"""

from __future__ import annotations

import sys

import bootstrap

# Pull in the third-party dependencies before core imports them.
bootstrap.ensure_packages()

import os
import re
import subprocess
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, filedialog, messagebox

import core


# ------------------------------------------------------- HiDPI awareness ----

def enable_dpi_awareness() -> None:
    """Ask Windows for crisp rendering on scaled / mixed-DPI displays."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
    except ImportError:
        return
    for call in (
        lambda: windll.user32.SetProcessDpiAwarenessContext(-4),  # per-monitor v2
        lambda: windll.shcore.SetProcessDpiAwareness(2),
        lambda: windll.user32.SetProcessDPIAware(),
    ):
        try:
            call()
            return
        except Exception:  # noqa: BLE001 - older Windows lacks these entry points
            continue


enable_dpi_awareness()


# ----------------------------------------------------------------- theme ----

BG        = "#0b0d11"   # window background
PANEL     = "#12151c"   # raised panel
CARD      = "#151922"   # card surface
INPUT_BG  = "#0e1116"   # input field
BORDER    = "#252b39"   # resting border
BORDER_H  = "#3a4256"   # hover border

TEXT      = "#f2f5fb"
SUBTLE    = "#c2c8d6"
MUTED     = "#79808f"

ACCENT    = "#ffffff"   # primary action
ACCENT_H  = "#d8dce4"
ACCENT_2  = "#6ee7ff"   # informational highlight
OK        = "#4ade80"
WARN      = "#fbbf24"
ERR       = "#f87171"

FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO = "Consolas" if sys.platform == "win32" else "Menlo"


def apply_titlebar_dark(root: tk.Tk) -> None:
    """Dark title bar on Windows. The window itself stays fully opaque."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll, byref, c_int, sizeof
        root.update_idletasks()
        try:
            hwnd = windll.user32.GetParent(root.winfo_id())
        except Exception:  # noqa: BLE001
            hwnd = root.winfo_id()
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, old and new
            try:
                value = c_int(1)
                windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, byref(value), sizeof(value))
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def apply_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)

    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Card.TFrame", background=CARD)

    style.configure("TLabel", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=(FONT, 10))
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=(FONT, 22, "bold"))
    style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("Field.TLabel", background=BG, foreground=SUBTLE, font=(FONT, 9, "bold"))
    style.configure("Hint.TLabel", background=BG, foreground=ACCENT_2, font=(FONT, 9))
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
    style.configure("StatusOK.TLabel", background=BG, foreground=OK, font=(FONT, 9, "bold"))
    style.configure("StatusWarn.TLabel", background=BG, foreground=WARN, font=(FONT, 9, "bold"))
    style.configure("StatusErr.TLabel", background=BG, foreground=ERR, font=(FONT, 9, "bold"))

    style.configure(
        "Modern.TEntry",
        fieldbackground=INPUT_BG, foreground=TEXT, bordercolor=BORDER,
        lightcolor=BORDER, darkcolor=BORDER, insertcolor=ACCENT_2, padding=10,
    )
    style.map(
        "Modern.TEntry",
        bordercolor=[("focus", ACCENT_2)],
        lightcolor=[("focus", ACCENT_2)],
        darkcolor=[("focus", ACCENT_2)],
    )

    style.configure(
        "Modern.TCombobox",
        fieldbackground=INPUT_BG, background=INPUT_BG, foreground=TEXT,
        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
        arrowcolor=SUBTLE, padding=8,
    )
    style.map(
        "Modern.TCombobox",
        fieldbackground=[("readonly", INPUT_BG)],
        bordercolor=[("focus", ACCENT_2), ("hover", BORDER_H)],
        arrowcolor=[("hover", ACCENT_2)],
    )

    style.configure(
        "Accent.TButton",
        background=ACCENT, foreground="#06080c", font=(FONT, 12, "bold"),
        padding=(20, 14), borderwidth=0, focusthickness=0, relief="flat",
    )
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_H), ("disabled", BORDER)],
        foreground=[("disabled", MUTED)],
    )

    style.configure(
        "Ghost.TButton",
        background=CARD, foreground=TEXT, font=(FONT, 9), padding=(12, 9),
        borderwidth=1, bordercolor=BORDER, focusthickness=0, relief="flat",
    )
    style.map(
        "Ghost.TButton",
        background=[("active", PANEL)],
        bordercolor=[("active", ACCENT_2)],
        foreground=[("active", ACCENT_2)],
    )

    style.configure("TCheckbutton", background=BG, foreground=SUBTLE, font=(FONT, 10))
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])

    style.configure(
        "Modern.Horizontal.TProgressbar",
        troughcolor=CARD, background=ACCENT_2, bordercolor=CARD,
        lightcolor=ACCENT_2, darkcolor=ACCENT_2, thickness=6,
    )
    style.configure(
        "Vertical.TScrollbar",
        background=CARD, troughcolor=PANEL, bordercolor=PANEL,
        arrowcolor=MUTED, gripcount=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER_H)])


def setup_scaling(root: tk.Tk) -> None:
    dpi = root.winfo_fpixels("1i")
    root.tk.call("tk", "scaling", max(1.0, dpi / 96.0) * 96 / 72)


def load_icon(root: tk.Tk) -> None:
    ico = core.resource_path("icon.ico")
    png = core.resource_path("icon.png")
    try:
        if sys.platform == "win32" and os.path.exists(ico):
            root.iconbitmap(default=ico)
            return
    except tk.TclError:
        pass
    try:
        if os.path.exists(png):
            image = tk.PhotoImage(file=png)
            root.iconphoto(True, image)
            root._icon_ref = image  # keep a reference so Tk does not free it
    except tk.TclError:
        pass


def open_folder(path: str) -> None:
    if not os.path.isdir(path):
        return
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 - opening a user-chosen folder
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


# ------------------------------------------------------- settings dialog ----

class SettingsDialog(tk.Toplevel):
    """Everything that is not needed on every download."""

    def __init__(self, parent: "App"):
        super().__init__(parent.root)
        self.app = parent
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent.root)
        apply_titlebar_dark(self)  # type: ignore[arg-type]

        cfg = parent.cfg
        body = ttk.Frame(self, padding=24)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(body, text="FILE NAMES", style="Field.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 6))
        self.naming = tk.StringVar(value=cfg["naming"])
        naming_box = ttk.Combobox(
            body, textvariable=self.naming, state="readonly", width=26,
            style="Modern.TCombobox", values=["artist-title", "title"],
        )
        naming_box.grid(row=row, column=1, sticky="we", pady=(0, 6), padx=(16, 0))
        row += 1
        ttk.Label(
            body, text="artist-title gives \"Artist - Song.mp3\"", style="Sub.TLabel"
        ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=(0, 16))
        row += 1

        ttk.Label(body, text="PARALLEL DOWNLOADS", style="Field.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 6))
        self.concurrency = tk.IntVar(value=int(cfg["concurrency"]))
        ttk.Spinbox(
            body, from_=1, to=8, textvariable=self.concurrency, width=6,
            background=INPUT_BG, foreground=TEXT,
        ).grid(row=row, column=1, sticky="w", pady=(0, 6), padx=(16, 0))
        row += 1
        ttk.Label(
            body, text="Higher is faster but gets rate-limited sooner. 3 is a safe default.",
            style="Sub.TLabel",
        ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=(0, 16))
        row += 1

        ttk.Label(body, text="COOKIES", style="Field.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 6))
        self.cookies = tk.StringVar(value=cfg["cookies_browser"])
        ttk.Combobox(
            body, textvariable=self.cookies, state="readonly", width=26,
            style="Modern.TCombobox", values=core.COOKIE_BROWSERS,
        ).grid(row=row, column=1, sticky="we", pady=(0, 6), padx=(16, 0))
        row += 1
        ttk.Label(
            body,
            text="Reuse a browser login when YouTube answers 403 or asks you to sign in.\n"
                 "Close that browser first, otherwise its cookie database stays locked.",
            style="Sub.TLabel", justify="left",
        ).grid(row=row, column=1, sticky="w", padx=(16, 0), pady=(0, 16))
        row += 1

        self.embed_metadata = tk.BooleanVar(value=cfg["embed_metadata"])
        self.embed_thumbnail = tk.BooleanVar(value=cfg["embed_thumbnail"])
        self.skip_existing = tk.BooleanVar(value=cfg["skip_existing"])
        for text, var in (
            ("Write title / artist tags into the file", self.embed_metadata),
            ("Embed cover art", self.embed_thumbnail),
            ("Skip tracks that are already in the folder", self.skip_existing),
        ):
            ttk.Checkbutton(body, text=text, variable=var).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=2)
            row += 1

        ttk.Separator(body, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="we", pady=18)
        row += 1

        ttk.Label(body, text="MAINTENANCE", style="Field.TLabel").grid(
            row=row, column=0, sticky="w")
        tools = ttk.Frame(body)
        tools.grid(row=row, column=1, sticky="w", padx=(16, 0))
        ttk.Button(tools, text="Update yt-dlp", style="Ghost.TButton",
                   command=self._update_ytdlp).pack(side="left")
        ttk.Button(tools, text="ffmpeg setup", style="Ghost.TButton",
                   command=lambda: parent.ffmpeg_setup(force=True)).pack(side="left", padx=(8, 0))
        ttk.Button(tools, text="Open config folder", style="Ghost.TButton",
                   command=lambda: open_folder(core.data_dir())).pack(side="left", padx=(8, 0))
        row += 1

        actions = ttk.Frame(body)
        actions.grid(row=row, column=0, columnspan=2, sticky="e", pady=(24, 0))
        ttk.Button(actions, text="Cancel", style="Ghost.TButton",
                   command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left")

        self.update_idletasks()
        self._center_on(parent.root)
        self.grab_set()

    def _center_on(self, other: tk.Misc) -> None:
        x = other.winfo_rootx() + (other.winfo_width() - self.winfo_width()) // 2
        y = other.winfo_rooty() + (other.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _update_ytdlp(self) -> None:
        self.app.log_line("Updating yt-dlp...", "muted")
        threading.Thread(target=self._update_worker, daemon=True).start()

    def _update_worker(self) -> None:
        ok = bootstrap.upgrade_ytdlp(log=lambda m: self.app.ui(self.app.log_line, m, "muted"))
        if ok:
            self.app.ui(self.app.log_line, "yt-dlp updated. Restart the app to load it.", "ok")
        else:
            self.app.ui(self.app.log_line, "yt-dlp update did not complete.", "err")

    def _save(self) -> None:
        cfg = self.app.cfg
        cfg["naming"] = self.naming.get()
        cfg["concurrency"] = max(1, min(8, int(self.concurrency.get() or 1)))
        cfg["cookies_browser"] = self.cookies.get()
        cfg["embed_metadata"] = bool(self.embed_metadata.get())
        cfg["embed_thumbnail"] = bool(self.embed_thumbnail.get())
        cfg["skip_existing"] = bool(self.skip_existing.get())
        core.save_config(cfg)
        self.app.log_line("Settings saved.", "ok")
        self.destroy()


# ------------------------------------------------------------------ app ----

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = core.load_config()
        self.ffmpeg_dir = core.find_ffmpeg_dir()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.fractions: dict[int, float] = {}
        self.fraction_lock = threading.Lock()
        self.total = 0

        root.title(core.APP_NAME)
        load_icon(root)
        apply_theme(root)
        setup_scaling(root)
        root.geometry("820x760")
        root.minsize(680, 640)
        root.after(10, lambda: apply_titlebar_dark(root))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._refresh_quality_state()
        root.after(300, self.startup_checks)

    # ---------------------------------------------------------- building ----

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(9, weight=1)  # the log soaks up the spare height

        # Header
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="we")
        header.columnconfigure(1, weight=1)
        titles = ttk.Frame(header)
        titles.grid(row=0, column=1, sticky="w")
        ttk.Label(titles, text=core.APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            titles,
            text="Fetch music from Tidal, Spotify, Deezer, YouTube "
                 "Music, SoundCloud, Bandcamp and more",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="Settings", style="Ghost.TButton",
                   command=self.open_settings).grid(row=0, column=2, sticky="ne")

        tk.Frame(outer, bg=BORDER, height=1).grid(row=1, column=0, sticky="we", pady=(20, 20))

        # Warning banner (ffmpeg / update notices). Hidden until needed.
        self.banner = tk.Frame(outer, bg=CARD, highlightthickness=1,
                               highlightbackground=WARN, highlightcolor=WARN)
        self.banner_text = tk.StringVar()
        tk.Label(self.banner, textvariable=self.banner_text, bg=CARD, fg=WARN,
                 font=(FONT, 9), justify="left", anchor="w", padx=14, pady=10
                 ).pack(side="left", fill="x", expand=True)
        self.banner_button = ttk.Button(self.banner, text="Fix", style="Ghost.TButton")
        self.banner_button.pack(side="right", padx=10, pady=8)

        # Link
        ttk.Label(outer, text="LINK", style="Field.TLabel").grid(row=3, column=0, sticky="w")
        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", self._on_url_change)
        entry = ttk.Entry(outer, textvariable=self.url_var, style="Modern.TEntry",
                          font=(FONT, 11))
        entry.grid(row=4, column=0, sticky="we", pady=(8, 6), ipady=4)
        entry.bind("<Return>", lambda _e: self.start())
        entry.focus_set()

        self.url_info = tk.StringVar(value="Paste a track, album or playlist link")
        self.url_info_label = ttk.Label(outer, textvariable=self.url_info, style="Status.TLabel")
        self.url_info_label.grid(row=5, column=0, sticky="w", pady=(0, 18))

        # Destination
        ttk.Label(outer, text="DESTINATION FOLDER", style="Field.TLabel").grid(
            row=6, column=0, sticky="w")
        folder_row = ttk.Frame(outer)
        folder_row.grid(row=7, column=0, sticky="we", pady=(8, 18))
        folder_row.columnconfigure(0, weight=1)
        self.dir_var = tk.StringVar(value=self.cfg["output_dir"])
        ttk.Entry(folder_row, textvariable=self.dir_var, style="Modern.TEntry",
                  font=(FONT, 10)).grid(row=0, column=0, sticky="we", ipady=4)
        ttk.Button(folder_row, text="Browse", style="Ghost.TButton",
                   command=self.browse).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(folder_row, text="Open", style="Ghost.TButton",
                   command=lambda: open_folder(self.dir_var.get())).grid(row=0, column=2, padx=(8, 0))

        # Format + bitrate, then the log fills the rest.
        options = ttk.Frame(outer)
        options.grid(row=8, column=0, sticky="we")
        options.columnconfigure(0, weight=3)
        options.columnconfigure(1, weight=2)
        self._build_format_row(options)

        log_wrap = tk.Frame(outer, bg=BORDER)
        log_wrap.grid(row=9, column=0, sticky="nsew")
        log_inner = tk.Frame(log_wrap, bg=CARD)
        log_inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.log = tk.Text(
            log_inner, height=9, wrap="word", state="disabled", bg=CARD, fg=SUBTLE,
            insertbackground=TEXT, selectbackground=ACCENT_2, selectforeground="#06080c",
            relief="flat", font=(MONO, 9), padx=16, pady=12,
            borderwidth=0, highlightthickness=0,
        )
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_inner, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)
        for tag, colour in (("muted", MUTED), ("ok", OK), ("err", ERR),
                            ("warn", WARN), ("accent", ACCENT_2)):
            self.log.tag_configure(tag, foreground=colour)

        self.progress = ttk.Progressbar(outer, style="Modern.Horizontal.TProgressbar",
                                        mode="determinate", maximum=100)
        self.progress.grid(row=10, column=0, sticky="we", pady=(18, 8))

        status_row = ttk.Frame(outer)
        status_row.grid(row=11, column=0, sticky="we", pady=(0, 16))
        status_row.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(status_row, textvariable=self.status, style="Status.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.counter = tk.StringVar(value="")
        ttk.Label(status_row, textvariable=self.counter, style="Status.TLabel").grid(
            row=0, column=1, sticky="e")

        buttons = ttk.Frame(outer)
        buttons.grid(row=12, column=0, sticky="we")
        buttons.columnconfigure(0, weight=1)
        self.go_button = ttk.Button(buttons, text="Download", style="Accent.TButton",
                                    command=self.start)
        self.go_button.grid(row=0, column=0, sticky="we")
        self.stop_button = ttk.Button(buttons, text="Stop", style="Ghost.TButton",
                                      command=self.request_cancel, state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(10, 0), sticky="ns")

    def _build_format_row(self, parent: ttk.Frame) -> None:
        left = ttk.Frame(parent)
        left.grid(row=0, column=0, sticky="we", padx=(0, 16))
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="FORMAT", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar(
            value=core.FORMAT_LABEL.get(self.cfg["audio_format"], "MP3"))
        self.format_box = ttk.Combobox(
            left, textvariable=self.format_var, state="readonly", style="Modern.TCombobox",
            values=[core.FORMAT_LABEL[f] for f in core.FORMAT_ORDER],
        )
        self.format_box.grid(row=1, column=0, sticky="we", pady=(8, 6))
        self.format_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_quality_state())

        right = ttk.Frame(parent)
        right.grid(row=0, column=1, sticky="we")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="BITRATE", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        self.quality_var = tk.StringVar(value=str(self.cfg["quality"]))
        self.quality_box = ttk.Combobox(
            right, textvariable=self.quality_var, state="readonly",
            style="Modern.TCombobox",
            values=[f"{b} kbps" for b in core.LOSSY_BITRATES],
        )
        self.quality_box.grid(row=1, column=0, sticky="we", pady=(8, 6))
        self.quality_var.set(f"{self.cfg['quality']} kbps")

        self.format_hint = tk.StringVar()
        ttk.Label(parent, textvariable=self.format_hint, style="Sub.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))

    # ----------------------------------------------------------- helpers ----

    def selected_format(self) -> str:
        label = self.format_var.get()
        for key, value in core.FORMAT_LABEL.items():
            if value == label:
                return key
        return "mp3"

    def selected_quality(self) -> str:
        return re.sub(r"\D", "", self.quality_var.get()) or "320"

    def _refresh_quality_state(self, *_args) -> None:
        fmt = self.selected_format()
        self.format_hint.set(core.FORMAT_HINT.get(fmt, ""))
        lossy = not core.is_lossless(fmt) and fmt != "original"
        self.quality_box.configure(state="readonly" if lossy else "disabled")
        self._check_ffmpeg_banner()

    def ui(self, fn, *args) -> None:
        """Run a callback on the Tk thread."""
        try:
            self.root.after(0, lambda: fn(*args))
        except RuntimeError:
            pass  # window already destroyed

    def log_line(self, message: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", tag or "")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, message: str, kind: str = "muted") -> None:
        self.status.set(message)
        self.status_label.configure(style={
            "muted": "Status.TLabel", "ok": "StatusOK.TLabel",
            "warn": "StatusWarn.TLabel", "err": "StatusErr.TLabel",
        }.get(kind, "Status.TLabel"))

    def set_counter(self, message: str) -> None:
        self.counter.set(message)

    def set_progress(self, percent: float) -> None:
        self.progress["value"] = max(0.0, min(100.0, percent))

    def set_busy(self, busy: bool) -> None:
        self.go_button.configure(state="disabled" if busy else "normal",
                                 text="Downloading..." if busy else "Download")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def browse(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.dir_var.get() or os.path.expanduser("~"))
        if chosen:
            self.dir_var.set(chosen)
            self.cfg["output_dir"] = chosen
            core.save_config(self.cfg)

    def open_settings(self) -> None:
        SettingsDialog(self)

    def on_close(self) -> None:
        self.cancel.set()
        self._persist()
        self.root.destroy()

    def _persist(self) -> None:
        self.cfg["output_dir"] = self.dir_var.get().strip() or self.cfg["output_dir"]
        self.cfg["audio_format"] = self.selected_format()
        self.cfg["quality"] = self.selected_quality()
        core.save_config(self.cfg)

    # -------------------------------------------------------- ffmpeg ------

    def show_banner(self, text: str, button_text: str, command) -> None:
        self.banner_text.set(text)
        self.banner_button.configure(text=button_text, command=command)
        self.banner.grid(row=2, column=0, sticky="we", pady=(0, 18))

    def hide_banner(self) -> None:
        self.banner.grid_forget()

    def _check_ffmpeg_banner(self) -> None:
        if not core.format_needs_ffmpeg(self.selected_format()):
            self.hide_banner()
            return
        if self.ffmpeg_dir:
            self.hide_banner()
            return
        self.show_banner(
            "ffmpeg is not installed. It is required to convert audio - "
            "without it only the Original format works.",
            "Install", lambda: self.ffmpeg_setup(force=True),
        )

    def startup_checks(self) -> None:
        self.log_line(f"{core.APP_NAME} {core.APP_VERSION}", "accent")
        if self.ffmpeg_dir:
            version = core.ffmpeg_version(self.ffmpeg_dir)
            self.log_line(f"ffmpeg: {version or self.ffmpeg_dir}", "muted")
        else:
            self.log_line("ffmpeg: not found", "warn")
        self.log_line(f"Saving to: {self.dir_var.get()}", "muted")
        self._check_ffmpeg_banner()

    def ffmpeg_setup(self, force: bool = False) -> None:
        """Find ffmpeg again, and offer to download it if it is still missing."""
        self.ffmpeg_dir = core.find_ffmpeg_dir()
        if self.ffmpeg_dir and not force:
            self._check_ffmpeg_banner()
            return
        if self.ffmpeg_dir:
            version = core.ffmpeg_version(self.ffmpeg_dir)
            replace = messagebox.askyesno(
                "ffmpeg found",
                f"ffmpeg is already available:\n\n{version or self.ffmpeg_dir}\n\n"
                "Download a fresh copy anyway?",
            )
            if not replace:
                self._check_ffmpeg_banner()
                return

        if sys.platform != "win32":
            messagebox.showinfo("Install ffmpeg", bootstrap.manual_ffmpeg_hint())
            return
        if not messagebox.askyesno(
            "Install ffmpeg",
            "Download a static ffmpeg build (about 40 MB) into\n"
            f"{bootstrap.ffmpeg_target_dir()}?\n\n"
            "Nothing outside that folder is touched.",
        ):
            return

        self.set_busy(True)
        self.set_status("Installing ffmpeg...")
        threading.Thread(target=self._ffmpeg_worker, daemon=True).start()

    def _ffmpeg_worker(self) -> None:
        path = bootstrap.install_ffmpeg_windows(
            progress=lambda pct: self.ui(self.set_progress, pct),
            log=lambda message: self.ui(self.log_line, message, "muted"),
            locate=core.find_ffmpeg_dir,
        )
        self.ffmpeg_dir = path or core.find_ffmpeg_dir()
        if self.ffmpeg_dir:
            self.ui(self.log_line, "ffmpeg is ready.", "ok")
            self.ui(self.set_status, "ffmpeg installed", "ok")
        else:
            self.ui(self.log_line, "ffmpeg install failed.", "err")
            self.ui(self.set_status, "ffmpeg install failed", "err")
        self.ui(self.set_progress, 0)
        self.ui(self.set_busy, False)
        self.ui(self._check_ffmpeg_banner)

    # ------------------------------------------------------------- flow ----

    def _on_url_change(self, *_args) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.url_info_label.configure(style="Status.TLabel")
            self.url_info.set("Paste a track, album or playlist link")
            return
        source = core.detect_source(url)
        if source == "unknown":
            self.url_info_label.configure(style="StatusWarn.TLabel")
            self.url_info.set("That does not look like a URL")
            return
        label = core.SOURCE_LABEL.get(source, source)
        kind = core.detect_kind(url, source)
        self.url_info_label.configure(style="Hint.TLabel")
        if source == "generic":
            self.url_info.set("Unknown site - will try yt-dlp anyway")
        elif kind:
            self.url_info.set(f"{label}  -  {kind}")
        else:
            self.url_info.set(label)

    def request_cancel(self) -> None:
        if self.cancel.is_set():
            return
        self.cancel.set()
        self.set_status("Stopping after the current track...", "warn")
        self.log_line("Stop requested.", "warn")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        url = self.url_var.get().strip()
        out_dir = self.dir_var.get().strip()
        fmt = self.selected_format()

        if not url:
            messagebox.showwarning("No link", "Paste a link first.")
            return
        if not out_dir:
            messagebox.showwarning("No folder", "Choose a destination folder.")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            probe = os.path.join(out_dir, ".melodex-write-test")
            with open(probe, "w", encoding="utf-8"):
                pass
            os.remove(probe)
        except OSError as exc:
            messagebox.showerror("Folder not writable",
                                 f"Cannot write to:\n{out_dir}\n\n{exc}")
            return

        if core.format_needs_ffmpeg(fmt) and not self.ffmpeg_dir:
            self.ffmpeg_dir = core.find_ffmpeg_dir()
        if core.format_needs_ffmpeg(fmt) and not self.ffmpeg_dir:
            if messagebox.askyesno(
                "ffmpeg required",
                f"Converting to {core.FORMAT_LABEL[fmt]} needs ffmpeg, which is not "
                "installed.\n\nInstall it now?",
            ):
                self.ffmpeg_setup(force=True)
            return

        self._persist()
        self.cancel.clear()
        with self.fraction_lock:
            self.fractions.clear()
        self.set_busy(True)
        self.set_progress(0)
        self.set_counter("")
        self.worker = threading.Thread(target=self._run, args=(url, out_dir), daemon=True)
        self.worker.start()

    def _options(self) -> core.DownloadOptions:
        return core.DownloadOptions(
            audio_format=self.selected_format(),
            quality=self.selected_quality(),
            embed_metadata=bool(self.cfg["embed_metadata"]),
            embed_thumbnail=bool(self.cfg["embed_thumbnail"]),
            naming=self.cfg["naming"],
            cookies_browser=self.cfg["cookies_browser"],
            skip_existing=bool(self.cfg["skip_existing"]),
            ffmpeg_dir=self.ffmpeg_dir,
        )

    def _hook(self, index: int):
        """Per-track yt-dlp progress hook. Also carries the cancel signal."""
        def hook(data: dict) -> None:
            if self.cancel.is_set():
                raise core.Cancelled()
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                fraction = (done / total) if total else 0.0
                self._set_fraction(index, min(0.99, fraction))
            elif status == "finished":
                self._set_fraction(index, 1.0)
        return hook

    def _set_fraction(self, index: int, value: float) -> None:
        with self.fraction_lock:
            self.fractions[index] = value
            overall = sum(self.fractions.values()) / self.total if self.total else 0.0
        self.ui(self.set_progress, overall * 100)

    def _process(self, index: int, track: core.Track, out_dir: str,
                 options: core.DownloadOptions) -> str | None:
        """Download one track. Returns an error message, or None on success."""
        label = f"[{index}/{self.total}] {track.display}"
        if self.cancel.is_set():
            return "cancelled"
        try:
            url = track.url
            resolved_title = ""
            # A track with no URL came from Tidal / Spotify / Deezer, so its
            # artist and title are clean enough to tag the file with.
            tags = ({"title": track.title, "artist": track.artist}
                    if not url and track.title else None)
            if not url:
                self.ui(self.log_line, f"{label} - searching YouTube", "muted")
                url, resolved_title = core.search_youtube(track, options.cookies_browser)

            basename = core.target_basename(track, resolved_title, options.naming)
            if options.skip_existing:
                existing = core.existing_file(out_dir, basename, options.audio_format)
                if existing:
                    self.ui(self.log_line, f"{label} - already downloaded, skipped", "muted")
                    self._set_fraction(index, 1.0)
                    return None

            self.ui(self.log_line, f"{label} - downloading", "accent")
            core.download_track(
                url, out_dir, options, basename, tags=tags,
                progress_hook=self._hook(index),
                on_retry=lambda old, new, why: self.ui(
                    self.log_line,
                    f"{label} - {'/'.join(old)} failed ({why}) - retrying with "
                    f"{'/'.join(new)}",
                    "warn",
                ),
            )
            self.ui(self.log_line, f"{label} - done", "ok")
            self._set_fraction(index, 1.0)
            return None
        except core.Cancelled:
            self.ui(self.log_line, f"{label} - stopped", "warn")
            return "cancelled"
        except core.SourceError as exc:
            self.ui(self.log_line, f"{label} - {exc}", "err")
            self._set_fraction(index, 1.0)
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - never let one track kill the run
            message = core.friendly_error(str(exc))
            self.ui(self.log_line, f"{label} - {message}", "err")
            self._set_fraction(index, 1.0)
            return message

    def _run(self, url: str, out_dir: str) -> None:
        options = self._options()
        try:
            source = core.detect_source(url)
            self.ui(self.set_status, f"Reading {core.SOURCE_LABEL.get(source, source)}...")
            self.ui(self.log_line, f"Link: {url}", "muted")

            playlist = core.fetch_tracks(url, options.cookies_browser)
            tracks = playlist.tracks
            self.total = len(tracks)

            name = f' "{playlist.name}"' if playlist.name else ""
            if self.total > 1:
                self.ui(self.log_line,
                        f"Found {self.total} tracks{name}", "accent")
            else:
                self.ui(self.log_line, f"Track: {tracks[0].display}", "accent")
            if source in core.SEARCH_SOURCES:
                self.ui(self.log_line,
                        f"{core.SOURCE_LABEL[source]} gives metadata only - the audio "
                        "is matched on YouTube.", "muted")

            self.ui(self.set_counter, f"0 / {self.total}")
            workers = 1 if self.total == 1 else max(1, min(int(self.cfg["concurrency"]), self.total))

            errors: list[str] = []
            cancelled = 0
            done = 0

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(self._process, index, track, out_dir, options)
                    for index, track in enumerate(tracks, start=1)
                ]
                for future in futures:
                    error = future.result()
                    done += 1
                    if error == "cancelled":
                        cancelled += 1
                    elif error:
                        errors.append(error)
                    self.ui(self.set_counter, f"{done} / {self.total}")
                    if not self.cancel.is_set():
                        self.ui(self.set_status, f"{done} of {self.total} finished...")

            self._finish(out_dir, errors, cancelled)
        except core.SourceError as exc:
            self.ui(self.set_status, "Failed", "err")
            self.ui(self.log_line, str(exc), "err")
            self.ui(lambda m=str(exc): messagebox.showerror("Cannot read link", m))
        except Exception as exc:  # noqa: BLE001 - last resort so the UI recovers
            message = core.friendly_error(str(exc))
            self.ui(self.set_status, "Failed", "err")
            self.ui(self.log_line, message, "err")
            self.ui(lambda m=message: messagebox.showerror("Error", m))
        finally:
            self.ui(self.set_busy, False)
            self.cancel.clear()

    def _finish(self, out_dir: str, errors: list[str], cancelled: int) -> None:
        succeeded = self.total - len(errors) - cancelled
        if cancelled:
            self.ui(self.set_progress, 100)
            self.ui(self.set_status, f"Stopped - {succeeded} of {self.total} saved", "warn")
            self.ui(self.log_line, f"Stopped. {succeeded} tracks saved.", "warn")
            return
        if not errors:
            self.ui(self.set_progress, 100)
            self.ui(self.set_status, f"Done - saved to {out_dir}", "ok")
            summary = (f"Downloaded {succeeded} tracks."
                       if self.total > 1 else "Download complete.")
            self.ui(lambda m=summary: messagebox.showinfo("Done", m))
            return
        self.ui(self.set_progress, 100)
        self.ui(self.set_status,
                f"Finished with errors - {succeeded} of {self.total} saved", "err")
        preview = "\n".join(f"- {e}" for e in errors[:5])
        extra = f"\n...and {len(errors) - 5} more (see the log)." if len(errors) > 5 else ""
        self.ui(lambda: messagebox.showwarning(
            "Partly done",
            f"Saved {succeeded} of {self.total}.\n\nFailures:\n{preview}{extra}",
        ))


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
