"""Offline checks for the parsing, matching and option-building logic.

No network, no ffmpeg, no GUI. Run with:  python test_core.py
"""

import os
import sys
import tempfile

import core


def test_source_detection():
    cases = {
        "https://tidal.com/browse/playlist/abcd1234-ef56": "tidal",
        "https://open.spotify.com/intl-cs/album/4aawyAB9vmqN3uQ7FjRGTy": "spotify",
        "https://www.deezer.com/cs/playlist/1234567": "deezer",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ": "youtube",
        "https://youtu.be/dQw4w9WgXcQ": "youtube",
        "https://soundcloud.com/artist/sets/album": "soundcloud",
        "https://artist.bandcamp.com/album/thing": "bandcamp",
        "https://example.com/song": "generic",
        "not a link at all": "unknown",
    }
    for url, expected in cases.items():
        got = core.detect_source(url)
        assert got == expected, f"{url}: expected {expected}, got {got}"


def test_url_parsing():
    assert core.parse_tidal_url("https://tidal.com/track/12345") == ("track", "12345")
    assert core.parse_tidal_url("https://tidal.com/album/999") == ("album", "999")
    assert core.parse_tidal_url("https://tidal.com/nope") == (None, None)

    assert core.parse_spotify_url(
        "https://open.spotify.com/playlist/37i9dQZF1DX") == ("playlist", "37i9dQZF1DX")
    assert core.parse_spotify_url(
        "https://open.spotify.com/intl-de/track/abc123") == ("track", "abc123")

    assert core.parse_deezer_url("https://www.deezer.com/cs/album/77") == ("album", "77")
    assert core.parse_deezer_url("https://deezer.com/track/5") == ("track", "5")


def test_kind_detection():
    assert core.detect_kind("https://youtube.com/watch?v=x&list=PL1", "youtube") == "playlist"
    assert core.detect_kind("https://youtube.com/watch?v=x", "youtube") == "track"
    assert core.detect_kind("https://soundcloud.com/a/sets/b", "soundcloud") == "playlist"


def test_retry_classification():
    retryable = [
        "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        "HTTP Error 429: Too Many Requests",
        "Sign in to confirm you're not a bot",
        "The read operation timed out",
        # Client-specific: another player client usually serves these fine.
        "ERROR: [youtube] abc: Requested format is not available",
        "ERROR: [youtube] abc: The page needs to be reloaded",
        "ERROR: [youtube] abc: No video formats found!",
    ]
    for message in retryable:
        assert core._is_retryable(message), f"should retry: {message}"

    fatal = [
        "ERROR: Video unavailable",
        "ERROR: Private video. Sign in if you've been granted access",
        "No space left on device",
        # A dead link stays dead on every client - do not burn five retries.
        "ERROR: [Bandcamp:album] x: Unable to download webpage: HTTP Error 404: Not Found",
        "ERROR: [youtube:tab] PL123: YouTube said: The playlist does not exist.",
    ]
    for message in fatal:
        assert not core._is_retryable(message), f"should NOT retry: {message}"


def test_stall_guard_is_set():
    """A throttled stream must abort and re-extract, not hang forever."""
    opts = core.build_ydl_opts(
        tempfile.gettempdir(), core.DownloadOptions(), core.YT_CLIENT_SETS[0], "x")
    assert opts.get("throttledratelimit", 0) > 0


def test_friendly_errors():
    assert "403" in core.friendly_error("HTTP Error 403: Forbidden")
    assert "Cookies" in core.friendly_error("Sign in to confirm you're not a bot")
    assert "Rate-limited" in core.friendly_error("HTTP Error 429: Too Many Requests")
    assert "404" in core.friendly_error("HTTP Error 404: Not Found")

    # A missing Python package must not be reported as a dead link.
    missing = core.friendly_error(
        "ERROR: Postprocessing: module mutagen was not found. "
        "Please install using `python3 -m pip install mutagen`")
    assert "mutagen" in missing and "404" not in missing


def test_mutagen_available():
    """yt-dlp needs mutagen to embed cover art into FLAC / M4A / Opus."""
    import importlib.util
    assert importlib.util.find_spec("mutagen") is not None, \
        "mutagen missing - run: pip install -r requirements.txt"


def test_duration_matching():
    """The 3-minute song must win over a same-titled 1-hour mix."""
    track = core.Track(title="Song", artist="Artist", duration=180)
    entries = [
        {"url": "https://y/mix", "title": "Song [1 HOUR]", "duration": 3600},
        {"url": "https://y/right", "title": "Artist - Song", "duration": 182},
        {"url": "https://y/live", "title": "Song (live)", "duration": 240},
    ]
    assert core._best_match(entries, track)["url"] == "https://y/right"

    # With no duration known, keep YouTube's own ranking.
    assert core._best_match(entries, core.Track(title="Song"))["url"] == "https://y/mix"


def test_basename():
    track = core.Track(title="Song: Part 2", artist="A/B")
    name = core.target_basename(track, "", "artist-title")
    assert name and not set(name) & set('\\/:*?"<>|'), name
    assert core.target_basename(track, "", "title").startswith("Song")
    assert core.target_basename(core.Track(), "Fallback", "artist-title") == "Fallback"
    assert core.target_basename(core.Track(), "", "artist-title") is None

    # Metadata sources give artist and title apart, so they get joined.
    tidal = core.Track(title="Never Gonna Give You Up", artist="Rick Astley")
    assert core.target_basename(tidal, "", "artist-title") == \
        "Rick Astley - Never Gonna Give You Up"

    # A YouTube title already contains the uploader - do not repeat it.
    youtube = core.Track(title="Rick Astley - Never Gonna Give You Up (Video)",
                         artist="Rick Astley")
    assert core.target_basename(youtube, "", "artist-title") == \
        "Rick Astley - Never Gonna Give You Up (Video)"

    # Case differences must not slip a duplicate through either.
    mixed = core.Track(title="RICK ASTLEY - Together Forever", artist="Rick Astley")
    assert core.target_basename(mixed, "", "artist-title").lower().count("astley") == 1


