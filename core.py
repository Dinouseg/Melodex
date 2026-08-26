"""Core logic for Wavequen Downloader.

Everything that is not GUI lives here: link parsing for the supported music
services, YouTube resolution, the yt-dlp download pipeline with client
rotation, format profiles, ffmpeg discovery and the persisted user config.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import yt_dlp
from yt_dlp.utils import sanitize_filename

APP_NAME = "Wavequen Downloader"
APP_SLUG = "WavequenDownloader"
APP_VERSION = "1.0.0"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------- paths ----

def resource_path(name: str) -> str:
    """Path to a bundled read-only asset (works inside a PyInstaller exe)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def data_dir() -> str:
    """Per-user writable directory for config and the downloaded ffmpeg."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    path = os.path.join(base, APP_SLUG)
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_PATH = os.path.join(data_dir(), "config.json")


def default_music_dir() -> str:
    for candidate in ("Music", "Hudba", "Downloads"):
        p = os.path.join(os.path.expanduser("~"), candidate)
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~")


DEFAULT_CONFIG = {
    "output_dir": "",
    "audio_format": "mp3",
    "quality": "320",
    "concurrency": 3,
    "embed_metadata": True,
    "embed_thumbnail": True,
    "naming": "artist-title",
    "cookies_browser": "none",
    "skip_existing": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg["output_dir"] = default_music_dir()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            for key in DEFAULT_CONFIG:
                if key in stored:
                    cfg[key] = stored[key]
    except (OSError, json.JSONDecodeError):
        pass
    if not cfg.get("output_dir"):
        cfg["output_dir"] = default_music_dir()
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({k: cfg.get(k, v) for k, v in DEFAULT_CONFIG.items()}, fh, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------- ffmpeg ----

def find_ffmpeg_dir() -> str | None:
    """Locate ffmpeg: our own copy, next to the app, PATH, then known installs."""
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

    local = os.path.join(data_dir(), "ffmpeg", "bin")
    if os.path.isfile(os.path.join(local, exe_name)):
        return local

    beside = resource_path("ffmpeg")
    if os.path.isfile(os.path.join(beside, "bin", exe_name)):
        return os.path.join(beside, "bin")
    if os.path.isfile(os.path.join(beside, exe_name)):
        return beside

    found = shutil.which("ffmpeg")
    if found:
        return os.path.dirname(found)

    if sys.platform == "win32":
        import glob
        patterns = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet",
                         "Packages", "*FFmpeg*", "**", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "ffmpeg", "bin", "ffmpeg.exe"),
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        for pattern in patterns:
            matches = glob.glob(pattern, recursive=True)
            if matches:
                return os.path.dirname(matches[0])
    return None


def ffmpeg_version(ffmpeg_dir: str | None) -> str | None:
    exe = os.path.join(ffmpeg_dir, "ffmpeg") if ffmpeg_dir else "ffmpeg"
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        out = subprocess.run([exe, "-version"], capture_output=True, text=True,
                            timeout=10, **kwargs)
        lines = (out.stdout or "").splitlines()
        return lines[0] if lines else None
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------ http utils ----

def make_session() -> requests.Session:
    """Session that retries transient failures (429 / 5xx) with backoff."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return session


SESSION = make_session()


class SourceError(RuntimeError):
    """Raised when a link cannot be read or downloaded. Message is user-facing."""


class Cancelled(Exception):
    """Raised from a progress hook to abort a download the user stopped.

    It must travel through the retry loop untouched, otherwise pressing Stop
    would look like a failed download and trigger a pointless client rotation.
    """


def _http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
              service: str = "Service", timeout: int = 20) -> requests.Response:
    """GET with specific, actionable messages for the usual failure codes."""
    try:
        resp = SESSION.get(url, params=params, headers=headers, timeout=timeout)
    except requests.Timeout:
        raise SourceError(f"{service} did not respond in time. Check your connection and retry.")
    except requests.ConnectionError:
        raise SourceError(f"Cannot reach {service}. Check your internet connection.")
    except requests.RequestException as exc:
        raise SourceError(f"{service} request failed: {exc}")

    code = resp.status_code
    if code == 200:
        return resp
    if code in (401, 403):
        raise SourceError(
            f"{service} refused the request (HTTP {code}). The content is private, "
            "region-locked, or the service changed its public API."
        )
    if code == 404:
        raise SourceError(f"{service} says this link does not exist (HTTP 404). Check the URL.")
    if code == 429:
        raise SourceError(f"{service} rate-limited us (HTTP 429). Wait a minute and try again.")
    if 500 <= code < 600:
        raise SourceError(f"{service} is having server trouble (HTTP {code}). Try again later.")
    raise SourceError(f"{service} returned HTTP {code}.")


