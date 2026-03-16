#!/usr/bin/env python3
"""
YouTube Music Downloader — Flask Backend
Serves index.html AND all API routes from the same origin (localhost:5000),
which completely eliminates any CORS issues.

Run:   python server.py
Then open:  http://localhost:5000
         (do NOT open index.html directly as a file://)
"""

import os, sys, json, re, time, subprocess, urllib.request, urllib.parse

# ── Dependency bootstrap ──────────────────────────────────────────────────────
def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])

def ensure_deps():
    deps = {
        "flask":   "flask",
        "yt_dlp":  "yt-dlp",
        "mutagen": "mutagen",
        "requests":"requests",
    }
    for mod, pkg in deps.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"  Installing {pkg}...")
            install(pkg)
    try:
        __import__("googleapiclient")
    except ImportError:
        try:
            install("google-api-python-client")
        except Exception:
            pass  # optional; only needed if YOUTUBE_API_KEY is set

print("Checking dependencies...")
ensure_deps()
print("  All good.\n")

# ── Imports ───────────────────────────────────────────────────────────────────
import requests
import yt_dlp
from flask import Flask, jsonify, request, send_from_directory, Response
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TCON, APIC

# ── App setup ─────────────────────────────────────────────────────────────────
# Serve static files (index.html) from the same directory as this script.
# Browser accesses everything via http://localhost:5000 — no CORS needed.
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

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

# ── Helpers ───────────────────────────────────────────────────────────────────

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

def make_entry(e: dict) -> dict:
    vid_id  = e.get("id") or ""
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
    ydl_opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": "in_playlist", "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
        return [make_entry(e) for e in (info.get("entries") or [])[:count]]

def yt_playlist(url: str, count: int) -> list:
    ydl_opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": "in_playlist", "playlistend": count, "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return [make_entry(e) for e in (info.get("entries") or [])[:count]]

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
        if not next_page:
            break
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
    _sp_cache["token"]   = data.get("access_token")
    _sp_cache["expires"] = time.time() + data.get("expires_in", 3600) - 60
    return _sp_cache["token"]

def fetch_spotify_hot(count: int) -> list:
    token = spotify_token()
    if not token:
        raise RuntimeError("Spotify credentials not set. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    hdrs = {"Authorization": f"Bearer {token}"}
    r    = requests.get(
        "https://api.spotify.com/v1/playlists/37i9dQZEVXbMDoHDwVN2tF/tracks",
        headers=hdrs,
        params={"limit": min(count, 50), "fields": "items(track(name,artists,album(images)))"},
        timeout=15,
    )
    results = []
    for item in r.json().get("items", [])[:count]:
        track = item.get("track") or {}
        if not track:
            continue
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        song   = track.get("name", "Unknown")
        images = track.get("album", {}).get("images", [])
        thumb  = images[0]["url"] if images else ""
        yt     = yt_search(f"{artist} {song} official video", 1)
        if yt:
            yt[0].update({"artist": artist, "song": song})
            if thumb:
                yt[0]["thumbnail"] = thumb
            results.append(yt[0])
        time.sleep(0.1)
    return results

# ── Metadata ──────────────────────────────────────────────────────────────────

def fetch_metadata(artist: str, song: str) -> dict:
    empty = {"album": "", "art_url": "", "genre": ""}
    if LASTFM_API_KEY:
        try:
            url = "http://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode({
                "method": "track.getInfo", "api_key": LASTFM_API_KEY,
                "artist": artist, "track": song, "format": "json", "autocorrect": 1,
            })
            with urllib.request.urlopen(url, timeout=10) as resp:
                data  = json.loads(resp.read())
            track = data.get("track", {})
            album = track.get("album", {})
            imgs  = album.get("image", [])
            art   = next((i["#text"] for i in reversed(imgs) if i.get("#text")), "")
            tags  = track.get("toptags", {}).get("tag", [])
            return {"album": album.get("title", ""), "art_url": art,
                    "genre": tags[0]["name"].title() if tags else ""}
        except Exception:
            pass
    try:
        q    = urllib.parse.quote(f'recording:"{song}" AND artist:"{artist}"')
        hdrs = {"User-Agent": "YTMusicDL/2.0"}
        resp = requests.get(f"https://musicbrainz.org/ws/2/recording/?query={q}&limit=1&fmt=json",
                            headers=hdrs, timeout=10)
        recs = resp.json().get("recordings", [])
        if not recs:
            return empty
        rec  = recs[0]
        rels = rec.get("releases", [])
        art_url, album = "", ""
        if rels:
            rid   = rels[0]["id"]
            album = rels[0].get("title", "")
            ca    = requests.head(f"https://coverartarchive.org/release/{rid}/front-500",
                                  headers=hdrs, timeout=8, allow_redirects=True)
            if ca.status_code == 200:
                art_url = ca.url
        rg_tags = rec.get("release-group", {}).get("tags", [])
        return {"album": album, "art_url": art_url,
                "genre": rg_tags[0]["name"].title() if rg_tags else ""}
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
    meta      = fetch_metadata(video.get("artist", ""), video.get("song", ""))
    art_bytes = None
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
    try:
        try:
            tags = ID3(mp3)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=video.get("song", "")))
        tags.add(TPE1(encoding=3, text=video.get("artist", "")))
        if meta.get("album"):
            tags.add(TALB(encoding=3, text=meta["album"]))
        if meta.get("genre"):
            tags.add(TCON(encoding=3, text=meta["genre"]))
        if art_bytes:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art_bytes))
        tags.save(mp3, v2_version=3)
    except Exception:
        pass
    return {"file": os.path.basename(mp3), "album": meta.get("album", ""),
            "genre": meta.get("genre", ""), "has_art": art_bytes is not None}

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    return jsonify({"ok": True, "youtube_api": bool(YOUTUBE_API_KEY),
                    "lastfm_api": bool(LASTFM_API_KEY),
                    "spotify_api": bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET),
                    "download_dir": DOWNLOAD_DIR})

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
    results = []
    for video in videos:
        try:
            res = download_and_tag(video)
            results.append({"id": video.get("id"), "status": "ok", **res})
        except Exception as exc:
            import traceback; traceback.print_exc()
            results.append({"id": video.get("id"), "status": "error", "error": str(exc)})
    return jsonify({"ok": True, "results": results})

@app.route("/downloads/<path:filename>")
def serve_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    w = 52
    print()
    print(f"  {'':=<{w}}")
    print(f"  YT.Music.DL  —  Backend Server")
    print(f"  {'':=<{w}}")
    print(f"  Open  ->  http://localhost:5000")
    print(f"  NOTE: open via URL above, not as file://")
    print(f"  {'':=<{w}}")
    print(f"  YouTube API  {'[OK]' if YOUTUBE_API_KEY else '[not set — yt-dlp fallback]'}")
    print(f"  Last.fm API  {'[OK]' if LASTFM_API_KEY else '[not set — MusicBrainz fallback]'}")
    print(f"  Spotify API  {'[OK]' if SPOTIFY_CLIENT_ID else '[not set — Hot 100 disabled]'}")
    print(f"  Downloads -> {DOWNLOAD_DIR}")
    print(f"  {'':=<{w}}")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False)
