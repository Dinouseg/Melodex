"""First-run setup: make sure the Python dependencies and ffmpeg are present.

Imported by app.py before anything third-party is touched, so a fresh clone
can be started with a plain `python app.py` and still work.

Nothing here is needed by the packaged .exe build, which ships the Python
dependencies inside the executable. ffmpeg is still fetched on demand.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile

# import name -> pip requirement
REQUIRED = {
    "yt_dlp": "yt-dlp>=2024.12.13",
    "requests": "requests>=2.31.0",
    # yt-dlp needs mutagen to write cover art into FLAC / M4A / Opus files.
    "mutagen": "mutagen>=1.47.0",
}

MIN_PYTHON = (3, 10)

# Mirrors are tried in order, smallest first. Several exist on purpose:
# gyan.dev goes down (503) often enough to matter, and it serves a Let's
# Encrypt certificate that Windows machines with a stale root store reject,
# while GitHub uses a different CA entirely. Every one of these is fetched
# with requests, which validates against its own bundled certifi store
# instead of the Windows one, so an out-of-date system root list no longer
# breaks the install.
FFMPEG_MIRRORS = [
    ("gyan.dev",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("GitHub (BtbN)",
     "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
     "ffmpeg-master-latest-win64-gpl-shared.zip"),
    ("GitHub (GyanD mirror)",
     "https://github.com/GyanD/codexffmpeg/releases/latest"),  # resolved via API
]


def frozen() -> bool:
    """True when running from a PyInstaller build."""
    return getattr(sys, "frozen", False)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        have = ".".join(str(n) for n in sys.version_info[:3])
        need = ".".join(str(n) for n in MIN_PYTHON)
        raise SystemExit(
            f"Melodex needs Python {need} or newer, but this is {have}.\n"
            "Download a current version from https://www.python.org/downloads/"
        )


def missing_packages() -> list[str]:
    return [req for mod, req in REQUIRED.items() if importlib.util.find_spec(mod) is None]


def install_packages(requirements: list[str], log=print) -> bool:
    """pip install the given requirements into the running interpreter."""
    if not requirements:
        return True
    log("Installing missing dependencies: " + ", ".join(requirements))
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
           *requirements]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"pip could not be started: {exc}")
        return False
    if proc.returncode != 0:
        log((proc.stderr or proc.stdout or "").strip()[-2000:])
        return False
    log("Dependencies installed.")
    return True


def ensure_packages(log=print) -> None:
    """Install anything missing, then verify. Raises SystemExit if impossible."""
    if frozen():
        return
    check_python()
    missing = missing_packages()
    if not missing:
        return
    if not install_packages(missing, log=log):
        raise SystemExit(
            "Automatic install failed. Run this manually and start the app again:\n\n"
            f"    {sys.executable} -m pip install -r requirements.txt\n"
        )
    still_missing = missing_packages()
    if still_missing:
        raise SystemExit(
            "These packages are still missing after install: " + ", ".join(still_missing)
        )


def upgrade_ytdlp(log=print) -> bool:
    """yt-dlp breaks whenever YouTube changes. Updating fixes most 403s."""
    if frozen():
        log("This is the packaged build. Download a newer release to update yt-dlp.")
        return False
    return install_packages(["yt-dlp"], log=log)


# ------------------------------------------------------------- ffmpeg ------

def ffmpeg_target_dir() -> str:
    """Where we install our own ffmpeg copy (same place core.py looks first)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    return os.path.join(base, "Melodex", "ffmpeg")


def manual_ffmpeg_hint() -> str:
    if sys.platform == "darwin":
        return "Install it with:  brew install ffmpeg"
    if sys.platform.startswith("linux"):
        return ("Install it with your package manager, for example:\n"
                "    sudo apt install ffmpeg      (Debian / Ubuntu)\n"
                "    sudo dnf install ffmpeg      (Fedora)\n"
                "    sudo pacman -S ffmpeg        (Arch)")
    return "Install it with:  winget install Gyan.FFmpeg"


