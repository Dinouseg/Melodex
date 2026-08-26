"""Build the standalone Melodex executable.

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
NAME = "Melodex"


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


def app_version() -> str:
    """Read APP_VERSION out of core.py without importing its dependencies."""
    import re
    source = open(os.path.join(ROOT, "core.py"), encoding="utf-8").read()
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', source)
    return match.group(1) if match else "0.0.0"


def write_version_resource(path: str, version: str) -> None:
    """Windows version metadata.

    An unsigned executable carrying no company, product or version fields
    scores worse with SmartScreen and antivirus heuristics than one that
    identifies itself, so this is cheap insurance against false positives.
    """
    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    numbers = ", ".join(str(int(p) if p.isdigit() else 0) for p in parts)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}), prodvers=({numbers}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Dinouseg'),
      StringStruct('FileDescription', 'Melodex - music library tool'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'MIT License. Copyright (c) 2026 Dinouseg'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', 'Melodex'),
      StringStruct('ProductVersion', '{version}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Melodex executable.")
    parser.add_argument("--clean", action="store_true", help="remove build/ and dist/ first")
    parser.add_argument("--onedir", action="store_true",
                        help="build a folder instead of a single file")
    args = parser.parse_args()

    if args.clean:
        for folder in ("build", "dist"):
            shutil.rmtree(os.path.join(ROOT, folder), ignore_errors=True)
        print("Cleaned build/ and dist/")

    ensure_tooling()

    version = app_version()
    version_file = os.path.join(ROOT, "build", "version_info.txt")
    os.makedirs(os.path.dirname(version_file), exist_ok=True)
    write_version_resource(version_file, version)
    print(f"Building {NAME} {version}")

    separator = ";" if sys.platform == "win32" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir" if args.onedir else "--onefile",
        "--windowed",
        # UPX-compressed binaries are flagged by antivirus far more often,
        # and save little here. Never let PyInstaller pick it up from PATH.
        "--noupx",
        "--version-file", version_file,
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
