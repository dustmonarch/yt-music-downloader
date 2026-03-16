#!/usr/bin/env python3
"""
YouTube Music Downloader — Flask Backend
Run:  python server.py  →  open http://localhost:5000
"""

import os, sys, json, re, time, subprocess, urllib.request, urllib.parse

# ── Dependency bootstrap ──────────────────────────────────────────────────────
def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])

def ensure_deps():
    for mod, pkg in {"flask":"flask","yt_dlp":"yt-dlp","mutagen":"mutagen","requests":"requests"}.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"  Installing {pkg}...")
            install(pkg)
    try:
        __import__("googleapiclient")
    except ImportError:
        try: install("google-api-python-client")
        except Exception: pass

print("Checking dependencies...")
ensure_deps()
print("  All good.\n")

# ── Imports ───────────────────────────────────────────────────────────────────
import requests
import yt_dlp
from flask import Flask, jsonify, request, send_from_directory, Response
from mutagen.id3 import (ID3, ID3NoHeaderError,
    TIT2, TPE1, TPE2, TALB, TCON, APIC, TRCK, TDRC)

# ── App setup ─────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
SUMMARY_FILE = os.path.join(DOWNLOAD_DIR, "summary.json")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

@app.route("/")
def serve_index():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return Response("", status=204, headers={
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    })

# ── Config ────────────────────────────────────────────────────────────────────
YOUTUBE_API_KEY       = os.environ.get("YOUTUBE_API_KEY", "")
LASTFM_API_KEY        = os.environ.get("LASTFM_API_KEY", "")
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

# ── Summary helpers ───────────────────────────────────────────────────────────

def load_summary() -> list:
    """Load existing download history from summary.json."""
    if not os.path.exists(SUMMARY_FILE):
        return []
    try:
        with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_summary(records: list):
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