def _no_window() -> dict:
    """Keep console windows from flashing up in the packaged GUI build."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def verify_ffmpeg(bin_dir: str, log=print) -> bool:
    """An installed ffmpeg is only real if it actually runs."""
    exe = os.path.join(bin_dir, "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if not os.path.isfile(exe):
        return False
    try:
        proc = subprocess.run([exe, "-version"], capture_output=True, text=True,
                              timeout=30, **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"ffmpeg was installed but will not run: {exc}")
        return False
    if proc.returncode != 0:
        log("ffmpeg was installed but exited with an error.")
        return False
    return True


def _resolve_github_latest(api_url: str, log=print) -> str | None:
    """Turn a GitHub 'latest release' page into a concrete .zip asset URL."""
    import requests
    api = api_url.replace("https://github.com/", "https://api.github.com/repos/")
    resp = requests.get(api, timeout=30)
    resp.raise_for_status()
    assets = resp.json().get("assets") or []
    # Prefer the small essentials zip; Python's zipfile cannot read the .7z.
    candidates = [a for a in assets
                  if a["name"].endswith(".zip") and "essentials" in a["name"]]
    if not candidates:
        candidates = [a for a in assets if a["name"].endswith(".zip")]
    if not candidates:
        return None
    best = min(candidates, key=lambda a: a.get("size") or 0)
    return best.get("browser_download_url")


def _download(url: str, dest: str, progress=None, log=print) -> bool:
    """Stream a URL to disk using requests, which validates against certifi.

    urllib is deliberately not used here: it trusts the Windows root store,
    and a stale store rejects Let's Encrypt chains with
    "certificate verify failed: certificate has expired".
    """
    import requests
    with requests.get(url, stream=True, timeout=60,
                      headers={"User-Agent": "Melodex"}) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        if total:
            log(f"Downloading ffmpeg ({total / 1024 / 1024:.0f} MB)...")
        else:
            log("Downloading ffmpeg...")
        read = 0
        with open(dest, "wb") as out:
            for chunk in resp.iter_content(262144):
                if not chunk:
                    continue
                out.write(chunk)
                read += len(chunk)
                if progress and total:
                    progress(read * 100 / total)
    # A captive portal or error page saved as .zip would fail later and more
    # confusingly, so check the archive magic now.
    with open(dest, "rb") as fh:
        if fh.read(2) != b"PK":
            log("That mirror returned something that is not a zip archive.")
            return False
    return True


def _unpack(tmp_zip: str, target: str, log=print) -> str | None:
    """Extract the archive and move its bin/ directory into place."""
    staging = target + ".new"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_zip) as archive:
            archive.extractall(staging)
    except (zipfile.BadZipFile, OSError) as exc:
        log(f"The downloaded archive is unusable: {exc}")
        shutil.rmtree(staging, ignore_errors=True)
        return None

    exe = None
    for root, _dirs, files in os.walk(staging):
        if "ffmpeg.exe" in files or "ffmpeg" in files:
            exe = root
            break
    if not exe:
        log("ffmpeg was not found inside the archive.")
        shutil.rmtree(staging, ignore_errors=True)
        return None

    final_bin = os.path.join(target, "bin")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(final_bin, exist_ok=True)
    # Shared builds keep their DLLs beside the exe, so take the whole folder.
    for name in os.listdir(exe):
        shutil.move(os.path.join(exe, name), os.path.join(final_bin, name))
    shutil.rmtree(staging, ignore_errors=True)
    return final_bin


def install_ffmpeg_via_winget(log=print, locate=None) -> str | None:
    """Last resort. winget uses the OS TLS stack and verifies package hashes."""
    if sys.platform != "win32" or not shutil.which("winget"):
        return None
    log("Trying winget...")
    cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements",
           "--disable-interactivity"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200,
                              **_no_window())
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"winget could not run: {exc}")
        return None
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        log("winget failed: " + (detail[-1] if detail else f"exit {proc.returncode}"))
        return None
    log("winget finished. Looking for the installed ffmpeg...")
    found = locate() if locate else None
    if found and verify_ffmpeg(found, log=log):
        return found
    log("winget installed ffmpeg but it could not be located. A restart may help.")
    return None


def install_ffmpeg_windows(progress=None, log=print, locate=None) -> str | None:
    """Install ffmpeg into our data dir, trying each mirror then winget.

    `locate` is core.find_ffmpeg_dir, passed in so this module stays importable
    before the third-party dependencies exist.
    """
    if sys.platform != "win32":
        log("Automatic ffmpeg install is Windows only.\n" + manual_ffmpeg_hint())
        return None

    import tempfile

    target = ffmpeg_target_dir()
    tmp_zip = os.path.join(tempfile.gettempdir(), "melodex-ffmpeg.zip")

    for name, url in FFMPEG_MIRRORS:
        log(f"Source: {name}")
        try:
            if progress:
                progress(0)
            resolved = url
            if url.endswith("/releases/latest"):
                resolved = _resolve_github_latest(url, log=log)
                if not resolved:
                    log("No usable archive on that mirror.")
                    continue
            if not _download(resolved, tmp_zip, progress=progress, log=log):
                continue
            log("Extracting...")
            final_bin = _unpack(tmp_zip, target, log=log)
            if not final_bin:
                continue
            if not verify_ffmpeg(final_bin, log=log):
                continue
            log(f"ffmpeg installed to {final_bin}")
            return final_bin
        except Exception as exc:  # noqa: BLE001 - network, TLS, disk all possible
            log(f"{name} failed: {_explain(exc)}")
            continue
        finally:
            _quiet_remove(tmp_zip)

    installed = install_ffmpeg_via_winget(log=log, locate=locate)
    if installed:
        return installed

    log("Every automatic method failed.\n" + manual_ffmpeg_hint())
    return None


def _explain(exc: Exception) -> str:
    """Plain-language reason, so the log says what to do about it."""
    text = str(exc)
    low = text.lower()
    if "certificate" in low and ("expired" in low or "verify failed" in low):
        return ("the HTTPS certificate could not be verified. Your Windows root "
                "certificates are probably out of date - install Windows Updates.")
    if "503" in text or "502" in text or "504" in text:
        return "that mirror is temporarily down (server error)."
    if "timed out" in low or "timeout" in low:
        return "the connection timed out."
    if "name or service not known" in low or "getaddrinfo" in low or "nodename" in low:
        return "DNS lookup failed - check your internet connection."
    if "no space left" in low or "errno 28" in low:
        return "the disk is full."
    if "permission denied" in low or "errno 13" in low or "winerror 5" in low:
        return "access denied writing to the install folder."
    return text[:200]


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


if __name__ == "__main__":
    ensure_packages()
    print("Python dependencies OK.")
    if shutil.which("ffmpeg"):
        print("ffmpeg found on PATH.")
    else:
        print("ffmpeg not found.")
        print(manual_ffmpeg_hint())