def _json(resp: requests.Response, service: str) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise SourceError(f"{service} returned a response we could not parse.")
    return data if isinstance(data, dict) else {}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# --------------------------------------------------------------- tracks ----

@dataclass
class Track:
    """One song to fetch.

    url set   -> download that link directly (YouTube, SoundCloud, Bandcamp...).
    url empty -> search YouTube using artist + title.
    """
    title: str = ""
    artist: str = ""
    url: str = ""
    duration: int | None = None

    @property
    def query(self) -> str:
        return (f"{self.artist} {self.title}").strip() or self.title or self.url

    @property
    def display(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        return self.title or self.url


@dataclass
class Playlist:
    tracks: list[Track] = field(default_factory=list)
    name: str = ""
    source: str = ""


# ---------------------------------------------------------------- tidal ----

TIDAL_TOKEN = "CzET4vdadNUFQ5JU"  # anonymous web token used by public Tidal clients
TIDAL_API = "https://api.tidal.com/v1"
TIDAL_COUNTRY = "US"


def parse_tidal_url(url: str) -> tuple[str | None, str | None]:
    for kind, pattern in (
        ("playlist", r"/playlist/([0-9a-fA-F-]{8,})"),
        ("album", r"/album/(\d+)"),
        ("track", r"/track/(\d+)"),
        ("mix", r"/mix/([\w-]+)"),
    ):
        m = re.search(pattern, url)
        if m:
            return kind, m.group(1)
    return None, None


def _tidal_get(path: str, params: dict | None = None) -> dict:
    headers = {"x-tidal-token": TIDAL_TOKEN, "User-Agent": "TIDAL_NATIVE_PLAYER/Win/3.1.2"}
    p = {"countryCode": TIDAL_COUNTRY, "limit": 100}
    if params:
        p.update(params)
    return _json(_http_get(f"{TIDAL_API}{path}", params=p, headers=headers, service="Tidal"),
                 "Tidal")


def _tidal_track(item: dict) -> Track:
    artists = [_clean(a.get("name", "")) for a in (item.get("artists") or [])]
    return Track(
        title=_clean(item.get("title", "")),
        artist=", ".join(a for a in artists if a),
        duration=item.get("duration"),
    )


def _tidal_paged(path: str) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        data = _tidal_get(path, {"offset": offset, "limit": 100})
        items = data.get("items") or []
        if not items:
            break
        out.extend(items)
        total = data.get("totalNumberOfItems", len(out))
        offset += len(items)
        if offset >= total:
            break
    return out


def fetch_tidal(url: str) -> Playlist:
    kind, ident = parse_tidal_url(url)
    if not kind:
        raise SourceError("Unrecognized Tidal link. Supported: track, album, playlist, mix.")

    if kind == "track":
        data = _tidal_get(f"/tracks/{ident}")
        if not data.get("title"):
            raise SourceError("Tidal returned no data for this track.")
        return Playlist([_tidal_track(data)], data.get("title", ""), "tidal")

    path = {
        "playlist": f"/playlists/{ident}/items",
        "album": f"/albums/{ident}/tracks",
        "mix": f"/mixes/{ident}/items",
    }[kind]
    tracks = []
    for item in _tidal_paged(path):
        inner = item.get("item") if isinstance(item.get("item"), dict) else item
        if inner.get("title"):
            tracks.append(_tidal_track(inner))
    if not tracks:
        raise SourceError(f"This Tidal {kind} contains no playable tracks.")
    return Playlist(tracks, "", "tidal")


# -------------------------------------------------------------- spotify ----

def parse_spotify_url(url: str) -> tuple[str | None, str | None]:
    m = re.search(
        r"spotify\.com/(?:intl-[a-z]+/)?(?:embed/)?(track|album|playlist)/([A-Za-z0-9]+)", url
    )
    return (m.group(1), m.group(2)) if m else (None, None)


def _spotify_track(node: dict) -> Track | None:
    title = node.get("title") or node.get("name") or ""
    if not title:
        return None
    artists_field = node.get("artists") or node.get("subtitle")
    if isinstance(artists_field, list):
        artist = ", ".join(a.get("name", "") for a in artists_field if isinstance(a, dict))
    else:
        artist = artists_field or ""
    ms = node.get("duration") or node.get("duration_ms")
    seconds = int(ms / 1000) if isinstance(ms, (int, float)) and ms > 1000 else None
    return Track(title=_clean(title), artist=_clean(artist), duration=seconds)


def _spotify_tracks(entity: dict, kind: str) -> list[Track]:
    if kind == "track":
        track = _spotify_track(entity)
        return [track] if track else []

    raw = entity.get("trackList") or entity.get("tracks") or []
    if isinstance(raw, dict):
        raw = raw.get("items") or []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        node = item.get("track") if isinstance(item.get("track"), dict) else item
        track = _spotify_track(node)
        if track:
            out.append(track)
    return out


def fetch_spotify(url: str) -> Playlist:
    kind, ident = parse_spotify_url(url)
    if not kind:
        raise SourceError("Unrecognized Spotify link. Supported: track, album, playlist.")

    resp = _http_get(f"https://open.spotify.com/embed/{kind}/{ident}", service="Spotify")
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>', resp.text, re.DOTALL)
    if not m:
        raise SourceError(
            "Spotify did not return track data. The item may be private, or Spotify "
            "changed its embed page."
        )
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Spotify returned invalid data: {exc}")

    props = (data.get("props") or {}).get("pageProps") or {}
    candidates = [
        ((props.get("state") or {}).get("data") or {}).get("entity"),
        props.get("entity"),
        (props.get("data") or {}).get("entity"),
    ]
    for entity in candidates:
        if isinstance(entity, dict):
            tracks = _spotify_tracks(entity, kind)
            if tracks:
                return Playlist(tracks, _clean(entity.get("name", "")), "spotify")

    raise SourceError(
        "Spotify embed contains no tracks. Private, region-restricted, or an "
        "algorithmic playlist Spotify does not expose publicly."
    )


# --------------------------------------------------------------- deezer ----

def parse_deezer_url(url: str) -> tuple[str | None, str | None]:
    m = re.search(r"deezer\.com/(?:[a-z]{2}/)?(track|album|playlist)/(\d+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _deezer_track(node: dict) -> Track | None:
    title = _clean(node.get("title_short") or node.get("title") or "")
    if not title:
        return None
    artist = _clean((node.get("artist") or {}).get("name", ""))
    return Track(title=title, artist=artist, duration=node.get("duration"))


def fetch_deezer(url: str) -> Playlist:
    kind, ident = parse_deezer_url(url)
    if not kind:
        raise SourceError("Unrecognized Deezer link. Supported: track, album, playlist.")

    if kind == "track":
        data = _json(_http_get(f"https://api.deezer.com/track/{ident}", service="Deezer"),
                     "Deezer")
        if data.get("error"):
            raise SourceError(f"Deezer: {data['error'].get('message', 'track not found')}.")
        track = _deezer_track(data)
        if not track:
            raise SourceError("Deezer returned no data for this track.")
        return Playlist([track], track.title, "deezer")

    tracks: list[Track] = []
    index = 0
    while True:
        data = _json(_http_get(
            f"https://api.deezer.com/{kind}/{ident}/tracks",
            params={"limit": 100, "index": index}, service="Deezer",
        ), "Deezer")
        if data.get("error"):
            raise SourceError(f"Deezer: {data['error'].get('message', 'not found')}.")
        items = data.get("data") or []
        if not items:
            break
        for node in items:
            track = _deezer_track(node)
            if track:
                tracks.append(track)
        index += len(items)
        if not data.get("next"):
            break

    if not tracks:
        raise SourceError(f"This Deezer {kind} contains no playable tracks.")
    return Playlist(tracks, "", "deezer")


# ------------------------------------------------------- yt-dlp clients ----

# yt-dlp talks to YouTube through "player clients". Individual clients get
# throttled or blocked (HTTP 403, "Sign in to confirm"), so we keep several
# sets and move to the next one whenever a request fails in a retryable way.
#
# yt-dlp adds and removes clients between releases, so the list is filtered
# against the clients the installed version actually knows about. Naming a
# client yt-dlp dropped makes it refuse every request.
_PREFERRED_CLIENT_SETS: list[list[str]] = [
    ["web_music", "visionos"],
    ["android", "tv_simply"],
    ["tv", "web_safari"],
    ["ios", "mweb"],
    ["android_vr", "web_embedded"],
    ["web"],
]


def _known_clients() -> set[str]:
    for module, name in (
        ("yt_dlp.extractor.youtube._base", "INNERTUBE_CLIENTS"),
        ("yt_dlp.extractor.youtube", "INNERTUBE_CLIENTS"),
    ):
        try:
            table = getattr(__import__(module, fromlist=[name]), name)
            if table:
                return set(table)
        except (ImportError, AttributeError):
            continue
    return set()  # unknown layout: trust our list rather than emptying it


def _supported_client_sets() -> list[list[str]]:
    known = _known_clients()
    if not known:
        return [list(s) for s in _PREFERRED_CLIENT_SETS]
    sets = []
    for candidate in _PREFERRED_CLIENT_SETS:
        usable = [c for c in candidate if c in known]
        if usable and usable not in sets:
            sets.append(usable)
    return sets or [["web"]]


YT_CLIENT_SETS: list[list[str]] = _supported_client_sets()

COOKIE_BROWSERS = ["none", "chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"]

# These depend on which player client answered, so another client may succeed.
# "requested format is not available" and "no video formats" look fatal but are
# not: several clients simply do not expose audio streams for a given video.
RETRYABLE_MARKERS = (
    "http error 403", "http error 429", "http error 500", "http error 502",
    "http error 503", "forbidden", "too many requests", "sign in to confirm",
    "unable to download", "fragment", "timed out", "timeout",
    "connection reset", "connection aborted", "temporary failure",
    "the read operation", "please try again", "content isn't available",
    "player response", "nsig extraction", "unable to extract",
    "requested format is not available", "no video formats",
    "page needs to be reloaded", "throttled", "failed to extract",
)

# Properties of the video itself. No client rotation will help, so failing
# fast here saves the user five pointless retries.
FATAL_MARKERS = (
    "video unavailable", "private video", "removed by the uploader",
    "copyright", "this video is not available", "members-only",
    "no space left", "is not a valid url",
    "http error 404", "does not exist", "was not found",
)


def _is_retryable(message: str) -> bool:
    low = message.lower()
    if any(marker in low for marker in FATAL_MARKERS):
        return False
    return any(marker in low for marker in RETRYABLE_MARKERS)


def friendly_error(message: str) -> str:
    """Turn a raw yt-dlp message into something a human can act on."""
    low = message.lower()
    missing_module = re.search(r"module (\w+) was not found", message)
    if missing_module:
        return (f"The Python package \"{missing_module.group(1)}\" is missing. "
                "Install it with:  pip install -r requirements.txt")
    if "403" in low or "forbidden" in low:
        return ("Blocked by the server (HTTP 403) on every client. Wait a few minutes, "
                "or pick your browser under Cookies in Settings.")
    if "429" in low or "too many requests" in low:
        return ("Rate-limited (HTTP 429). Lower Parallel downloads in Settings or wait "
                "a few minutes.")
    if "sign in to confirm" in low:
        return ("YouTube wants a signed-in session. Pick your browser under Cookies in "
                "Settings so yt-dlp can reuse your login.")
    if "404" in low:
        return "Not found (HTTP 404). The track was removed or the link is wrong."
    if "video unavailable" in low or "private video" in low:
        return "Unavailable on YouTube (private, deleted or region-locked)."
    if "requested format is not available" in low or "no video formats" in low:
        return ("No downloadable audio stream on any client. The upload may be "
                "age-restricted - try picking your browser under Cookies.")
    if "ffmpeg" in low or "ffprobe" in low:
        return "ffmpeg is missing or broken. Install it from the Setup dialog."
    if "no space left" in low:
        return "Disk full. Free some space and retry."
    if "no such file or directory" in low or "permission denied" in low:
        return f"Cannot write to the destination folder: {message.strip()}"
    return message.strip().replace("ERROR: ", "")


# ------------------------------------------------------- source routing ----

def detect_source(url: str) -> str:
    u = url.lower()
    if "tidal.com" in u:
        return "tidal"
    if "spotify.com" in u or "spotify.link" in u:
        return "spotify"
    if "deezer.com" in u or "dzr.page.link" in u:
        return "deezer"
    if "music.youtube.com" in u or "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "soundcloud.com" in u:
        return "soundcloud"
    if "bandcamp.com" in u:
        return "bandcamp"
    if "mixcloud.com" in u:
        return "mixcloud"
    if "audiomack.com" in u:
        return "audiomack"
    if re.match(r"https?://", u):
        return "generic"
    return "unknown"


SOURCE_LABEL = {
    "tidal": "Tidal",
    "spotify": "Spotify",
    "deezer": "Deezer",
    "youtube": "YouTube / YouTube Music",
    "soundcloud": "SoundCloud",
    "bandcamp": "Bandcamp",
    "mixcloud": "Mixcloud",
    "audiomack": "Audiomack",
    "generic": "Direct link",
    "unknown": "Unknown",
}

# Services we only read metadata from. The audio itself comes from YouTube.
SEARCH_SOURCES = {"tidal", "spotify", "deezer"}


def detect_kind(url: str, source: str) -> str | None:
    if source == "tidal":
        return parse_tidal_url(url)[0]
    if source == "spotify":
        return parse_spotify_url(url)[0]
    if source == "deezer":
        return parse_deezer_url(url)[0]
    if source == "youtube":
        return "playlist" if ("/playlist" in url or re.search(r"[?&]list=", url)) else "track"
    if source in ("soundcloud", "bandcamp"):
        return "playlist" if ("/sets/" in url or "/album/" in url) else "track"
    return None


def _base_opts(cookies_browser: str = "none") -> dict:
    """yt-dlp options shared by every call we make."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 3,
        # YouTube sometimes serves a stream that crawls at a few KB/s instead of
        # refusing outright. This makes yt-dlp give up on it and re-extract
        # rather than sitting on a stalled download forever.
        "throttledratelimit": 51200,
        "http_headers": {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
    }
    if cookies_browser and cookies_browser != "none":
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def _with_client(opts: dict, clients: list[str]) -> dict:
    merged = dict(opts)
    merged["extractor_args"] = {"youtube": {"player_client": list(clients)}}
    return merged


def fetch_direct(url: str, cookies_browser: str = "none") -> Playlist:
    """Read a YouTube / SoundCloud / Bandcamp / any yt-dlp link into tracks."""
    last_error = ""
    for clients in YT_CLIENT_SETS:
        opts = _with_client(_base_opts(cookies_browser), clients)
        opts.update({"skip_download": True, "extract_flat": "in_playlist"})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many exception types
            last_error = str(exc)
            if not _is_retryable(last_error):
                raise SourceError(friendly_error(last_error))
            continue

        if not info:
            last_error = "no data returned"
            continue

        entries = info.get("entries")
        if entries is not None:
            tracks = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                link = entry.get("webpage_url") or entry.get("url")
                if not link:
                    continue
                tracks.append(Track(
                    title=_clean(entry.get("title") or ""),
                    artist=_clean(entry.get("uploader") or entry.get("channel") or ""),
                    url=link,
                    duration=entry.get("duration"),
                ))
            if not tracks:
                raise SourceError("This playlist is empty or fully unavailable.")
            return Playlist(tracks, _clean(info.get("title") or ""), detect_source(url))

        return Playlist([Track(
            title=_clean(info.get("title") or ""),
            artist=_clean(info.get("uploader") or info.get("channel") or ""),
            url=info.get("webpage_url") or url,
            duration=info.get("duration"),
        )], "", detect_source(url))

    raise SourceError(friendly_error(last_error or "Could not read this link."))


def fetch_tracks(url: str, cookies_browser: str = "none") -> Playlist:
    """Resolve any supported link into a Playlist of Tracks."""
    url = url.strip()
    if not url:
        raise SourceError("Enter a link first.")
    if not re.match(r"https?://", url, re.IGNORECASE):
        raise SourceError("That is not a link. Paste a full URL starting with https://")

    source = detect_source(url)
    if source == "tidal":
        return fetch_tidal(url)
    if source == "spotify":
        return fetch_spotify(url)
    if source == "deezer":
        return fetch_deezer(url)
    return fetch_direct(url, cookies_browser)


# -------------------------------------------------------- youtube search ----

def search_youtube(track: Track, cookies_browser: str = "none") -> tuple[str, str]:
    """Find the closest YouTube match for a track. Returns (url, title)."""
    query = track.query
    last_error = ""
    for clients in YT_CLIENT_SETS:
        opts = _with_client(_base_opts(cookies_browser), clients)
        opts.update({"skip_download": True, "noplaylist": True, "extract_flat": "in_playlist"})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch5:{query}", download=False)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if not _is_retryable(last_error):
                break
            continue

        entries = [e for e in ((info or {}).get("entries") or []) if isinstance(e, dict)]
        entries = [e for e in entries if e.get("webpage_url") or e.get("url")]
        if not entries:
            last_error = "no search results"
            continue

        best = _best_match(entries, track)
        return (best.get("webpage_url") or best["url"], best.get("title", ""))

    detail = f" ({friendly_error(last_error)})" if last_error else ""
    raise SourceError(f'No YouTube match for "{query}".{detail}')


def _best_match(entries: list[dict], track: Track) -> dict:
    """Pick the result closest in length. Guards against hour-long mixes."""
    if not track.duration:
        return entries[0]
    scored = []
    for rank, entry in enumerate(entries):
        dur = entry.get("duration")
        if not dur:
            penalty = 60.0  # unknown length: usable, but not preferred
        else:
            penalty = abs(dur - track.duration)
            if penalty > 90:
                penalty += 600  # almost certainly the wrong upload
        scored.append((penalty + rank * 5, rank, entry))
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][2]


# ------------------------------------------------------------- download ----

# label -> (yt-dlp codec, lossless?, file extension)
AUDIO_FORMATS: dict[str, tuple[str, bool, str]] = {
    "mp3":      ("mp3", False, "mp3"),
    "m4a":      ("m4a", False, "m4a"),
    "opus":     ("opus", False, "opus"),
    "vorbis":   ("vorbis", False, "ogg"),
    "flac":     ("flac", True, "flac"),
    "wav":      ("wav", True, "wav"),
    "alac":     ("alac", True, "m4a"),
    "original": ("", False, ""),
}

FORMAT_ORDER = ["mp3", "m4a", "opus", "vorbis", "flac", "wav", "alac", "original"]

FORMAT_LABEL = {
    "mp3": "MP3",
    "m4a": "M4A / AAC",
    "opus": "Opus",
    "vorbis": "OGG Vorbis",
    "flac": "FLAC (lossless)",
    "wav": "WAV (lossless)",
    "alac": "ALAC (lossless)",
    "original": "Original (no re-encode)",
}

FORMAT_HINT = {
    "mp3": "Universal and small. 320 kbps sounds transparent to most ears.",
    "m4a": "AAC. Beats MP3 at the same size, native on Apple devices.",
    "opus": "Best quality per byte. Some older players do not support it.",
    "vorbis": "Open format with wide software support.",
    "flac": "Lossless at roughly 60% of WAV size. Best archive choice.",
    "wav": "Uncompressed PCM. Huge files, no tags. For DJ software.",
    "alac": "Apple Lossless. Same idea as FLAC, for the Apple ecosystem.",
    "original": "Whatever the site serves, untouched. Fastest, needs no ffmpeg.",
}

LOSSY_BITRATES = ["320", "256", "192", "160", "128", "96"]


def format_needs_ffmpeg(fmt: str) -> bool:
    return fmt != "original"


def is_lossless(fmt: str) -> bool:
    return AUDIO_FORMATS.get(fmt, ("", False, ""))[1]


@dataclass
class DownloadOptions:
    audio_format: str = "mp3"
    quality: str = "320"
    embed_metadata: bool = True
    embed_thumbnail: bool = True
    naming: str = "artist-title"   # or "title"
    cookies_browser: str = "none"
    skip_existing: bool = True
    ffmpeg_dir: str | None = None


def target_basename(track: Track, resolved_title: str, naming: str) -> str | None:
    """Filename stem we want, or None to let yt-dlp name it from the site title."""
    title = track.title or resolved_title
    if not title:
        return None
    # YouTube and SoundCloud titles usually already read "Artist - Song", so
    # prefixing the uploader there would produce "Artist - Artist - Song".
    if (naming == "artist-title" and track.artist
            and track.artist.lower() not in title.lower()):
        stem = f"{track.artist} - {title}"
    else:
        stem = title
    return sanitize_filename(stem, restricted=False)[:180].strip() or None


def build_ydl_opts(out_dir: str, options: DownloadOptions, clients: list[str],
                   basename: str | None, progress_hook=None,
                   tags: dict[str, str] | None = None) -> dict:
    codec, lossless, _ext = AUDIO_FORMATS.get(options.audio_format, AUDIO_FORMATS["mp3"])
    opts = _with_client(_base_opts(options.cookies_browser), clients)

    template = (basename + ".%(ext)s") if basename else "%(title)s.%(ext)s"
    opts.update({
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, template),
        "noplaylist": True,
        "windowsfilenames": sys.platform == "win32",
        "trim_file_name": 200,
        "concurrent_fragment_downloads": 4,
        "overwrites": False,
        "continuedl": True,
    })

    postprocessors: list[dict] = []
    pp_args: dict[str, list[str]] = {}
    if codec:
        # yt-dlp reads preferredquality as a bitrate, and ignores it for lossless.
        postprocessors.append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
            "preferredquality": "0" if lossless else options.quality,
        })
        if codec == "wav":
            pp_args["extractaudio"] = ["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2"]

    # WAV carries no tag container, and Vorbis thumbnails are not supported.
    if options.embed_metadata and codec != "wav":
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})
        # Prefer the tags the music service gave us over the YouTube video
        # title, so the library shows "One More Time" and not
        # "Daft Punk - One More Time (Official Video) [4K]".
        overrides = [arg for key, value in (tags or {}).items() if value
                     for arg in ("-metadata", f"{key}={value}")]
        if overrides:
            pp_args["metadata"] = overrides
    if pp_args:
        opts["postprocessor_args"] = pp_args
    if options.embed_thumbnail and codec not in ("wav", "", "vorbis"):
        opts["writethumbnail"] = True
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})

    if postprocessors:
        opts["postprocessors"] = postprocessors
    if options.ffmpeg_dir:
        opts["ffmpeg_location"] = options.ffmpeg_dir
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


def existing_file(out_dir: str, basename: str | None, audio_format: str) -> str | None:
    """Path of an already-downloaded file, if we can predict its name."""
    if not basename:
        return None
    ext = AUDIO_FORMATS.get(audio_format, ("", False, ""))[2]
    if not ext:
        return None
    path = os.path.join(out_dir, f"{basename}.{ext}")
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def download_track(url: str, out_dir: str, options: DownloadOptions,
                   basename: str | None = None, progress_hook=None,
                   on_retry=None, tags: dict[str, str] | None = None) -> None:
    """Download one URL, rotating yt-dlp clients until one of them works.

    Raises SourceError with a human-readable message if every client fails.
    """
    os.makedirs(out_dir, exist_ok=True)
    last_error = ""
    last_index = len(YT_CLIENT_SETS) - 1
    for attempt, clients in enumerate(YT_CLIENT_SETS):
        opts = build_ydl_opts(out_dir, options, clients, basename, progress_hook, tags)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many exception types
            if isinstance(exc.__cause__, Cancelled) or isinstance(exc.__context__, Cancelled):
                raise Cancelled from exc  # yt-dlp wrapped it in a DownloadError
            last_error = str(exc)
            if not _is_retryable(last_error) or attempt == last_index:
                raise SourceError(friendly_error(last_error))
            if on_retry:
                on_retry(clients, YT_CLIENT_SETS[attempt + 1], friendly_error(last_error))
            time.sleep(min(2 ** attempt, 8))
    raise SourceError(friendly_error(last_error or "Download failed."))
