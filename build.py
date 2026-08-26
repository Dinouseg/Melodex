"""Build the standalone WaveQueen Downloader executable.

    python build.py            build a one-file executable
    python build.py --clean    wipe build/ and dist/ first
    python build.py --onedir   build a folder instead (starts faster)

PyInstaller is installed automatically if it is missing. ffmpeg is NOT bundled:
the app downloads it on first use into the per-user data directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "WaveQueenDownloader"


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def ensure_tooling() -> None:
    import importlib.util
    needed = [
        pkg for module, pkg in
        (("PyInstaller", "pyinstaller"), ("yt_dlp", "yt-dlp"), ("requests", "requests"))
        if importlib.util.find_spec(module) is None
    ]
    if needed:
        run([sys.executable, "-m", "pip", "install", "--upgrade", *needed])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the WaveQueen Downloader executable.")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/ first")
    parser.add_argument("--onedir", action="store_true",
                        help="build a folder instead of a single file")
    args = parser.parse_args()

    if args.clean:
        for folder in ("build", "dist"):
            shutil.rmtree(os.path.join(ROOT, folder), ignore_errors=True)
        print("Cleaned build/ and dist/")

    ensure_tooling()

    separator = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir" if args.onedir else "--onefile",
        "--windowed",
        "--name", NAME,
        "--add-data", f"icon.ico{separator}.",
        "--add-data", f"icon.png{separator}.",
        # yt-dlp resolves extractors dynamically, so PyInstaller cannot see them.
        "--collect-submodules", "yt_dlp",
        "--hidden-import", "yt_dlp.extractor",
        # yt-dlp imports mutagen lazily, only when embedding cover art.
        "--collect-submodules", "mutagen",
        "app.py",
    ]
    if os.path.exists(os.path.join(ROOT, "icon.ico")):
        cmd[cmd.index("--windowed") + 1:cmd.index("--windowed") + 1] = ["--icon", "icon.ico"]

    run(cmd)

    built = os.path.join(ROOT, "dist", NAME + (".exe" if sys.platform == "win32" else ""))
    if args.onedir:
        built = os.path.join(ROOT, "dist", NAME)
    print(f"\nDone: {built}")


if __name__ == "__main__":
    main()
