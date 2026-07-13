#!/usr/bin/env python3
# ==============================================================================
# 👑 CEGUKAN SEEKER V21.0 — INDEX PARSER
# Handles: VTT download via yt-dlp, parse, JSON index builder, manual search
# ==============================================================================

import json
import os
import re
import subprocess
import threading
import time
import glob
import shutil
from datetime import datetime
from typing import Optional

try:
    from rich.console import Console
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TextColumn, TimeRemainingColumn, MofNCompleteColumn
    )
    from rich.panel import Panel
    from rich.text import Text
    from rich import box as rbox
except ImportError:
    print("[❌] Install rich dulu: pip install rich --break-system-packages")
    raise SystemExit(1)

_console     = Console()
_print_lock  = threading.Lock()

def safe_print(*args, **kwargs):
    with _print_lock:
        _console.print(*args, **kwargs)

# ── Hook age-restricted log ──────────────────────────────────────
try:
    from cs20_age_engine import hook_log_age_restricted as _hook_age_parser
    _AGE_HOOK_PARSER = True
except ImportError:
    _AGE_HOOK_PARSER = False
    def _hook_age_parser(*a, **kw): pass

# ==============================================================================
# STORAGE PATH DETECTION
# ==============================================================================
def detect_cache_root() -> str:
    """
    Deteksi path storage yang tersedia secara otomatis.
    Priority: shared storage Android > Termux home fallback
    """
    shared = os.path.expanduser("~/storage/shared")
    if os.path.isdir(shared):
        path = os.path.join(shared, "CS20_Index")
    else:
        path = os.path.expanduser("~/.cs20/index_cache")
        safe_print(
            "[yellow][⚠️] Storage permission belum disetup. "
            "Cache disimpan di Termux home.[/yellow]"
        )
        safe_print(
            "[dim]     Jalankan: termux-setup-storage "
            "untuk akses storage internal[/dim]"
        )
    os.makedirs(path, exist_ok=True)
    return path

# ==============================================================================
# VTT TIMESTAMP PARSER
# ==============================================================================
_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
_VTT_CUE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]+>")

def _vtt_time_to_sec(vtt_time: str) -> int:
    """Convert VTT timestamp '00:01:23.456' → integer seconds 83."""
    m = _VTT_TIMESTAMP_RE.match(vtt_time.strip())
    if not m:
        return 0
    h, mn, s, _ = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + mn * 60 + s

def parse_vtt_content(vtt_text: str) -> list:
    """
    Parse raw VTT string → list of {sec, text} dicts.

    Handles YouTube auto-generated VTT word-level duplication:
    Baris dengan timestamp start yang sama di-collapse, ambil teks terpanjang.
    Strip semua HTML tag (<c>, <00:01:23.000>, dll).
    """
    segments = {}  # key: start_sec → teks terpanjang

    lines = vtt_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        cue_match = _VTT_CUE_RE.match(line)
        if cue_match:
            start_sec = _vtt_time_to_sec(cue_match.group(1))
            # Kumpulkan baris teks sampai baris kosong
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                raw = lines[i].strip()
                # Strip HTML tags dan tag timestamp inline YouTube
                clean = _VTT_TAG_RE.sub("", raw).strip()
                if clean:
                    text_lines.append(clean)
                i += 1
            combined = " ".join(text_lines).strip()
            if combined:
                # Ambil teks terpanjang untuk timestamp yang sama (collapse duplikat)
                existing = segments.get(start_sec, "")
                if len(combined) > len(existing):
                    segments[start_sec] = combined
        else:
            i += 1

    # Sort by timestamp
    result = [
        {"sec": sec, "text": text}
        for sec, text in sorted(segments.items())
        if text
    ]
    return result

# ==============================================================================
# YT-DLP SUBTITLE DOWNLOADER
# ==============================================================================
_LANG_SUB_MAP = {
    "id": ["id", "en"],
    "en": ["en", "en-US", "en-GB"],
    "jp": ["ja", "ja-JP"],
    "kr": ["ko", "ko-KR"],
    "in": ["hi", "hi-IN", "en"],
}

