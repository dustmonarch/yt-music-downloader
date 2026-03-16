#!/usr/bin/env python3
"""
YouTube Music Downloader — Flask Backend
Serves the React frontend and handles all download/metadata logic.

Run:  python server.py
Then open:  http://localhost:5000
"""

import os, sys, json, re, time, subprocess, urllib.request, urllib.parse

# ── Dependency bootstrap ──────────────────────────────────────────────────────
def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])

def ensure_deps():
    deps = {
        "flask":           "flask",
        "flask_cors":      "flask-cors",
        "yt_dlp":          "yt-dlp",
        "mutagen":         "mutagen",
        "requests":        "requests",
        "googleapiclient": "google-api-python-client",
    }
    for mod, pkg in deps.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"Installing {pkg}...")
            install(pkg)

print("Checking dependencies...")
ensure_deps()
print("All good.\n")

# ── Imports ───────────────────────────────────────────────────────────────────
import requests
import yt_dlp
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TCON, APIC

app = Flask(__name__, static_folder="frontend/build", static_url_path="/")
CORS(app)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
LASTFM_API_KEY  = os.environ.get("LASTFM_API_KEY", "")
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_title(raw: str, channel: str):
    clean = re.sub(
        r'\s*[\(\[](official\s*(music\s*)?video|lyrics?|audio|hd|4k|ft\.?.*?)[\)\]].*',
        '', raw, flags=re.IGNORECASE
    ).strip()
    for sep in (' - ', ' – ', ' — '):
        if sep in clean:
            parts = clean.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return channel, clean

# ── YouTube helpers ───────────────────────────────────────────────────────────

def yt_search_keyword(query: str, count: int) -> list:
    ydl_opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
        for e in (info.get("entries") or [])[:count]:
            vid_id = e.get("id","")
            artist, song = parse_title(e.get("title","Unknown"), e.get("uploader",""))
            results.append({
                "id":        vid_id,
                "title":     e.get("title","Unknown"),
                "channel":   e.get("uploader") or e.get("channel","Unknown"),
                "views":     e.get("view_count") or 0,
                "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                "url":       f"https://www.youtube.com/watch?v={vid_id}",
                "artist":    artist,
                "song":      song,
                "duration":  e.get("duration") or 0,
            })
    return results

def yt_playlist(playlist_url: str, count: int) -> list:
    ydl_opts = {"quiet": True, "extract_flat": True, "playlist_end": count, "skip_download": True}
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
        for e in (info.get("entries") or [])[:count]:
            vid_id = e.get("id","")
            artist, song = parse_title(e.get("title","Unknown"), e.get("uploader") or e.get("channel",""))
            results.append({
                "id":        vid_id,
                "title":     e.get("title","Unknown"),
                "channel":   e.get("uploader") or e.get("channel","Unknown"),
                "views":     e.get("view_count") or 0,
                "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                "url":       f"https://www.youtube.com/watch?v={vid_id}",
                "artist":    artist,
                "song":      song,
                "duration":  e.get("duration") or 0,
            })
    return results

def yt_api_chart(count: int) -> list:
    from googleapiclient.discovery import build
    yt = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    results, next_page = [], None
    while len(results) < count:
        req = yt.videos().list(
            part="snippet,statistics,contentDetails",
            chart="mostPopular",
            videoCategoryId="10",
            maxResults=min(50, count - len(results)),
            pageToken=next_page,
            regionCode="US",
        )
        resp = req.execute()
        for item in resp.get("items", []):
            snip  = item["snippet"]
            stats = item.get("statistics", {})
            vid_id = item["id"]
            artist, song = parse_title(snip.get("title","Unknown"), snip.get("channelTitle",""))
            results.append({
                "id":        vid_id,
                "title":     snip.get("title","Unknown"),
                "channel":   snip.get("channelTitle","Unknown"),
                "views":     int(stats.get("viewCount",0)),
                "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                "url":       f"https://www.youtube.com/watch?v={vid_id}",
                "artist":    artist,
                "song":      song,
                "duration":  0,
            })
        next_page = resp.get("nextPageToken")
        if not next_page: break
    return results[:count]

