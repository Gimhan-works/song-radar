#!/usr/bin/env python3
"""
Trending Song Radar
-------------------
Finds music videos released in the last WINDOW_HOURS (default 5) via the
YouTube Data API, tracks their growth over time, and writes data.json with an
estimated "trending score" (0-100) for each song.

Usage:
    export YOUTUBE_API_KEY="your-key"
    python tracker.py              # run once
    python tracker.py --serve      # run every 60 min + serve dashboard on :8000
"""
import argparse
import functools
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from math import log10

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "tracker.db")
OUT_PATH = os.path.join(BASE, "data.json")
API = "https://www.googleapis.com/youtube/v3/"

WINDOW_HOURS = float(os.environ.get("WINDOW_HOURS", "5"))
REGIONS = [r.strip() for r in os.environ.get("REGIONS", "US").split(",") if r.strip()]
MIN_SECONDS, MAX_SECONDS = 90, 600  # keep song-length videos only

# Karaoke signal: how many karaoke / instrumental (voice removed) videos exist for a song.
# Each check is one YouTube search = 100 quota units, so it is capped and cached.
KARAOKE_TOP = int(os.environ.get("KARAOKE_TOP", "15"))                 # songs checked per run
KARAOKE_RECHECK_MIN = int(os.environ.get("KARAOKE_RECHECK_MIN", "120"))  # re-check a song after N min
KARAOKE_DAILY_SEARCHES = int(os.environ.get("KARAOKE_DAILY_SEARCHES", "60"))  # hard daily cap
KARAOKE_RE = re.compile(
    r"karaoke|instrumental|no vocals?|without vocals?|backing track|minus one|vocal remove|sing[- ]?along",
    re.I,
)


# --- "New song release" filter: everything else is dropped from the list ---
MIN_CHANNEL_SUBS = int(os.environ.get("MIN_CHANNEL_SUBS", "20000"))  # ignore tiny/unknown uploaders
NOT_A_SONG_RE = re.compile(
    r"\bcover(s|ed)?\b|karaoke|instrumental|no vocals?|without vocals?|backing track|minus one|"
    r"\bremix(ed)?\b|\bmashup\b|\bbootleg\b|slowed|reverb|sped[- ]?up|nightcore|8d audio|bass boosted|"
    r"\bmix\b|\bplaylist\b|full album|\bcompilation\b|non[- ]?stop|\bjukebox\b|top \d+|best of|"
    r"reaction|\breact\w*|review|tutorial|how to|lesson|piano version|guitar tab|"
    r"live (at|in|on|from|session|performance)|\bconcert\b|behind the scenes|making of|interview|"
    r"\btrailer\b|\bteaser\b|\bpodcast\b|\bshorts?\b|\bepisode\b|\bvlog\b|\bunboxing\b|"
    r"ringtone|\bloop\b|\b1 hour\b|\b10 hours?\b|study music|lofi (mix|beats)|sleep music",
    re.I,
)


def is_new_song(title, channel, tags, subs):
    """True only for a normal song release (official-looking upload of a single track)."""
    if NOT_A_SONG_RE.search(title):
        return False
    if re.search(r"\bfull (video|movie|film)\b|\bscene\b", title, re.I):
        return False
    # unknown / tiny channels are mostly re-uploads, fan edits and spam;
    # "- Topic" auto-channels and VEVO are always real label uploads
    official = channel.endswith("- Topic") or "vevo" in channel.lower() or "official" in channel.lower()
    return official or subs >= MIN_CHANNEL_SUBS