def download_subtitle_single(
    video_id: str,
    output_dir: str,
    lang: str = "id",
    timeout: int = 30,
) -> dict:
    """
    Download auto-generated subtitle untuk satu video via yt-dlp.
    Return dict: {status, vtt_path, error_type, error_msg}
    """
    sub_langs = _LANG_SUB_MAP.get(lang, ["id", "en"])
    lang_str  = ",".join(sub_langs)
    url       = f"https://www.youtube.com/watch?v={video_id}"

    # Template output: {video_id}.{lang}.vtt
    outtmpl = os.path.join(output_dir, f"{video_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-langs",   lang_str,
        "--sub-format",  "vtt",
        "--skip-download",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--socket-timeout", "20",
        "-o", outtmpl,
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Cari file VTT yang dihasilkan
        pattern = os.path.join(output_dir, f"{video_id}.*.vtt")
        vtt_files = glob.glob(pattern)

        if vtt_files:
            return {
                "status":     "ok",
                "vtt_path":   vtt_files[0],
                "error_type": None,
                "error_msg":  None,
            }

        # Tidak ada file VTT → parse stderr untuk error type
        combined_err = (stdout + " " + stderr).lower()

        if "video unavailable" in combined_err or "private video" in combined_err:
            return {"status": "unavailable",   "vtt_path": None,
                    "error_type": "unavailable", "error_msg": stderr[:200]}

        if "confirm your age" in combined_err or "age-restricted" in combined_err:
            # Hook: catat ke log age_restricted
            _hook_age_parser(
                config_dir=os.path.dirname(output_dir),  # estimasi config_dir
                channel=channel,
                video_id=video_id,
                lang=lang,
            )
            return {"status": "age_restricted", "vtt_path": None,
                    "error_type": "age_restricted", "error_msg": stderr[:200]}

        if "no subtitles" in combined_err or "there are no" in combined_err \
                or "subtitles not available" in combined_err:
            return {"status": "no_sub",         "vtt_path": None,
                    "error_type": "no_sub",       "error_msg": stderr[:200]}

        if "429" in combined_err or "too many requests" in combined_err:
            return {"status": "rate_limited",   "vtt_path": None,
                    "error_type": "rate_limited", "error_msg": stderr[:200]}

        if "http error 403" in combined_err or "forbidden" in combined_err:
            return {"status": "forbidden",      "vtt_path": None,
                    "error_type": "forbidden",    "error_msg": stderr[:200]}

        if any(kw in combined_err for kw in (
            "timeout", "timed out", "connection", "network",
            "ssl", "remotedisconnected", "broken pipe"
        )):
            return {"status": "network_error",  "vtt_path": None,
                    "error_type": "network_error", "error_msg": stderr[:200]}

        if result.returncode != 0:
            return {"status": "error",          "vtt_path": None,
                    "error_type": " ytdlp_error",
                    "error_msg": stderr[:200] or f"exit code {result.returncode}"}

        # Exit 0 tapi tidak ada file → no_sub
        return {"status": "no_sub", "vtt_path": None,
                "error_type": "no_sub", "error_msg": "no subtitle file produced"}

    except subprocess.TimeoutExpired:
        return {"status": "network_error",  "vtt_path": None,
                "error_type": "timeout",    "error_msg": "yt-dlp timeout"}

    except FileNotFoundError:
        return {"status": "error",          "vtt_path": None,
                "error_type": "ytdlp_missing",
                "error_msg": "yt-dlp not found in PATH"}

    except Exception as e:
        return {"status": "error",          "vtt_path": None,
                "error_type": "unknown",
                "error_msg": f"{type(e).__name__}: {str(e)[:200]}"}


def download_and_index_video(
    video_id:   str,
    channel:    str,
    batch_no:   int,
    output_dir: str,
    index_dir:  str,
    lang:       str = "id",
    title:      str = "",
) -> dict:
    """
    Download subtitle → parse VTT → simpan JSON index.
    Hapus file VTT setelah parse berhasil.
    Return dict hasil (siap masuk status.json).
    """
    dl = download_subtitle_single(video_id, output_dir, lang)

    base = {
        "video_id":    video_id,
        "title":       title or video_id,
        "channel":     channel,
        "batch":       batch_no,
        "fetched_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "lang_used":   lang,
    }

    if dl["status"] != "ok":
        base.update({
            "status":       dl["status"],
            "error_type":   dl["error_type"],
            "error_msg":    dl["error_msg"],
            "segments":     [],
            "duration_sec": 0,
        })
        return base

    # Parse VTT
    vtt_path = dl["vtt_path"]
    try:
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
            vtt_text = f.read()
        segments = parse_vtt_content(vtt_text)
        duration = segments[-1]["sec"] if segments else 0

        base.update({
            "status":       "ok",
            "error_type":   None,
            "error_msg":    None,
            "segments":     segments,
            "duration_sec": duration,
        })

        # Simpan JSON index
        json_path = os.path.join(index_dir, f"{video_id}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, separators=(",", ":"))

    except Exception as e:
        base.update({
            "status":       "parse_error",
            "error_type":   "parse_error",
            "error_msg":    f"{type(e).__name__}: {str(e)[:200]}",
            "segments":     [],
            "duration_sec": 0,
        })
    finally:
        # Hapus file VTT setelah proses
        try:
            if os.path.exists(vtt_path):
                os.remove(vtt_path)
        except Exception:
            pass

    return base

# ==============================================================================
# VIDEO ID FETCHER (reuse logic dari engine lama, via yt-dlp)
# ==============================================================================
def get_playlist_count(channel: str, content_type: str) -> int:
    """
    Ambil estimasi jumlah video via --playlist-items 0 --print playlist_count.
    Return 0 jika gagal (caller harus handle fallback ke input manual).
    """
    if content_type == "live":
        url = f"https://www.youtube.com/@{channel}/streams"
    else:
        url = f"https://www.youtube.com/@{channel}/videos"

    cmd = [
        "yt-dlp",
        "--playlist-items", "0",
        "--print",          "playlist_count",
        "--quiet",
        "--no-warnings",
        "--socket-timeout", "15",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        line   = result.stdout.strip().splitlines()
        if line and line[0].isdigit():
            return int(line[0])
    except Exception:
        pass
    return 0


def fetch_video_ids_range(
    channel:      str,
    content_type: str,
    start:        int,
    end:          int,
) -> list:
    """
    Ambil video ID + judul untuk range index [start, end] (1-based, inklusif).
    Return list of (video_id, title) tuples.
    """
    if content_type == "live":
        urls       = [f"https://www.youtube.com/@{channel}/streams"]
        extra_args = []
    elif content_type == "video":
        urls       = [f"https://www.youtube.com/@{channel}/videos"]
        extra_args = ["--match-filter", "duration>60"]
    else:
        urls = [
            f"https://www.youtube.com/@{channel}/streams",
            f"https://www.youtube.com/@{channel}/videos",
        ]
        extra_args = ["--match-filter", "duration>60"]

    seen, result = set(), []
    for url in urls:
        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--print",          "%(id)s|||%(title)s",
            "--playlist-start", str(start),
            "--playlist-end",   str(end),
            "--no-playlist-reverse",
            *extra_args,
            "--quiet",
            "--no-warnings",
            "--socket-timeout", "20",
            url,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|||", 1)
                vid   = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else vid
                if vid and vid not in seen:
                    seen.add(vid)
                    result.append((vid, title))
        except Exception:
            pass

    return result

# ==============================================================================
# INDEX & STATUS JSON HELPERS
# ==============================================================================
def load_json_safe(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_safe(path: str, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, path)
        return True
    except Exception:
        return False

def load_meta(session_dir: str) -> dict:
    return load_json_safe(os.path.join(session_dir, "meta.json"), {})

def save_meta(session_dir: str, meta: dict):
    save_json_safe(os.path.join(session_dir, "meta.json"), meta)

def load_status(batch_dir: str) -> dict:
    return load_json_safe(os.path.join(batch_dir, "status.json"), {
        "batch_no": 0, "total": 0, "done": 0, "videos": {}
    })

def save_status(batch_dir: str, status: dict):
    save_json_safe(os.path.join(batch_dir, "status.json"), status)

def update_video_status(batch_dir: str, video_id: str, status_str: str):
    """Thread-safe update status satu video di status.json."""
    path = os.path.join(batch_dir, "status.json")
    with _print_lock:
        data = load_json_safe(path, {"videos": {}, "done": 0})
        data["videos"][video_id] = status_str
        data["done"] = sum(
            1 for v in data["videos"].values()
            if v not in ("pending",)
        )
        save_json_safe(path, data)

# ==============================================================================
# ERROR LOG (index-specific, terpisah dari _blocked.json engine lama)
# ==============================================================================
def init_error_log(path: str, channel: str, batch_no: int, lang: str):
    if os.path.exists(path):
        return
    save_json_safe(path, {
        "channel":    channel,
        "batch_no":   batch_no,
        "lang":       lang,
        "created_at": datetime.now().strftime("%d-%m-%Y %H:%M"),
        "videos":     [],
    })

def append_error_log(path: str, video_id: str, error_type: str, error_msg: str):
    """Thread-safe append error ke index error log."""
    with _print_lock:
        data = load_json_safe(path, {"videos": []})
        ids = [v["id"] for v in data.get("videos", [])]
        if video_id not in ids:
            data["videos"].append({
                "id":         video_id,
                "error_type": error_type,
                "error_msg":  error_msg,
                "logged_at":  datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            })
            save_json_safe(path, data)

def count_error_log(path: str) -> dict:
    """Return breakdown count per error_type."""
    data   = load_json_safe(path, {"videos": []})
    counts = {}
    for v in data.get("videos", []):
        et = v.get("error_type", "unknown")
        counts[et] = counts.get(et, 0) + 1
    return counts

# ==============================================================================
# MANUAL SEARCH ENGINE
# ==============================================================================
_PHRASE_RE   = re.compile(r'"([^"]+)"')
_OPERATOR_RE = re.compile(r'\b(AND|OR)\b')

def _tokenize_query(query: str) -> dict:
    """
    Parse query string menjadi struktur search.

    Supported:
      - Single word        : cegukan
      - Phrase (quote)     : "lagi minum"
      - AND                : cegukan AND minum
      - OR                 : cegukan OR hiccup
      - Mixed              : "lagi minum" OR "sambil makan" AND cegukan
      - Fuzzy (default)    : setiap term di-match dengan re.IGNORECASE + partial

    Return:
      {
        "terms":    [{"type": "phrase"|"word", "value": str, "compiled": re}],
        "operator": "AND"|"OR"  (default OR jika campur/tidak ada)
      }
    """
    # Tentukan operator dominan
    ops_found = _OPERATOR_RE.findall(query)
    if ops_found:
        operator = "AND" if ops_found.count("AND") >= ops_found.count("OR") else "OR"
    else:
        operator = "OR"

    terms = []

    # Extract phrase dulu
    phrase_matches = _PHRASE_RE.findall(query)
    query_remainder = _PHRASE_RE.sub("", query)

    for phrase in phrase_matches:
        phrase = phrase.strip()
        if phrase:
            # Phrase: semua kata harus muncul berurutan (dengan kemungkinan spasi/karakter antar kata)
            escaped = re.escape(phrase)
            try:
                compiled = re.compile(escaped, re.IGNORECASE)
            except re.error:
                compiled = re.compile(re.escape(phrase), re.IGNORECASE)
            terms.append({"type": "phrase", "value": phrase, "compiled": compiled})

    # Extract kata tunggal dari sisa (buang operator keywords)
    remainder_clean = _OPERATOR_RE.sub(" ", query_remainder)
    for word in remainder_clean.split():
        word = word.strip().strip('"').strip("'")
        if word and len(word) >= 2:
            try:
                compiled = re.compile(re.escape(word), re.IGNORECASE)
            except re.error:
                continue
            terms.append({"type": "word", "value": word, "compiled": compiled})

    return {"terms": terms, "operator": operator}


def search_index_batch(
    index_dir: str,
    query:     str,
    channel:   str,
) -> list:
    """
    Search semua JSON index di index_dir menggunakan parsed query.
    Return list of result dicts (compatible dengan build_html di engine lama).
    """
    parsed   = _tokenize_query(query)
    terms    = parsed["terms"]
    operator = parsed["operator"]

    if not terms:
        return []

    results  = []
    json_files = glob.glob(os.path.join(index_dir, "*.json"))

    for jf in json_files:
        data = load_json_safe(jf, None)
        if not data or data.get("status") != "ok":
            continue

        segments = data.get("segments", [])
        video_id = data.get("video_id", "")
        if not segments or not video_id:
            continue

        hits = _search_segments(segments, terms, operator, video_id)
        if not hits:
            continue

        # Build result dict compatible dengan build_html engine lama
        tier_counts = {"CORE": len(hits), "TYPO": 0, "SILENT": 0, "CONTEXT": 0, "FP": 0}
        html_rows   = _build_html_rows_search(hits, video_id, query)

        results.append({
            "video_id":    video_id,
            "channel":     channel,
            "status":      "analyzed",
            "status_label": f"🔍 MANUAL SEARCH — {len(hits)} hit",
            "hits":         hits,
            "score":        len(hits) * 5,
            "persentase":   min(100, len(hits) * 10),
            "tier_counts":  tier_counts,
            "cluster_count": 1,
            "maraton_mins": 0,
            "is_valid":     True,
            "kasta":        "VALID",
            "kasta_label":  f"🔍 SEARCH: {query[:40]}",
            "html_rows":    html_rows,
        })

    # Sort by hit count descending
    results.sort(key=lambda x: len(x["hits"]), reverse=True)
    return results


def _search_segments(
    segments: list,
    terms:    list,
    operator: str,
    video_id: str,
) -> list:
    """
    Cari term di segments. Return list of hit dicts.
    AND = semua term harus ada di video (anywhere)
    OR  = minimal satu term ada
    """
    if operator == "AND":
        # Cek semua term ada di video dulu
        full_text = " ".join(s["text"] for s in segments)
        for term in terms:
            if not term["compiled"].search(full_text):
                return []

    hits = []
    seen_secs = set()

    for seg in segments:
        text     = seg.get("text", "")
        start_sec = seg.get("sec", 0)

        if start_sec in seen_secs:
            continue

        matched = False
        if operator == "OR":
            matched = any(t["compiled"].search(text) for t in terms)
        else:  # AND — tampilkan semua baris yang match term manapun
            matched = any(t["compiled"].search(text) for t in terms)

        if matched:
            seen_secs.add(start_sec)
            hits.append({
                "sec":   start_sec,
                "time":  _sec_to_hms(start_sec),
                "text":  text,
                "tiers": {"CORE": 1, "TYPO": 0, "SILENT": 0, "CONTEXT": 0, "FP": 0},
                "url":   f"https://youtu.be/{video_id}?t={start_sec}",
            })

    return hits


def _sec_to_hms(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _build_html_rows_search(hits: list, video_id: str, query: str) -> str:
    """Build HTML rows untuk hasil search manual."""
    rows = ""
    for hit in hits:
        safe_text = (
            hit["text"]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        rows += (
            f"<tr>"
            f"<td><a href='{hit['url']}' target='_blank' class='t-link'>"
            f"[{hit['time']}]</a></td>"
            f"<td><span class='tc core'>SEARCH</span> {safe_text}</td>"
            f"</tr>\n"
        )
    return rows