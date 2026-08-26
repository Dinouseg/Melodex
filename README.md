# WaveQueen Downloader

Desktop music downloader and the companion app to the **WaveQueen** music
player. Paste a link, pick a folder and a format, and the tracks land on disk
tagged, cover art included, ready to play.

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

---

## What it does

- **Many sources.** Tidal, Spotify, Deezer, YouTube, YouTube Music, SoundCloud,
  Bandcamp, Mixcloud, Audiomack, and anything else yt-dlp supports. Unknown
  sites are passed to yt-dlp anyway rather than rejected.
- **Tracks, albums, playlists and mixes.** Paginated properly, so a 900-track
  playlist comes back whole.
- **Your choice of format.** MP3, M4A/AAC, Opus, OGG Vorbis, FLAC, WAV, ALAC,
  or the original stream with no re-encoding at all.
- **Survives blocks.** Every request rotates through six different yt-dlp
  player clients, so one client answering HTTP 403, "sign in to confirm" or
  "requested format is not available" no longer kills the download. Stalled
  streams are detected and re-fetched instead of hanging.
- **Tags and cover art** written into the file, so WaveQueen has something to
  show.
- **Parallel downloads** with a live progress bar, a stop button, and a skip
  for tracks already in the folder.
- **Nothing hardcoded.** Destination folder, format, bitrate and every other
  setting live in a per-user config file.

Tidal, Spotify and Deezer do not hand out audio. For those links the app reads
the track list, then matches each song on YouTube by title **and length**, so
you get the song rather than a one-hour mix that happens to share its name.

---

## Install

### Option 1: the executable (Windows)

Grab `WaveQueenDownloader.exe` from the
[Releases](../../releases) page and run it. No Python needed. On first launch it
offers to download ffmpeg for you.

### Option 2: from source (any OS)

```bash
git clone https://github.com/Dinouseg/wavequeen-downloader.git
cd wavequeen-downloader
python app.py
```

That is the whole procedure. On the first run the app checks its Python
dependencies and installs anything missing with pip. If you would rather do it
yourself:

```bash
pip install -r requirements.txt
python app.py
```

**Requires Python 3.10 or newer.** The dependencies are `yt-dlp`, `requests`
and `mutagen` (mutagen is what writes cover art into FLAC, M4A and Opus).

---

## ffmpeg

ffmpeg does the audio conversion. Every format except **Original** needs it.

- **Windows:** the app downloads a static build (about 40 MB) into its own data
  folder when you click Install. Nothing else on the system is touched. If you
  prefer: `winget install Gyan.FFmpeg`
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg` / `sudo dnf install ffmpeg` /
  `sudo pacman -S ffmpeg`

An ffmpeg already on your `PATH` is found and used automatically.

---

## Formats

| Format | Lossless | Notes |
|---|---|---|
| MP3 | no | Universal. 320 kbps is transparent to most ears. |
| M4A / AAC | no | Beats MP3 at the same size. Native on Apple devices. |
| Opus | no | Best quality per byte. Some older players cannot read it. |
| OGG Vorbis | no | Open format, wide software support. |
| FLAC | yes | Roughly 60% of WAV size. Best archive choice. |
| WAV | yes | Uncompressed PCM 16-bit / 44.1 kHz. No tags. For DJ software. |
| ALAC | yes | Apple Lossless. FLAC for the Apple ecosystem. |
| Original | n/a | Whatever the site serves, untouched. Fastest, needs no ffmpeg. |

Bitrate applies to the lossy formats only, and is greyed out otherwise.

---

## Settings

Behind the **Settings** button:

| Setting | What it does |
|---|---|
| File names | `Artist - Song.mp3` or just `Song.mp3` |
| Parallel downloads | 1 to 8. Higher is faster but gets rate-limited sooner. |
| Cookies | Reuse a browser login when YouTube answers 403 or demands sign-in. Close that browser first, or its cookie database stays locked. |
| Tags / cover art | Write metadata and artwork into the file. |
| Skip existing | Leave tracks already in the folder alone. |
| Update yt-dlp | yt-dlp breaks whenever YouTube changes something. This fixes most 403s. |

Settings live in:

- Windows: `%LOCALAPPDATA%\WaveQueenDownloader\config.json`
- macOS: `~/Library/Application Support/WaveQueenDownloader/config.json`
- Linux: `~/.config/WaveQueenDownloader/config.json`

---

## Troubleshooting

**HTTP 403 on everything.** yt-dlp is out of date. Settings, then Update yt-dlp,
then restart. If it persists, pick your browser under Cookies.

**"Sign in to confirm you're not a bot".** YouTube wants a session. Pick your
browser under Cookies, and close that browser before downloading.

**HTTP 429 / rate limited.** Lower Parallel downloads to 1 or 2 and wait a few
minutes.

**Spotify playlist comes back empty.** Spotify only exposes public playlists
through its embed page. Algorithmic playlists such as Discover Weekly are not
available.

**Wrong song downloaded.** The YouTube match uses track length, which needs the
source to report a duration. If it does not, paste the YouTube link directly.

**ffmpeg errors.** Settings, then ffmpeg setup, and reinstall it.

---

## Building the executable

```bash
python build.py --clean
```

Output lands in `dist/`. Add `--onedir` for a folder build, which starts faster
than the single-file one.

---

## Project layout

```
app.py             Tkinter GUI, download orchestration
core.py            Sources, yt-dlp pipeline, formats, config, ffmpeg discovery
bootstrap.py       First-run dependency and ffmpeg installation
build.py           PyInstaller build script
requirements.txt   Runtime dependencies
test_core.py       Offline checks - python test_core.py
```

---

## Legal

This tool is for downloading music you have the right to download: your own
recordings, public domain works, Creative Commons releases, and material whose
licence permits it. Downloading copyrighted music without permission breaks the
terms of service of every platform listed above, and may be illegal where you
live. You are responsible for how you use it.

---

## Usage of AI

The entire code was written using Claude Code (Opus 5) and then debugged by hand; minor and minor fixes were made by hand. Expect some errors.

---

## License

MIT. See [LICENSE](LICENSE).