def api(endpoint, **params):
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def parse_duration(iso):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY, published REAL);
        CREATE TABLE IF NOT EXISTS snapshots(
            video_id TEXT, ts REAL, views INTEGER, likes INTEGER, comments INTEGER);
        CREATE INDEX IF NOT EXISTS idx_snap ON snapshots(video_id, ts);
        CREATE TABLE IF NOT EXISTS karaoke(
            video_id TEXT PRIMARY KEY, count INTEGER, links TEXT, checked REAL);
        CREATE TABLE IF NOT EXISTS quota(day TEXT PRIMARY KEY, searches INTEGER);
        """
    )
    return conn


def search_new():
    after = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for region in REGIONS:
        res = api(
            "search", part="id", type="video", videoCategoryId="10",
            order="viewCount", publishedAfter=after, regionCode=region, maxResults=50,
        )
        ids.update(i["id"]["videoId"] for i in res.get("items", []))
    return ids


def chunks(seq, n=50):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_videos(ids):
    out = []
    for part in chunks(ids):
        res = api("videos", part="snippet,statistics,contentDetails", id=",".join(part))
        out.extend(res.get("items", []))
    return out


def fetch_subs(channel_ids):
    subs = {}
    for part in chunks(set(channel_ids)):
        res = api("channels", part="statistics", id=",".join(part))
        for c in res.get("items", []):
            st = c["statistics"]
            subs[c["id"]] = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
    return subs


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def clean_title(title):
    """'Artist - Song (Official Video) ft. X' -> 'Artist - Song'."""
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", title)
    t = t.split("|")[0]
    t = re.sub(r"\b(ft|feat|featuring)\b\.?.*$", " ", t, flags=re.I)
    t = re.sub(r"\b(official|video|music|audio|lyric|lyrics|visualizer|hd|4k|mv)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text):
    return set(re.findall(r"\w{3,}", text.lower()))


def count_karaoke(title, video_id, published):
    """Search karaoke/instrumental uploads of this song made after it was released.
    Returns (count, up to 3 example links). Costs one search (100 quota units)."""
    q = clean_title(title) or title
    want = tokens(q) - {"karaoke", "instrumental"}
    after = datetime.fromtimestamp(published, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = api(
        "search", part="snippet", q=q + " karaoke", type="video",
        publishedAfter=after, maxResults=25,
    )
    hits = []
    for it in res.get("items", []):
        vid, t = it["id"].get("videoId"), it["snippet"]["title"]
        if not vid or vid == video_id or not KARAOKE_RE.search(t):
            continue
        # the upload must actually be about this song (>= 60% of its title words)
        if want and len(want & tokens(t)) < max(1, round(0.6 * len(want))):
            continue
        hits.append({"id": vid, "title": t, "url": f"https://www.youtube.com/watch?v={vid}"})
    return len(hits), hits[:3]


def enrich_karaoke(conn, songs, inputs, now):
    """Attach karaoke counts to the top-ranked songs (cached + daily quota cap),
    then re-score them with the karaoke bonus."""
    if KARAOKE_TOP <= 0:
        return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute("SELECT searches FROM quota WHERE day=?", (day,)).fetchone()
    used = row[0] if row else 0
    blocked = False
    for rank, s in enumerate(songs):
        cached = conn.execute(
            "SELECT count, links, checked FROM karaoke WHERE video_id=?", (s["id"],)
        ).fetchone()
        fresh = cached is not None and now - cached[2] < KARAOKE_RECHECK_MIN * 60
        if not fresh and rank < KARAOKE_TOP and used < KARAOKE_DAILY_SEARCHES and not blocked:
            try:
                count, links = count_karaoke(s["title"], s["id"], inputs[s["id"]][1])
            except urllib.error.HTTPError as e:
                print(f"karaoke search skipped (HTTP {e.code})", file=sys.stderr)
                blocked = True
            else:
                used += 1
                conn.execute(
                    "INSERT OR REPLACE INTO karaoke VALUES(?,?,?,?)",
                    (s["id"], count, json.dumps(links), now),
                )
                cached = (count, json.dumps(links), now)
        if cached is not None:
            s["karaoke_count"] = cached[0]
            s["karaoke_links"] = json.loads(cached[1])
            s["score"] = compute_score(*inputs[s["id"]][0], karaoke=cached[0])[0]
            # Opportunity = strong trend + few karaoke versions yet (room for YOUR upload).
            # Uses the score WITHOUT the karaoke bonus, then discounts it by karaoke competition.
            base = compute_score(*inputs[s["id"]][0])[0]
            s["opportunity"] = round(base * (1 - 0.7 * clip(cached[0] / 5)), 1)
    conn.execute("INSERT OR REPLACE INTO quota VALUES(?,?)", (day, used))
    songs.sort(key=lambda s: s["score"], reverse=True)


def compute_score(age_h, views, likes, comments, subs, snaps, karaoke=None):
    """Return (score 0-100, views_per_hour, acceleration ratio or None).
    karaoke = number of karaoke/instrumental videos found for the song (None = not checked)."""
    age_h = max(age_h, 0.25)
    vph = views / age_h

    # 1) Raw speed: 100k views/hour ~ max
    s_speed = clip(log10(1 + vph) / 5)

    # 2) Acceleration: is the recent rate higher than the lifetime average?
    accel = None
    s_accel = 0.5  # neutral until we have two snapshots
    if len(snaps) >= 2:
        last_ts, last_views = snaps[-1][0], snaps[-1][1]
        prev = [s for s in snaps if s[0] <= last_ts - 900]  # >= 15 min earlier
        if prev:
            p_ts, p_views = prev[-1][0], prev[-1][1]
            recent_rate = (last_views - p_views) / max((last_ts - p_ts) / 3600, 0.05)
            if vph > 0:
                accel = recent_rate / vph
                s_accel = clip((accel - 0.5) / 1.5)

    # 3) Reach beyond fanbase: views relative to subscribers
    s_reach = clip(log10(1 + 100 * views / max(subs, 1000)) / 3)

    # 4) Engagement
    like_ratio = likes / views if views else 0
    comment_rate = comments / views if views else 0
    s_eng = 0.5 * clip(like_ratio / 0.06) + 0.5 * clip(comment_rate / 0.005)

    # 5) Artist/channel size
    s_chan = clip(log10(1 + subs) / 7)

    score = 100 * (0.35 * s_speed + 0.20 * s_accel + 0.20 * s_reach + 0.15 * s_eng + 0.10 * s_chan)

    # 6) Karaoke bonus: people only make voice-removed versions of songs they want to sing.
    #    5 or more karaoke uploads = +10 points (capped at 100).
    if karaoke:
        score = min(100.0, score + 10 * clip(karaoke / 5))
    return round(score, 1), round(vph), (round(accel, 2) if accel is not None else None)


def run_once():
    if not os.environ.get("YOUTUBE_API_KEY"):
        sys.exit("Set YOUTUBE_API_KEY first (see README.md).")
    conn = init_db()
    now = time.time()
    cutoff = now - WINDOW_HOURS * 3600

    new_ids = search_new()
    tracked = {r[0] for r in conn.execute("SELECT id FROM videos WHERE published > ?", (cutoff,))}
    items = fetch_videos(new_ids | tracked)
    subs = fetch_subs(i["snippet"]["channelId"] for i in items)

    songs = []
    inputs = {}  # video id -> (compute_score args, published timestamp)
    for it in items:
        vid, sn, st = it["id"], it["snippet"], it["statistics"]
        published = parse_ts(sn["publishedAt"])
        dur = parse_duration(it["contentDetails"].get("duration"))
        if published < cutoff or not (MIN_SECONDS <= dur <= MAX_SECONDS):
            continue
        if sn.get("liveBroadcastContent") not in (None, "none"):
            continue
        # Only genuine NEW SONG releases: drop covers, karaoke, remixes, mixes, reactions, etc.
        if sn.get("categoryId") not in (None, "10"):
            continue
        ch_subs = subs.get(sn["channelId"], 0)
        if not is_new_song(sn["title"], sn.get("channelTitle", ""), sn.get("tags"), ch_subs):
            continue
        views = int(st.get("viewCount", 0))
        likes = int(st.get("likeCount", 0))
        comments = int(st.get("commentCount", 0))

        conn.execute("INSERT OR IGNORE INTO videos VALUES(?,?)", (vid, published))
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?)", (vid, now, views, likes, comments))
        snaps = conn.execute(
            "SELECT ts, views FROM snapshots WHERE video_id=? ORDER BY ts", (vid,)
        ).fetchall()

        args = ((now - published) / 3600, views, likes, comments, ch_subs, snaps)
        inputs[vid] = (args, published)
        score, vph, accel = compute_score(*args)
        thumbs = sn.get("thumbnails", {})
        songs.append({
            "id": vid,
            "title": sn["title"],
            "channel": sn["channelTitle"],
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
            "age_min": round((now - published) / 60),
            "views": views,
            "likes": likes,
            "comments": comments,
            "subs": ch_subs,
            "views_per_hour": vph,
            "acceleration": accel,
            "karaoke_count": None,   # filled by enrich_karaoke for the top songs
            "karaoke_links": [],
            "opportunity": None,     # trend strength discounted by karaoke competition
            "score": score,
        })

    songs.sort(key=lambda s: s["score"], reverse=True)
    enrich_karaoke(conn, songs, inputs, now)

    conn.execute("DELETE FROM karaoke WHERE checked < ?", (now - 48 * 3600,))
    conn.execute("DELETE FROM snapshots WHERE ts < ?", (now - 48 * 3600,))
    conn.execute("DELETE FROM videos WHERE published < ?", (now - 48 * 3600,))
    conn.commit()
    conn.close()

    songs.sort(key=lambda s: s["score"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": WINDOW_HOURS,
        "regions": REGIONS,
        "songs": songs[:50],
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[{datetime.now():%H:%M:%S}] tracked {len(songs)} songs -> data.json")


def safe_run():
    try:
        run_once()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        print(f"YouTube API error {e.code}: {body}", file=sys.stderr)
    except Exception as e:  # keep the loop alive
        print(f"Error: {e}", file=sys.stderr)


def serve_forever(interval_min, port):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=BASE)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Dashboard: http://localhost:{port}/index.html  (refreshing every {interval_min} min)")
    while True:
        safe_run()
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="loop + serve dashboard")
    ap.add_argument("--interval", type=int, default=60, help="minutes between runs (default 60)")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if args.serve:
        serve_forever(args.interval, args.port)
    else:
        run_once()