def test_format_table():
    assert set(core.FORMAT_ORDER) == set(core.AUDIO_FORMATS)
    for fmt in core.FORMAT_ORDER:
        assert fmt in core.FORMAT_LABEL and fmt in core.FORMAT_HINT
    assert core.is_lossless("flac") and core.is_lossless("wav")
    assert not core.is_lossless("mp3")
    assert not core.format_needs_ffmpeg("original")
    assert core.format_needs_ffmpeg("mp3")


def test_ydl_options():
    out = tempfile.gettempdir()
    clients = core.YT_CLIENT_SETS[0]

    mp3 = core.build_ydl_opts(
        out, core.DownloadOptions(audio_format="mp3", quality="192"), clients, "Song")
    extract = mp3["postprocessors"][0]
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == "192"
    assert mp3["extractor_args"]["youtube"]["player_client"] == clients
    assert mp3["outtmpl"].endswith("Song.%(ext)s")

    # Lossless ignores the bitrate box.
    flac = core.build_ydl_opts(
        out, core.DownloadOptions(audio_format="flac", quality="128"), clients, None)
    assert flac["postprocessors"][0]["preferredquality"] == "0"
    assert flac["outtmpl"].endswith("%(title)s.%(ext)s")

    # WAV holds no tags and no artwork.
    wav = core.build_ydl_opts(out, core.DownloadOptions(audio_format="wav"), clients, "S")
    keys = [pp["key"] for pp in wav["postprocessors"]]
    assert "FFmpegMetadata" not in keys and "EmbedThumbnail" not in keys
    assert "pcm_s16le" in wav["postprocessor_args"]["extractaudio"]
    # WAV must not lose its PCM args to the tag overrides.
    wav_tagged = core.build_ydl_opts(out, core.DownloadOptions(audio_format="wav"),
                                     clients, "S", tags={"title": "T"})
    assert "pcm_s16le" in wav_tagged["postprocessor_args"]["extractaudio"]
    assert "metadata" not in wav_tagged["postprocessor_args"]

    # Clean service tags beat the YouTube video title.
    tagged = core.build_ydl_opts(out, core.DownloadOptions(audio_format="mp3"), clients,
                                 "S", tags={"title": "One More Time", "artist": "Daft Punk"})
    args = tagged["postprocessor_args"]["metadata"]
    assert "title=One More Time" in args and "artist=Daft Punk" in args
    # Blank fields must not produce empty tags.
    blank = core.build_ydl_opts(out, core.DownloadOptions(audio_format="mp3"), clients,
                                "S", tags={"title": "X", "artist": ""})
    assert blank["postprocessor_args"]["metadata"] == ["-metadata", "title=X"]

    # Original does not re-encode at all.
    original = core.build_ydl_opts(
        out, core.DownloadOptions(audio_format="original", embed_thumbnail=False,
                                  embed_metadata=False),
        clients, "S")
    assert "postprocessors" not in original

    # Cookies only appear when a browser was chosen.
    assert "cookiesfrombrowser" not in mp3
    with_cookies = core.build_ydl_opts(
        out, core.DownloadOptions(cookies_browser="firefox"), clients, "S")
    assert with_cookies["cookiesfrombrowser"] == ("firefox",)


def test_client_rotation_is_distinct():
    assert len(core.YT_CLIENT_SETS) >= 3
    seen = [tuple(s) for s in core.YT_CLIENT_SETS]
    assert len(seen) == len(set(seen)), "duplicate client sets waste a retry"


def test_clients_exist_in_installed_ytdlp():
    """Naming a client this yt-dlp release dropped breaks every request."""
    known = core._known_clients()
    if not known:
        return  # yt-dlp changed its layout; the filter falls back on purpose
    used = {client for group in core.YT_CLIENT_SETS for client in group}
    unknown = used - known
    assert not unknown, f"yt-dlp does not know these clients: {sorted(unknown)}"
    assert all(group for group in core.YT_CLIENT_SETS), "empty client set"


def test_existing_file(tmp=None):
    with tempfile.TemporaryDirectory() as folder:
        assert core.existing_file(folder, "Song", "mp3") is None
        path = os.path.join(folder, "Song.mp3")
        with open(path, "wb") as fh:
            fh.write(b"audio")
        assert core.existing_file(folder, "Song", "mp3") == path
        # Empty files are treated as failed downloads, not as done.
        open(os.path.join(folder, "Empty.mp3"), "wb").close()
        assert core.existing_file(folder, "Empty", "mp3") is None
        # An unpredictable name cannot be checked.
        assert core.existing_file(folder, None, "mp3") is None
        assert core.existing_file(folder, "Song", "original") is None


def test_config_roundtrip():
    cfg = core.load_config()
    for key in core.DEFAULT_CONFIG:
        assert key in cfg, f"missing config key: {key}"
    assert os.path.isdir(os.path.dirname(core.CONFIG_PATH))


def test_bad_input_rejected():
    for bad in ("", "   ", "hello world", "ftp://x/y"):
        try:
            core.fetch_tracks(bad)
        except core.SourceError:
            continue
        raise AssertionError(f"should have been rejected: {bad!r}")


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ok    {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - this is the test runner
            failed += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