# ── Spotify Hot 100 ───────────────────────────────────────────────────────────

_spotify_token_cache = {"token": None, "expires": 0}

def spotify_token():
    if _spotify_token_cache["token"] and time.time() < _spotify_token_cache["expires"]:
        return _spotify_token_cache["token"]
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=10,
    )
    data = resp.json()
    token = data.get("access_token")
    _spotify_token_cache["token"]   = token
    _spotify_token_cache["expires"] = time.time() + data.get("expires_in", 3600) - 60
    return token

def fetch_spotify_hot100(count: int) -> list:
    token = spotify_token()
    if not token:
        raise RuntimeError("Spotify credentials not configured. Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    headers = {"Authorization": f"Bearer {token}"}
    # Global Top 50 playlist
    playlist_id = "37i9dQZEVXbMDoHDwVN2tF"
    resp = requests.get(
        f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks",
        headers=headers,
        params={"limit": min(count, 50), "fields": "items(track(name,artists,album(images)))"},
        timeout=15,
    )
    items = resp.json().get("items", [])
    results = []
    for item in items[:count]:
        track = item.get("track") or {}
        if not track: continue
        artist = track["artists"][0]["name"] if track.get("artists") else "Unknown"
        song   = track.get("name", "Unknown")
        images = track.get("album", {}).get("images", [])
        thumb  = images[0]["url"] if images else ""
        # Search YouTube for this track
        yt_results = yt_search_keyword(f"{artist} {song} official video", 1)
        if yt_results:
            vid = yt_results[0]
            vid["artist"]    = artist
            vid["song"]      = song
            vid["thumbnail"] = thumb or vid["thumbnail"]
            results.append(vid)
        time.sleep(0.15)
    return results

# ── Metadata: Last.fm / MusicBrainz ──────────────────────────────────────────

def fetch_metadata(artist: str, song: str) -> dict:
    if LASTFM_API_KEY:
        try:
            params = {
                "method": "track.getInfo",
                "api_key": LASTFM_API_KEY,
                "artist": artist, "track": song,
                "format": "json", "autocorrect": 1,
            }
            url = "http://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            track = data.get("track", {})
            album = track.get("album", {})
            images = album.get("image", [])
            art_url = next((i["#text"] for i in reversed(images) if i.get("#text")), "")
            tags = track.get("toptags", {}).get("tag", [])
            genre = tags[0]["name"].title() if tags else ""
            return {"album": album.get("title",""), "art_url": art_url, "genre": genre}
        except Exception:
            pass
    # MusicBrainz fallback
    try:
        headers = {"User-Agent": "YTMusicDownloader/2.0"}
        q = urllib.parse.quote(f'recording:"{song}" AND artist:"{artist}"')
        resp = requests.get(
            f"https://musicbrainz.org/ws/2/recording/?query={q}&limit=1&fmt=json",
            headers=headers, timeout=10
        )
        data = resp.json()
        recs = data.get("recordings", [])
        if not recs:
            return {"album": "", "art_url": "", "genre": ""}
        rec = recs[0]
        releases = rec.get("releases", [])
        art_url, album = "", ""
        if releases:
            release_id = releases[0]["id"]
            album = releases[0].get("title","")
            ca = requests.head(
                f"https://coverartarchive.org/release/{release_id}/front-500",
                headers=headers, timeout=8, allow_redirects=True
            )
            if ca.status_code == 200:
                art_url = ca.url
        rg_tags = rec.get("release-group", {}).get("tags", [])
        genre = rg_tags[0]["name"].title() if rg_tags else ""
        return {"album": album, "art_url": art_url, "genre": genre}
    except Exception:
        return {"album": "", "art_url": "", "genre": ""}

# ── MP3 download + tag ────────────────────────────────────────────────────────

def download_and_tag(video: dict) -> dict:
    safe = re.sub(r'[\\/*?:"<>|]', "_", video["title"])[:80]
    out_template = os.path.join(DOWNLOAD_DIR, safe)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    mp3_path = out_template + ".mp3"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video["url"]])

    if not os.path.exists(mp3_path):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(safe) and f.endswith(".mp3"):
                mp3_path = os.path.join(DOWNLOAD_DIR, f)
                break

    # Metadata
    meta = fetch_metadata(video.get("artist",""), video.get("song",""))
    art_bytes = None
    if meta.get("art_url"):
        try:
            r = requests.get(meta["art_url"], timeout=15)
            if r.status_code == 200:
                art_bytes = r.content
        except Exception:
            pass

    # Save cover art
    if art_bytes:
        art_path = mp3_path.replace(".mp3", ".jpg")
        with open(art_path, "wb") as f:
            f.write(art_bytes)

    # Embed ID3
    try:
        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.add(TIT2(encoding=3, text=video.get("song","")))
        tags.add(TPE1(encoding=3, text=video.get("artist","")))
        if meta.get("album"):  tags.add(TALB(encoding=3, text=meta["album"]))
        if meta.get("genre"):  tags.add(TCON(encoding=3, text=meta["genre"]))
        if art_bytes:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art_bytes))
        tags.save(mp3_path, v2_version=3)
    except Exception:
        pass

    return {
        "file": os.path.basename(mp3_path),
        "album": meta.get("album",""),
        "genre": meta.get("genre",""),
        "has_art": art_bytes is not None,
    }

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/search", methods=["POST"])
def api_search():
    body  = request.json or {}
    mode  = body.get("mode", "trending")
    count = int(body.get("count", 10))
    count = max(1, min(count, 50))

    try:
        if mode == "trending":
            if YOUTUBE_API_KEY:
                videos = yt_api_chart(count)
            else:
                videos = yt_playlist(
                    "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
                    count
                )
        elif mode == "hot100":
            videos = fetch_spotify_hot100(count)
        elif mode == "genre":
            genre = body.get("genre", "pop")
            videos = yt_search_keyword(f"{genre} music official video 2025 2026", count)
        elif mode == "new":
            videos = yt_search_keyword("new music official video 2026", count)
        elif mode == "views":
            if YOUTUBE_API_KEY:
                videos = yt_api_chart(count)
                videos.sort(key=lambda v: v.get("views",0), reverse=True)
            else:
                videos = yt_search_keyword("most viewed music video 2025 2026", count)
        elif mode == "upandcoming":
            videos = yt_playlist(
                "https://www.youtube.com/playlist?list=PLbpi6ZahtOH6Ar_3GPy3workfN-m1An8",
                count
            )
            if not videos:
                videos = yt_search_keyword("up and coming artists new music 2026", count)
        elif mode == "keyword":
            kw = body.get("keyword", "music")
            videos = yt_search_keyword(kw, count)
        else:
            videos = yt_search_keyword("music official video", count)

        return jsonify({"ok": True, "videos": videos})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    body   = request.json or {}
    videos = body.get("videos", [])
    if not videos:
        return jsonify({"ok": False, "error": "No videos provided"}), 400

    results = []
    for video in videos:
        try:
            result = download_and_tag(video)
            results.append({"id": video["id"], "status": "ok", **result})
        except Exception as e:
            results.append({"id": video["id"], "status": "error", "error": str(e)})

    return jsonify({"ok": True, "results": results})


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "ok": True,
        "youtube_api": bool(YOUTUBE_API_KEY),
        "lastfm_api":  bool(LASTFM_API_KEY),
        "spotify_api": bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET),
        "download_dir": os.path.abspath(DOWNLOAD_DIR),
    })


@app.route("/downloads/<path:filename>")
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)


if __name__ == "__main__":
    print("=" * 55)
    print("  YouTube Music Downloader — Backend")
    print(f"  http://localhost:5000")
    print("=" * 55)
    print(f"  YouTube API : {'✔ configured' if YOUTUBE_API_KEY else '✘ not set (using fallback)'}")
    print(f"  Last.fm API : {'✔ configured' if LASTFM_API_KEY else '✘ not set (using MusicBrainz)'}")
    print(f"  Spotify API : {'✔ configured' if SPOTIFY_CLIENT_ID else '✘ not set (Hot 100 disabled)'}")
    print(f"  Downloads   : ./{DOWNLOAD_DIR}/")
    print("=" * 55 + "\n")
    app.run(debug=False, port=5000)