def norm(s: str) -> str:
    """Normalise a string for fuzzy matching (lowercase, strip punctuation/spaces)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def build_downloaded_set(summary: list) -> dict:
    """
    Return a dict keyed by video id AND by norm(artist)+norm(song)
    so we can detect duplicates even if the video id differs.
    Value is the summary record.
    """
    index = {}
    for rec in summary:
        if rec.get("status") != "ok":
            continue
        vid_id = rec.get("id", "")
        if vid_id:
            index[vid_id] = rec
        key = norm(rec.get("artist", "")) + "|" + norm(rec.get("song", ""))
        if key != "|":
            index[key] = rec
    return index

# ── Title parsing ─────────────────────────────────────────────────────────────

def parse_title(raw: str, channel: str):
    clean = re.sub(
        r'\s*[\(\[](official\s*(music\s*)?video|lyrics?|audio|hd|4k|mv'
        r'|visuali[sz]er|ft\.?.*?|feat\.?.*?)[\)\]].*',
        '', raw, flags=re.IGNORECASE
    ).strip()
    for sep in (' - ', ' \u2013 ', ' \u2014 '):
        if sep in clean:
            parts = clean.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return channel, clean

# ── yt-dlp entry filtering ────────────────────────────────────────────────────

def is_video_entry(e: dict) -> bool:
    """
    Return True only if this yt-dlp flat-playlist entry is a single video,
    not a playlist, channel, or other non-downloadable type.
    """
    entry_type = e.get("_type", "video")
    ie_key     = e.get("ie_key", "")

    # Explicit playlist/channel types
    if entry_type in ("playlist", "multi_video"):
        return False
    # ie_key hints (YoutubeTab = channel or playlist page)
    if ie_key in ("YoutubeTab", "YoutubePlaylist"):
        return False
    # Must have an id that looks like a YouTube video id (11 chars)
    vid_id = e.get("id", "")
    if not vid_id or len(vid_id) != 11:
        return False
    # Playlists sneak through with url containing /playlist?
    url = e.get("url", "") or e.get("webpage_url", "")
    if "playlist" in url.lower() or "/channel/" in url.lower():
        return False
    return True

def make_entry(e: dict) -> dict:
    vid_id  = e.get("id", "")
    title   = e.get("title") or "Unknown"
    channel = e.get("uploader") or e.get("channel") or e.get("uploader_id") or "Unknown"
    artist, song = parse_title(title, channel)
    return {
        "id":        vid_id,
        "title":     title,
        "channel":   channel,
        "views":     e.get("view_count") or 0,
        "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
        "url":       e.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}",
        "artist":    artist,
        "song":      song,
        "duration":  e.get("duration") or 0,
    }

# ── YouTube helpers ───────────────────────────────────────────────────────────

def yt_search(query: str, count: int) -> list:
    # Request extra results so we still hit `count` after filtering non-videos
    fetch = min(count * 2, 50)
    ydl_opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": "in_playlist", "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info    = ydl.extract_info(f"ytsearch{fetch}:{query}", download=False)
        entries = info.get("entries") or []
        videos  = [make_entry(e) for e in entries if is_video_entry(e)]
    return videos[:count]

def yt_playlist(url: str, count: int) -> list:
    fetch    = min(count * 2, 100)
    ydl_opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": "in_playlist", "playlistend": fetch, "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info    = ydl.extract_info(url, download=False)
        entries = info.get("entries") or []
        videos  = [make_entry(e) for e in entries if is_video_entry(e)]
    return videos[:count]

def yt_api_chart(count: int) -> list:
    from googleapiclient.discovery import build
    yt      = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    results, next_page = [], None
    while len(results) < count:
        resp = yt.videos().list(
            part="snippet,statistics", chart="mostPopular",
            videoCategoryId="10", maxResults=min(50, count - len(results)),
            pageToken=next_page, regionCode="US",
        ).execute()
        for item in resp.get("items", []):
            snip, stats = item["snippet"], item.get("statistics", {})
            vid_id = item["id"]
            artist, song = parse_title(snip.get("title", "Unknown"), snip.get("channelTitle", ""))
            results.append({
                "id": vid_id, "title": snip.get("title", "Unknown"),
                "channel": snip.get("channelTitle", "Unknown"),
                "views": int(stats.get("viewCount", 0)),
                "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "artist": artist, "song": song, "duration": 0,
            })
        next_page = resp.get("nextPageToken")
        if not next_page: break
    return results[:count]

# ── Spotify ───────────────────────────────────────────────────────────────────

_sp_cache: dict = {"token": None, "expires": 0}

def spotify_token():
    if _sp_cache["token"] and time.time() < _sp_cache["expires"]:
        return _sp_cache["token"]
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    r    = requests.post("https://accounts.spotify.com/api/token",
                         data={"grant_type": "client_credentials"},
                         auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET), timeout=10)
    data = r.json()
    _sp_cache.update({"token": data.get("access_token"),
                      "expires": time.time() + data.get("expires_in", 3600) - 60})
    return _sp_cache["token"]

def fetch_spotify_hot(count: int) -> list:
    token = spotify_token()
    if not token:
        raise RuntimeError("Spotify credentials not set. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    hdrs = {"Authorization": f"Bearer {token}"}
    r    = requests.get(
        "https://api.spotify.com/v1/playlists/37i9dQZEVXbMDoHDwVN2tF/tracks",
        headers=hdrs,
        params={"limit": min(count, 50), "fields": "items(track(name,artists,album(name,images,release_date)))"},
        timeout=15,
    )
    results = []
    for item in r.json().get("items", [])[:count]:
        track = item.get("track") or {}
        if not track: continue
        artist     = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        song       = track.get("name", "Unknown")
        album_name = track.get("album", {}).get("name", "")
        images     = track.get("album", {}).get("images", [])
        thumb      = images[0]["url"] if images else ""
        yt         = yt_search(f"{artist} {song} official video", 1)
        if yt:
            yt[0].update({"artist": artist, "song": song, "album_hint": album_name})
            if thumb: yt[0]["thumbnail"] = thumb
            results.append(yt[0])
        time.sleep(0.1)
    return results

# ── Metadata: Last.fm / MusicBrainz ──────────────────────────────────────────

def fetch_metadata(artist: str, song: str, album_hint: str = "") -> dict:
    """
    Fetch album name, album artist, track number, year, genre, and cover art.
    Returns a dict with keys: album, album_artist, track, year, genre, art_url.
    """
    empty = {"album": album_hint, "album_artist": artist,
             "track": "", "year": "", "genre": "", "art_url": ""}

    # ── Last.fm ───────────────────────────────────────────────────────────────
    if LASTFM_API_KEY:
        try:
            url = "http://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode({
                "method": "track.getInfo", "api_key": LASTFM_API_KEY,
                "artist": artist, "track": song, "format": "json", "autocorrect": 1,
            })
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            track_data = data.get("track", {})
            album_data = track_data.get("album", {})
            imgs       = album_data.get("image", [])
            art        = next((i["#text"] for i in reversed(imgs) if i.get("#text")), "")
            tags       = track_data.get("toptags", {}).get("tag", [])
            # track number is in album_data["@attr"]["position"] when available
            position   = album_data.get("@attr", {}).get("position", "")
            return {
                "album":        album_data.get("title", album_hint),
                "album_artist": artist,
                "track":        str(position),
                "year":         "",
                "genre":        tags[0]["name"].title() if tags else "",
                "art_url":      art,
            }
        except Exception:
            pass

    # ── MusicBrainz fallback ──────────────────────────────────────────────────
    try:
        q    = urllib.parse.quote(f'recording:"{song}" AND artist:"{artist}"')
        hdrs = {"User-Agent": "YTMusicDL/2.0"}
        resp = requests.get(
            f"https://musicbrainz.org/ws/2/recording/?query={q}&limit=1&fmt=json",
            headers=hdrs, timeout=10)
        recs = resp.json().get("recordings", [])
        if not recs:
            return empty
        rec  = recs[0]
        rels = rec.get("releases", [])
        art_url, album, year, track_num = "", album_hint, "", ""
        if rels:
            rel   = rels[0]
            rid   = rel["id"]
            album = rel.get("title", album_hint)
            year  = (rel.get("date") or "")[:4]
            # track number from media list
            for medium in rel.get("media", []):
                for t in medium.get("tracks", []):
                    if norm(t.get("title","")) == norm(song):
                        track_num = str(t.get("number", ""))
            # cover art
            ca = requests.head(f"https://coverartarchive.org/release/{rid}/front-500",
                               headers=hdrs, timeout=8, allow_redirects=True)
            if ca.status_code == 200:
                art_url = ca.url
        rg = rec.get("release-group", {})
        rg_tags = rg.get("tags", [])
        # album artist from artist-credit
        ac = rec.get("artist-credit", [])
        album_artist = ac[0].get("artist", {}).get("name", artist) if ac else artist
        return {
            "album":        album,
            "album_artist": album_artist,
            "track":        track_num,
            "year":         year,
            "genre":        rg_tags[0]["name"].title() if rg_tags else "",
            "art_url":      art_url,
        }
    except Exception:
        return empty

# ── Download + tag ────────────────────────────────────────────────────────────

def download_and_tag(video: dict) -> dict:
    safe    = re.sub(r'[\\/*?:"<>|]', "_", video.get("title", "track"))[:80]
    out_tpl = os.path.join(DOWNLOAD_DIR, safe)
    mp3     = out_tpl + ".mp3"

    ydl_opts = {
        "format": "bestaudio/best", "outtmpl": out_tpl + ".%(ext)s",
        "quiet": True, "no_warnings": True,
        "postprocessors": [{"key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video["url"]])

    if not os.path.exists(mp3):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(safe) and f.endswith(".mp3"):
                mp3 = os.path.join(DOWNLOAD_DIR, f)
                break

    album_hint = video.get("album_hint", "")
    meta       = fetch_metadata(video.get("artist", ""), video.get("song", ""), album_hint)
    art_bytes  = None
    if meta.get("art_url"):
        try:
            r = requests.get(meta["art_url"], timeout=15)
            if r.status_code == 200:
                art_bytes = r.content
        except Exception:
            pass

    if art_bytes:
        with open(mp3.replace(".mp3", ".jpg"), "wb") as f:
            f.write(art_bytes)

    # ── Embed ID3 tags ────────────────────────────────────────────────────────
    try:
        try:
            tags = ID3(mp3)
        except ID3NoHeaderError:
            tags = ID3()

        tags.add(TIT2(encoding=3, text=video.get("song", "")))     # Title
        tags.add(TPE1(encoding=3, text=video.get("artist", "")))   # Lead artist
        if meta.get("album"):
            tags.add(TALB(encoding=3, text=meta["album"]))          # Album title
        if meta.get("album_artist"):
            tags.add(TPE2(encoding=3, text=meta["album_artist"]))   # Album artist (key for Apple Music grouping)
        if meta.get("genre"):
            tags.add(TCON(encoding=3, text=meta["genre"]))          # Genre
        if meta.get("track"):
            tags.add(TRCK(encoding=3, text=meta["track"]))          # Track number
        if meta.get("year"):
            tags.add(TDRC(encoding=3, text=meta["year"]))           # Recording year
        if art_bytes:
            tags.add(APIC(encoding=3, mime="image/jpeg",
                          type=3, desc="Cover", data=art_bytes))    # Cover art

        tags.save(mp3, v2_version=3)
    except Exception:
        pass

    return {
        "file":         os.path.basename(mp3),
        "album":        meta.get("album", ""),
        "album_artist": meta.get("album_artist", ""),
        "genre":        meta.get("genre", ""),
        "track":        meta.get("track", ""),
        "year":         meta.get("year", ""),
        "has_art":      art_bytes is not None,
    }

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify({
        "ok": True,
        "youtube_api":  bool(YOUTUBE_API_KEY),
        "lastfm_api":   bool(LASTFM_API_KEY),
        "spotify_api":  bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET),
        "download_dir": DOWNLOAD_DIR,
    })

@app.route("/api/downloaded")
def api_downloaded():
    """
    Return the set of already-downloaded video ids and artist|song keys
    so the frontend can mark them immediately on page load / after search.
    """
    summary = load_summary()
    ids  = [r["id"]   for r in summary if r.get("status") == "ok" and r.get("id")]
    keys = [
        norm(r.get("artist","")) + "|" + norm(r.get("song",""))
        for r in summary
        if r.get("status") == "ok"
        and (r.get("artist") or r.get("song"))
    ]
    return jsonify({"ok": True, "ids": ids, "keys": keys})

@app.route("/api/search", methods=["POST"])
def api_search():
    body  = request.get_json(force=True) or {}
    mode  = body.get("mode", "trending")
    count = max(1, min(int(body.get("count", 10)), 50))
    try:
        if mode == "trending":
            videos = yt_api_chart(count) if YOUTUBE_API_KEY else yt_search("top music hits 2025 official video", count)
        elif mode == "hot100":
            videos = fetch_spotify_hot(count)
        elif mode == "genre":
            videos = yt_search(f"{body.get('genre','pop')} music official video", count)
        elif mode == "new":
            videos = yt_search("new music 2026 official video", count)
        elif mode == "views":
            if YOUTUBE_API_KEY:
                videos = yt_api_chart(count)
                videos.sort(key=lambda v: v.get("views", 0), reverse=True)
            else:
                videos = yt_search("most popular music video 2025", count)
        elif mode == "upandcoming":
            try:
                videos = yt_playlist("https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Ar_3GPy3workfN-m1An8", count)
            except Exception:
                videos = []
            if not videos:
                videos = yt_search("new artist music 2026 breakout official video", count)
        elif mode == "keyword":
            videos = yt_search(body.get("keyword", "music").strip() or "music", count)
        else:
            videos = yt_search("music official video", count)

        return jsonify({"ok": True, "videos": videos})
    except Exception as exc:
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.route("/api/download", methods=["POST"])
def api_download():
    body   = request.get_json(force=True) or {}
    videos = body.get("videos", [])
    if not videos:
        return jsonify({"ok": False, "error": "No videos provided"}), 400

    summary = load_summary()
    results = []
    for video in videos:
        try:
            res = download_and_tag(video)
            record = {
                "id":           video.get("id", ""),
                "title":        video.get("title", ""),
                "artist":       video.get("artist", ""),
                "song":         video.get("song", ""),
                "status":       "ok",
                **res,
            }
            # Update summary: replace existing record for this id or append
            replaced = False
            for i, r in enumerate(summary):
                if r.get("id") == record["id"]:
                    summary[i] = record
                    replaced = True
                    break
            if not replaced:
                summary.append(record)
            results.append({"id": video.get("id"), "status": "ok", **res})
        except Exception as exc:
            import traceback; traceback.print_exc()
            results.append({"id": video.get("id"), "status": "error", "error": str(exc)})

    save_summary(summary)
    return jsonify({"ok": True, "results": results})

@app.route("/downloads/<path:filename>")
def serve_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    w = 52
    print()
    print(f"  {'='*w}")
    print(f"  YT.Music.DL  —  Backend Server")
    print(f"  {'='*w}")
    print(f"  Open  ->  http://localhost:5000")
    print(f"  NOTE: use URL above, not file://")
    print(f"  {'='*w}")
    print(f"  YouTube API  {'[OK]' if YOUTUBE_API_KEY else '[not set — yt-dlp fallback]'}")
    print(f"  Last.fm API  {'[OK]' if LASTFM_API_KEY else '[not set — MusicBrainz fallback]'}")
    print(f"  Spotify API  {'[OK]' if SPOTIFY_CLIENT_ID else '[not set — Hot 100 disabled]'}")
    print(f"  Downloads -> {DOWNLOAD_DIR}")
    print(f"  {'='*w}")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False)
