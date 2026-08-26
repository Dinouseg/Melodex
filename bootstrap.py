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

FFMPEG_WINDOWS_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def frozen() -> bool:
    """True when running from a PyInstaller build."""
    return getattr(sys, "frozen", False)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        have = ".".join(str(n) for n in sys.version_info[:3])
        need = ".".join(str(n) for n in MIN_PYTHON)
        raise SystemExit(
            f"Wavequen Downloader needs Python {need} or newer, but this is {have}.\n"
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
    return os.path.join(base, "WavequenDownloader", "ffmpeg")


def manual_ffmpeg_hint() -> str:
    if sys.platform == "darwin":
        return "Install it with:  brew install ffmpeg"
    if sys.platform.startswith("linux"):
        return ("Install it with your package manager, for example:\n"
                "    sudo apt install ffmpeg      (Debian / Ubuntu)\n"
                "    sudo dnf install ffmpeg      (Fedora)\n"
                "    sudo pacman -S ffmpeg        (Arch)")
    return "Install it with:  winget install Gyan.FFmpeg"


def install_ffmpeg_windows(progress=None, log=print) -> str | None:
    """Download a static ffmpeg build into our data dir. Returns the bin path."""
    if sys.platform != "win32":
        log("Automatic ffmpeg install is Windows only.\n" + manual_ffmpeg_hint())
        return None

    import tempfile
    import urllib.request

    target = ffmpeg_target_dir()
    tmp_zip = os.path.join(tempfile.gettempdir(), "wavequen-ffmpeg.zip")

    log("Downloading ffmpeg (about 40 MB)...")
    try:
        request = urllib.request.Request(
            FFMPEG_WINDOWS_URL, headers={"User-Agent": "WavequenDownloader"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            read = 0
            with open(tmp_zip, "wb") as out:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    out.write(chunk)
                    read += len(chunk)
                    if progress and total:
                        progress(read * 100 / total)
    except Exception as exc:  # noqa: BLE001 - network, DNS, TLS, disk all possible
        log(f"Download failed: {exc}\n{manual_ffmpeg_hint()}")
        _quiet_remove(tmp_zip)
        return None

    log("Extracting...")
    staging = target + ".new"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp_zip) as archive:
            archive.extractall(staging)
    except (zipfile.BadZipFile, OSError) as exc:
        log(f"The downloaded archive is unusable: {exc}")
        shutil.rmtree(staging, ignore_errors=True)
        _quiet_remove(tmp_zip)
        return None
    finally:
        _quiet_remove(tmp_zip)

    # The archive holds one top-level ffmpeg-*-essentials_build folder.
    exe = None
    for root, _dirs, files in os.walk(staging):
        if "ffmpeg.exe" in files:
            exe = os.path.join(root, "ffmpeg.exe")
            break
    if not exe:
        log("ffmpeg.exe was not found inside the archive.")
        shutil.rmtree(staging, ignore_errors=True)
        return None

    bin_dir = os.path.dirname(exe)
    final_bin = os.path.join(target, "bin")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(final_bin, exist_ok=True)
    for name in os.listdir(bin_dir):
        shutil.move(os.path.join(bin_dir, name), os.path.join(final_bin, name))
    shutil.rmtree(staging, ignore_errors=True)

    log(f"ffmpeg installed to {final_bin}")
    return final_bin


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
