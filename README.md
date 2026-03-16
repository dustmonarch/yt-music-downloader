# YT.Music.DL

**A local web app for browsing, previewing, and downloading YouTube music videos as tagged MP3s.**

Browse by trending chart, Spotify Hot 100, genre, view count, or keyword. See thumbnails, song titles, artist names, and view counts before you commit to downloading anything. Downloads are saved as 192kbps MP3s with ID3 tags (artist, album, genre) and embedded album art.

---

## Quick Start

```bash
# 1. Start the backend
python server.py

# 2. Open the app in your browser
#    Go to http://localhost:5000
#    Do NOT open index.html directly as a file://
```

> **Requires:** Python 3.10+, and [ffmpeg](https://ffmpeg.org/download.html) installed on your system.

---

## Files

| File | Description |
|---|---|
| `server.py` | Flask backend — searches YouTube/Spotify, downloads audio, fetches metadata, embeds ID3 tags. Auto-installs Python dependencies on first run. |
| `index.html` | Single-page frontend — video card grid with thumbnails, song/artist info, selection, and real-time download progress. Must be served via `server.py`. |
| `downloads/` | Output directory (auto-created). Contains `.mp3` files, `.jpg` album art, and `summary.json`. |

---

## Search Modes

| Mode | Description |
|---|---|
| 🔥 **Trending** | YouTube's most popular music chart. Uses YouTube Data API if a key is set, otherwise falls back to yt-dlp scraping. |
| 🎵 **Spotify Hot** | Spotify's Global Top 50 playlist, matched to YouTube videos. Requires Spotify API credentials. |
| 🎸 **By Genre** | 12 genre chips: Pop, Hip-Hop, R&B, Rock, Electronic, Country, Latin, K-Pop, Indie, Jazz, Metal, Soul. |
| ✨ **New Drops** | Recently uploaded official music videos. |
| 📈 **Top Views** | Sorted by all-time view count. |
| 🚀 **Rising** | Breakout and up-and-coming artists. |
| 🔍 **Keyword** | Free-text search — artist name, song title, vibe, anything. |

---

## How It Works

### Search
1. The frontend sends a `POST /api/search` request with the chosen mode and result count.
2. The backend fetches video metadata from YouTube (via API or yt-dlp) or Spotify.
3. Results come back with thumbnail URLs, titles, artists, view counts, and durations.
4. The frontend renders a card grid. Nothing is downloaded yet.

### Download
1. You click cards to select them, then hit **Download Selected**.
2. Each video is sent to `POST /api/download` one at a time.
3. yt-dlp pulls the best available audio stream; ffmpeg re-encodes it to 192kbps MP3.
4. Last.fm (or MusicBrainz if no key) is queried for album name, genre, and cover art.
5. Album art is saved as a `.jpg` alongside the `.mp3`.
6. ID3 tags (title, artist, album, genre, cover image) are embedded in the MP3 with mutagen.
7. Status updates appear in the UI in real time.

### Output
```
downloads/
  Billie Eilish - BIRDS OF A FEATHER.mp3
  Billie Eilish - BIRDS OF A FEATHER.jpg
  Sabrina Carpenter - Espresso.mp3
  Sabrina Carpenter - Espresso.jpg
  summary.json
```

`summary.json` contains a record for every downloaded track:
```json
{
  "title": "Billie Eilish - BIRDS OF A FEATHER (Official Music Video)",
  "artist": "Billie Eilish",
  "song": "BIRDS OF A FEATHER",
  "album": "HIT ME HARD AND SOFT",
  "genre": "Indie Pop",
  "file": "Billie Eilish - BIRDS OF A FEATHER.mp3",
  "status": "ok"
}
```

---

## Installation & Requirements

### ffmpeg

yt-dlp requires ffmpeg to convert audio streams to MP3.

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (via Chocolatey)
choco install ffmpeg
```

### Python packages

`server.py` installs everything automatically on first run:

```
flask  ·  yt-dlp  ·  mutagen  ·  requests  ·  google-api-python-client
```

Or install manually:
```bash
pip install flask yt-dlp mutagen requests google-api-python-client
```

---

## Configuration (Optional API Keys)

The app works without any API keys using free fallbacks. Keys unlock higher reliability, larger result counts, and Spotify Hot 100 mode.

Set these as environment variables before running `server.py`:

```bash
# macOS / Linux
export YOUTUBE_API_KEY=your_key_here
export LASTFM_API_KEY=your_key_here
export SPOTIFY_CLIENT_ID=your_id_here
export SPOTIFY_CLIENT_SECRET=your_secret_here

# Windows (Command Prompt)
set YOUTUBE_API_KEY=your_key_here
```

| Variable | Where to get it | What it unlocks |
|---|---|---|
| `YOUTUBE_API_KEY` | [Google Cloud Console](https://console.cloud.google.com/) | Reliable Trending/Top Views chart data; required for 50+ results |
| `LASTFM_API_KEY` | [last.fm/api](https://www.last.fm/api/account/create) | Better genre tags and album art (falls back to MusicBrainz without it) |
| `SPOTIFY_CLIENT_ID` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) | Required for Spotify Hot 100 mode |
| `SPOTIFY_CLIENT_SECRET` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) | Required for Spotify Hot 100 mode |

The three coloured pills in the top-right of the UI show green when a service is configured, red otherwise.

---

## Troubleshooting

**"Failed to fetch" / can't reach the server**
Open the app via `http://localhost:5000` in your browser, not by double-clicking `index.html`. Opening `index.html` as a `file://` URL causes the browser to block requests to `localhost`.

**ffmpeg not found**
yt-dlp needs ffmpeg to convert audio. See the Installation section above.

**Spotify Hot 100 fails**
This mode requires `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Register a free app at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) to get them.

**Thumbnails not loading**
Thumbnails are loaded directly from YouTube's CDN (`i.ytimg.com`). A firewall or VPN blocking YouTube will prevent them from loading. Downloads are unaffected.

**Downloads are slow**
Tracks download sequentially (one at a time) to avoid rate limiting. Speed depends on your connection and YouTube's server response.

**Server crashes on startup**
Check the terminal output for a Python traceback. The most common cause is a missing dependency — run `pip install flask yt-dlp mutagen requests` manually if auto-install fails.

---

## Legal Note

> This tool is intended for personal, non-commercial use only. Downloading copyrighted music without the rights holder's permission may violate YouTube's Terms of Service and applicable copyright law. Only download content you have the right to access offline.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python · Flask · yt-dlp · mutagen |
| Metadata | Last.fm API · MusicBrainz · Cover Art Archive |
| Charts | YouTube Data API v3 · Spotify Web API |
| Frontend | Vanilla HTML/CSS/JS (no build step) |
