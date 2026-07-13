#!/usr/bin/env python3
# ==============================================================================
# 👑 CEGUKAN SEEKER V20.0 — AGE RESTRICTED ENGINE
# Bypass age-restricted videos via yt-dlp + cookies (Netscape format)
# Interface: Rich full dashboard — Termux-compatible
# ==============================================================================

import argparse
import gc
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TextColumn, TimeRemainingColumn, MofNCompleteColumn,
    )
    from rich.layout import Layout
    from rich import box as rbox
    from rich.rule import Rule
    from rich.align import Align
except ImportError:
    print("[❌] Install rich dulu: pip install rich --break-system-packages")
    sys.exit(1)

# Tambahkan direktori skrip ke sys.path supaya bisa import cs20_engine
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ==============================================================================
# CONSTANTS & GLOBALS
# ==============================================================================
_console    = Console()

# Saat True (dipicu --json-events), engine dipanggil dari UI (Streamlit):
# - semua prompt interaktif di-skip, pakai nilai dari args langsung
# - output tambahan berupa JSON event per baris (prefix "CS20JSON:")
JSON_MODE: bool = False

def _emit_json(event_type: str, payload: dict):
    line = {"type": event_type, **payload}
    print("CS20JSON:" + json.dumps(line, ensure_ascii=False), flush=True)
_print_lock = threading.Lock()

_TERM_WIDTH   = shutil.get_terminal_size(fallback=(42, 24)).columns
_COMPACT_MODE = _TERM_WIDTH < 50
_BAR_WIDTH    = max(10, min(26, _TERM_WIDTH - 34))

# ── VTT subtitle lang map ──────────────────────────────────────────
_LANG_SUB_MAP = {
    "id": ["id", "en"],
    "en": ["en", "en-US", "en-GB"],
    "jp": ["ja", "ja-JP"],
    "kr": ["ko", "ko-KR"],
    "in": ["hi", "hi-IN"],
}

# ── Storage root ───────────────────────────────────────────────────
def _detect_age_root() -> str:
    shared = os.path.expanduser("~/storage/shared")
    if os.path.isdir(shared):
        path = os.path.join(shared, "CS20_AgeRestricted")
    else:
        path = os.path.expanduser("~/.cs20/age_restricted")
    os.makedirs(path, exist_ok=True)
    return path

# ── Live stats ─────────────────────────────────────────────────────
_stats = {
    "phase":        "bypass",   # "bypass" | "analysis"
    "done":         0,
    "total":        0,
    "ok":           0,
    "no_sub":       0,
    "still_blocked":0,
    "unavailable":  0,
    "rate_limited": 0,
    "network":      0,
    "error":        0,
    "an_valid":     0,
    "an_hits":      0,
    "start_time":   0.0,
    "_jobs":        2,
}
_stats_lock      = threading.Lock()
_print_lock      = threading.Lock()
_partial_results = []
_cleanup_done    = False
_current_log_path = ""

# ==============================================================================
# HELPER
# ==============================================================================
def safe_print(*args, **kwargs):
    with _print_lock:
        _console.print(*args, **kwargs)

def _fmt_eta(done: int, total: int, start_time: float) -> str:
    if done == 0:
        return "menghitung..."
    elapsed   = time.time() - start_time
    rate      = done / elapsed if elapsed > 0 else 0
    remaining = (total - done) / rate if rate > 0 else 0
    m, s      = divmod(int(remaining), 60)
    return f"~{m}m {s}s"

def _sec_to_hms(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ==============================================================================
# VTT PARSER
# ==============================================================================
_VTT_TS_RE  = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
_VTT_CUE_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
_VTT_TAG_RE = re.compile(r"<[^>]+>")

def _vtt_ts_to_sec(ts: str) -> int:
    m = _VTT_TS_RE.match(ts.strip())
    if not m:
        return 0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

def _parse_vtt(vtt_text: str) -> list:
    """Parse VTT string → list of {sec, text}."""
    segments: dict = {}
    lines = vtt_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        cue = _VTT_CUE_RE.match(line)
        if cue:
            start_sec = _vtt_ts_to_sec(cue.group(1))
            text_parts = []
            i += 1
            while i < len(lines) and lines[i].strip():
                raw = _VTT_TAG_RE.sub("", lines[i]).strip()
                if raw:
                    text_parts.append(raw)
                i += 1
            text = " ".join(text_parts).strip()
            if text:
                existing = segments.get(start_sec, "")
                if len(text) > len(existing):
                    segments[start_sec] = text
        i += 1
    return [{"sec": s, "text": t} for s, t in sorted(segments.items())]

# ==============================================================================
# COOKIES VALIDATION
# ==============================================================================
def _check_cookies_exist(cookies_path: str) -> bool:
    return os.path.isfile(cookies_path) and os.path.getsize(cookies_path) > 0

def _parse_netscape_cookies(cookies_path: str) -> list:
    """
    Parse Netscape cookies.txt.
    Return list of dict: {domain, flag, path, secure, expiry, name, value}
    """
    cookies = []
    try:
        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    try:
                        expiry = int(parts[4])
                    except ValueError:
                        expiry = 0
                    cookies.append({
                        "domain": parts[0],
                        "flag":   parts[1],
                        "path":   parts[2],
                        "secure": parts[3],
                        "expiry": expiry,
                        "name":   parts[5],
                        "value":  parts[6],
                    })
    except Exception:
        pass
    return cookies

def _validate_cookies_netscape(cookies_path: str) -> dict:
    """
    Validasi cookies via:
    1. Parse Netscape → cek field expiry
    2. Cek apakah ada cookie penting YouTube (SAPISID, SID, HSID, etc.)

    Return {valid: bool, reason: str, expired_count: int, yt_cookies: int}
    """
    cookies = _parse_netscape_cookies(cookies_path)
    if not cookies:
        return {"valid": False, "reason": "File cookies kosong atau format tidak dikenali",
                "expired_count": 0, "yt_cookies": 0}

    now_ts = int(time.time())
    expired = 0
    yt_important = {"SAPISID", "SID", "HSID", "__Secure-1PSID",
                    "__Secure-3PSID", "LOGIN_INFO", "APISID"}
    found_yt   = 0
    found_imp  = 0
    all_expired_imp = True

    for ck in cookies:
        if "youtube.com" not in ck["domain"] and "google.com" not in ck["domain"]:
            continue
        found_yt += 1
        if ck["name"] in yt_important:
            found_imp += 1
            # expiry 0 = sesi / tidak expire
            if ck["expiry"] == 0 or ck["expiry"] > now_ts:
                all_expired_imp = False
            else:
                expired += 1

    if found_yt == 0:
        return {"valid": False,
                "reason": "Tidak ada cookie YouTube/Google ditemukan",
                "expired_count": expired, "yt_cookies": 0}

    if found_imp == 0:
        return {"valid": False,
                "reason": "Cookie auth penting (SAPISID/SID) tidak ditemukan",
                "expired_count": expired, "yt_cookies": found_yt}

    if all_expired_imp and expired > 0:
        return {"valid": False,
                "reason": f"Semua cookie auth sudah expired ({expired} cookie kadaluarsa)",
                "expired_count": expired, "yt_cookies": found_yt}

    return {"valid": True,
            "reason": f"OK — {found_yt} cookie YT ditemukan, {found_imp} auth cookie valid",
            "expired_count": expired, "yt_cookies": found_yt}

def _test_cookies_live(cookies_path: str, test_video_id: str) -> dict:
    """
    Test cookies secara live: coba yt-dlp dry-run ke video age-restricted.
    Return {ok: bool, msg: str}
    """
    url = f"https://www.youtube.com/watch?v={test_video_id}"
    cmd = [
        "yt-dlp",
        "--cookies",      cookies_path,
        "--skip-download",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--socket-timeout", "20",
        "--print",        "id",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        combined = (r.stdout + r.stderr).lower()

        if r.returncode == 0 and test_video_id in r.stdout:
            return {"ok": True, "msg": "Cookies valid & video berhasil diakses"}

        if "sign in" in combined or "login" in combined or "cookie" in combined:
            return {"ok": False, "msg": "Cookies ditolak YouTube — perlu login ulang"}
        if "age" in combined or "confirm your age" in combined:
            return {"ok": False, "msg": "Cookies tidak punya akses age-restricted"}
        if "429" in combined or "too many" in combined:
            return {"ok": False, "msg": "Rate limited saat test cookies"}
        if "unavailable" in combined or "private" in combined:
            # Video tidak tersedia, tapi bukan masalah cookies
            return {"ok": True,  "msg": "Cookies terlihat valid (video tidak tersedia)"}
        if "timed out" in combined or "timeout" in combined:
            return {"ok": False, "msg": "Timeout saat test — cek koneksi"}

        # returncode != 0 tapi bukan error cookies jelas → anggap valid
        if r.returncode != 0:
            return {"ok": True,  "msg": "Cookies terlihat valid (exit non-zero tapi tidak ada error auth)"}

        return {"ok": True, "msg": "Cookies valid"}

    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "Timeout saat test cookies"}
    except FileNotFoundError:
        return {"ok": False, "msg": "yt-dlp tidak ditemukan di PATH"}
    except Exception as e:
        return {"ok": False, "msg": f"Error saat test: {e}"}

# ==============================================================================
# LOG MANAGEMENT
# ==============================================================================
def _age_log_root(channel: str) -> str:
    """Return root folder log untuk channel: CS20_AgeRestricted/{channel}/"""
    root = os.path.join(_detect_age_root(), channel)
    os.makedirs(root, exist_ok=True)
    return root

def _new_session_dir(channel: str) -> str:
    """Buat folder sesi baru berdasarkan timestamp."""
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(_age_log_root(channel), f"sesi_{ts}")
    os.makedirs(path, exist_ok=True)
    return path

def _list_sessions(channel: str) -> list:
    """List semua sesi untuk channel, paling baru di depan."""
    root = _age_log_root(channel)
    sesi = sorted(
        [d for d in os.listdir(root) if d.startswith("sesi_")],
        reverse=True,
    )
    return [os.path.join(root, s) for s in sesi]

def _log_path(session_dir: str, channel: str) -> str:
    return os.path.join(session_dir, f"{channel}_agerestricted.json")

def _init_log(log_path: str, channel: str, lang: str):
    if os.path.exists(log_path):
        return
    data = {
        "channel":    channel,
        "lang":       lang,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "videos":     [],
    }
    _write_log(log_path, data)

def _read_log(log_path: str) -> dict:
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"channel": "?", "lang": "id", "created_at": "?", "videos": []}

def _write_log(log_path: str, data: dict):
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _append_failed_log(log_path: str, video_id: str, reason: str, channel: str, lang: str):
    """Tambah video yang gagal ke log (thread-safe). Dipakai saat bypass gagal."""
    try:
        with _print_lock:
            if os.path.exists(log_path):
                data = _read_log(log_path)
            else:
                data = {"channel": channel, "lang": lang,
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "videos": []}
            ids = [v["id"] for v in data.get("videos", [])]
            if video_id not in ids:
                data["videos"].append({
                    "id":        video_id,
                    "reason":    reason,
                    "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                _write_log(log_path, data)
    except Exception:
        pass

# Hook dipanggil engine lain saat age_restricted terdeteksi
def hook_log_age_restricted(config_dir: str, channel: str, video_id: str, lang: str = "id"):
    """
    Hook untuk cs20_engine.py dan cs20_index_engine.py.
    Dipanggil saat status == 'age_restricted'.
    Menyimpan ke log di CS20_AgeRestricted/{channel}/pending_log.json
    sehingga bisa dipick-up oleh mode ini nanti.
    """
    try:
        root     = _age_log_root(channel)
        pending  = os.path.join(root, "pending_log.json")
        if os.path.exists(pending):
            data = _read_log(pending)
        else:
            data = {"channel": channel, "lang": lang,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "videos": []}
        ids = [v["id"] for v in data.get("videos", [])]
        if video_id not in ids:
            data["videos"].append({
                "id":        video_id,
                "reason":    "age_restricted",
                "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            _write_log(pending, data)
    except Exception:
        pass

def _load_pending_log(channel: str) -> dict | None:
    """Cek apakah ada pending_log dari engine lain."""
    root    = _age_log_root(channel)
    pending = os.path.join(root, "pending_log.json")
    if not os.path.exists(pending):
        return None
    data = _read_log(pending)
    if not data.get("videos"):
        return None
    return data

# ==============================================================================
# BYPASS DOWNLOAD (yt-dlp + cookies)
# ==============================================================================
def _download_subtitle_with_cookies(
    video_id:    str,
    cookies_path: str,
    lang:        str,
    output_dir:  str,
    timeout:     int = 45,
) -> dict:
    """
    Download subtitle video age-restricted via yt-dlp + cookies.
    Return {status, vtt_path, error_type, error_msg}
    """
    sub_langs = _LANG_SUB_MAP.get(lang, ["id", "en"])
    lang_str  = ",".join(sub_langs)
    url       = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl   = os.path.join(output_dir, f"{video_id}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--cookies",       cookies_path,
        "--write-auto-subs",
        "--sub-langs",     lang_str,
        "--sub-format",    "vtt",
        "--skip-download",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--socket-timeout", "20",
        "-o", outtmpl,
        url,
    ]

    try:
        r      = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        comb   = (stdout + " " + stderr).lower()

        # Cari VTT
        pattern   = os.path.join(output_dir, f"{video_id}.*.vtt")
        vtt_files = glob.glob(pattern)
        if vtt_files:
            return {"status": "ok", "vtt_path": vtt_files[0],
                    "error_type": None, "error_msg": None}

        # Tidak ada VTT — parse error
        if "video unavailable" in comb or "private video" in comb:
            return {"status": "unavailable", "vtt_path": None,
                    "error_type": "unavailable", "error_msg": stderr[:200]}

        if "confirm your age" in comb or "age-restricted" in comb or "sign in" in comb:
            return {"status": "still_blocked", "vtt_path": None,
                    "error_type": "age_bypass_failed", "error_msg": stderr[:200]}

        if "no subtitles" in comb or "there are no" in comb \
                or "subtitles not available" in comb:
            return {"status": "no_sub", "vtt_path": None,
                    "error_type": "no_sub", "error_msg": stderr[:200]}

        if "429" in comb or "too many requests" in comb:
            return {"status": "rate_limited", "vtt_path": None,
                    "error_type": "rate_limited", "error_msg": stderr[:200]}

        if any(kw in comb for kw in (
            "timeout", "timed out", "connection", "network",
            "ssl", "remotedisconnected", "broken pipe",
        )):
            return {"status": "network_error", "vtt_path": None,
                    "error_type": "network_error", "error_msg": stderr[:200]}

        if r.returncode != 0:
            return {"status": "error", "vtt_path": None,
                    "error_type": "ytdlp_error",
                    "error_msg": stderr[:200] or f"exit {r.returncode}"}

        return {"status": "no_sub", "vtt_path": None,
                "error_type": "no_sub", "error_msg": "no subtitle file produced"}

    except subprocess.TimeoutExpired:
        return {"status": "network_error", "vtt_path": None,
                "error_type": "timeout", "error_msg": "yt-dlp timeout"}
    except FileNotFoundError:
        return {"status": "error", "vtt_path": None,
                "error_type": "ytdlp_missing", "error_msg": "yt-dlp not found"}
    except Exception as e:
        return {"status": "error", "vtt_path": None,
                "error_type": "unknown", "error_msg": str(e)[:200]}

# ==============================================================================
# ANALISIS (reuse fuzzy engine dari cs20_engine)
# ==============================================================================
def _load_fuzzy_engine(lang: str):
    """
    Import COMPILED_TIERS & ALL_PATTERNS_COMBINED dari cs20_engine.
    Return (COMPILED_TIERS, ALL_PATTERNS_COMBINED) atau fallback.
    """
    try:
        from cs20_engine import _ALL_KEYWORD_TIERS, _init_lang
        _init_lang(lang)
        from cs20_engine import COMPILED_TIERS, ALL_PATTERNS_COMBINED
        return COMPILED_TIERS, ALL_PATTERNS_COMBINED, True
    except ImportError:
        pass

    # Fallback minimal
    import re as _re
    fallback_pat = _re.compile(
        r"cegukan|hiccup|cekukan|jegukan|しゃっくり|딸꾹질", _re.IGNORECASE
    )
    fallback_tiers = {
        "CORE": {"bobot": 5, "patterns": [fallback_pat]},
    }
    return fallback_tiers, fallback_pat, False

def _analyze_segments(
    video_id: str,
    channel:  str,
    segments: list,
    COMPILED_TIERS,
    ALL_PATTERNS,
) -> dict:
    """Analisis segmen VTT dengan fuzzy regex. Sama dengan cs20_engine."""
    base = {
        "video_id":    video_id, "channel": channel,
        "status":      "no_match", "status_label": "⬜ Tidak Ada Indikasi",
        "hits": [], "score": 0, "persentase": 0,
        "tier_counts": {t: 0 for t in COMPILED_TIERS},
        "cluster_count": 0, "maraton_mins": 0,
        "is_valid": False, "kasta": "ZONK", "kasta_label": "💀 ZONK",
        "html_rows": "",
    }
    if not segments:
        return base

    full_text = " ".join(s.get("text", "") for s in segments)
    if not ALL_PATTERNS.search(full_text):
        return base

    HIT_LIST  = []
    LAST_TEXT = ""
    LAST_SEC  = -1
    tier_counts = {t: 0 for t in COMPILED_TIERS}

    def _classify(text):
        res = {t: 0 for t in COMPILED_TIERS}
        for tname, tdata in COMPILED_TIERS.items():
            pats = tdata.get("patterns", [])
            for pat in pats:
                if pat.search(text):
                    res[tname] += 1
        return res

    for seg in segments:
        text = seg.get("text", "").strip()
        sec  = int(seg.get("sec", 0))
        if not text or text == LAST_TEXT:
            continue
        if not ALL_PATTERNS.search(text):
            continue
        if abs(sec - LAST_SEC) < 1:
            continue
        hit_tiers = _classify(text)
        if not any(v > 0 for v in hit_tiers.values()):
            continue
        for t, cnt in hit_tiers.items():
            if cnt:
                tier_counts[t] += 1
        HIT_LIST.append({
            "sec": sec, "time": _sec_to_hms(sec), "text": text,
            "tiers": hit_tiers, "url": f"https://youtu.be/{video_id}?t={sec}",
        })
        LAST_TEXT = text
        LAST_SEC  = sec

    if not HIT_LIST:
        return base

    # Cluster
    total_dur = (HIT_LIST[-1]["sec"] - HIT_LIST[0]["sec"]) // 60 if HIT_LIST else 0
    CLUSTER_GAP = 60*60 if total_dur > 180 else (30*60 if total_dur > 60 else 20*60)
    clusters, cur = [], []
    for hit in HIT_LIST:
        if not cur:
            cur = [hit]
        elif hit["sec"] - cur[-1]["sec"] >= CLUSTER_GAP:
            clusters.append(cur); cur = [hit]
        else:
            cur.append(hit)
    if cur:
        clusters.append(cur)

    # Scoring
    CORE_HITS    = tier_counts.get("CORE", 0)
    GLOBAL_SCORE = 0
    MARATON_MINS = 0
    VALID_CLUSTERS = 0
    for cl in clusters:
        c_dur = max(1, (cl[-1]["sec"] - cl[0]["sec"]) // 60)
        c_core   = sum(1 for h in cl if h["tiers"].get("CORE"))
        c_typo   = sum(1 for h in cl if h["tiers"].get("TYPO"))
        c_silent = sum(1 for h in cl if h["tiers"].get("SILENT"))
        c_ctx    = sum(1 for h in cl if h["tiers"].get("CONTEXT"))
        c_fp     = sum(1 for h in cl if h["tiers"].get("FP"))
        c_base   = c_core*5 + c_typo*4 + c_silent*4 + c_ctx*2 + c_fp
        GLOBAL_SCORE += c_base + min(10, (len(cl)//c_dur)*2) + (15 if c_silent else 0)
        if c_dur > MARATON_MINS: MARATON_MINS = c_dur
        if c_core >= 2: VALID_CLUSTERS += 1
    cwc = sum(1 for cl in clusters if any(h["tiers"].get("CORE") for h in cl))
    if cwc > 1: GLOBAL_SCORE += 20 * (cwc - 1)
    PERSENTASE = min(100, (GLOBAL_SCORE * 100) // 60)

    IS_MARATON = len(clusters) == 1 and MARATON_MINS >= 30 and GLOBAL_SCORE >= 8
    IS_MULTI   = VALID_CLUSTERS >= 2
    HAS_SILENT = tier_counts.get("SILENT", 0) > 0

    kasta = "ZONK"; kasta_label = "💀 ZONK"; is_valid = False
    if CORE_HITS == 0:
        PERSENTASE = min(PERSENTASE, 15); kasta = "AMBIGU"
        kasta_label = "⚠️ AMBIGU — Indikasi Lemah"
    elif IS_MARATON and PERSENTASE >= 60:
        PERSENTASE = 100; kasta = "GOD_MODE"; is_valid = True
        kasta_label = f"👑 GOD MODE — MARATON {MARATON_MINS} MENIT"
    elif IS_MULTI and PERSENTASE >= 60:
        kasta = "VALID_HIGH"; is_valid = True
        kasta_label = f"🔥 VALID HIGH — {VALID_CLUSTERS} SESI"
    elif HAS_SILENT and CORE_HITS >= 1:
        PERSENTASE = max(PERSENTASE, 75); kasta = "SILENT"; is_valid = True
        kasta_label = "🤫 VALID — SILENT TREATMENT"
    elif CORE_HITS >= 3 and PERSENTASE >= 60:
        kasta = "VALID_HIGH"; is_valid = True; kasta_label = "✅ VALID HIGH"
    elif CORE_HITS >= 1 and PERSENTASE >= 40:
        kasta = "VALID"; is_valid = True; kasta_label = "✅ VALID"
    elif CORE_HITS >= 1:
        kasta = "LOW"; kasta_label = "📋 LOW INDICATOR"
    else:
        PERSENTASE = min(PERSENTASE, 15); kasta = "AMBIGU"; kasta_label = "⚠️ AMBIGU"

    kasta_label += f" | {len(clusters)} cluster, {len(HIT_LIST)} hit"

    # HTML rows
    html_rows = ""
    for hit in HIT_LIST:
        safe_text = hit["text"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        if hit["tiers"].get("SILENT"):   hl = "silent"
        elif hit["tiers"].get("CORE"):   hl = "core"
        elif hit["tiers"].get("TYPO"):   hl = "typo"
        elif hit["tiers"].get("CONTEXT"): hl = "ctx"
        else: hl = ""
        ts = ""
        if hit["tiers"].get("CORE"):    ts += "<span class='tc core'>CORE</span> "
        if hit["tiers"].get("TYPO"):    ts += "<span class='tc typo'>TYPO</span> "
        if hit["tiers"].get("SILENT"):  ts += "<span class='tc silent'>SILENT</span> "
        if hit["tiers"].get("CONTEXT"): ts += "<span class='tc ctx'>CTX</span> "
        html_rows += (
            f"<tr><td><a href='{hit['url']}' target='_blank' class='t-link'>"
            f"[{hit['time']}]</a></td><td>{ts}{safe_text}</td></tr>\n"
        )

    base.update({
        "status": "analyzed", "status_label": kasta_label,
        "hits": HIT_LIST, "score": GLOBAL_SCORE, "persentase": PERSENTASE,
        "tier_counts": tier_counts, "cluster_count": len(clusters),
        "maraton_mins": MARATON_MINS, "is_valid": is_valid,
        "kasta": kasta, "kasta_label": kasta_label, "html_rows": html_rows,
    })
    return base

# ==============================================================================
# RICH DASHBOARD
# ==============================================================================
def _make_dashboard(channel: str, session_label: str) -> Table:
    s   = _stats
    pct = f"{s['done']*100//s['total']}%" if s["total"] else "0%"
    eta = _fmt_eta(s["done"], s["total"], s["start_time"])

    phase_label = (
        "[bold red]⚡ BYPASS[/bold red]"
        if s["phase"] == "bypass"
        else "[bold green]🔍 ANALISIS[/bold green]"
    )

    tbl = Table(
        box=rbox.SIMPLE_HEAVY,
        show_header=False,
        width=min(_TERM_WIDTH - 2, 62),
        pad_edge=True,
    )
    tbl.add_column("k", style="dim",       width=20)
    tbl.add_column("v", style="bold white", width=38)

    # Header blok
    tbl.add_row(
        "[bold red]🔞 AGE BYPASS[/bold red]",
        f"[cyan]@{channel}[/cyan]"
    )
    tbl.add_row("Sesi",   f"[dim]{session_label}[/dim]")
    tbl.add_row("Fase",   phase_label)
    tbl.add_row(
        "Progress",
        f"[white]{s['done']}[/white] / {s['total']}  [cyan]{pct}[/cyan]"
    )
    tbl.add_row("ETA",    f"[yellow]{eta}[/yellow]")
    tbl.add_row("", "")

    # Bypass stats
    tbl.add_row("[dim]── Bypass ──[/dim]", "")
    tbl.add_row(
        "[green]Berhasil[/green]",
        f"[green]{s['ok']}[/green]"
        f"    [dim]no_sub[/dim]  [yellow]{s['no_sub']}[/yellow]"
    )
    tbl.add_row(
        "[dim]masih_blokir[/dim]",
        f"[red]{s['still_blocked']}[/red]"
        f"   [dim]unavail[/dim]  [orange1]{s['unavailable']}[/orange1]"
    )
    tbl.add_row(
        "[dim]ratelimit[/dim]",
        f"[red]{s['rate_limited']}[/red]"
        f"   [dim]network[/dim]  [yellow]{s['network']}[/yellow]"
    )
    tbl.add_row(
        "[dim]error[/dim]",
        f"[red]{s['error']}[/red]"
    )
    tbl.add_row("", "")

    # Analysis stats
    tbl.add_row("[dim]── Analisis ──[/dim]", "")
    if s["phase"] == "analysis":
        tbl.add_row(
            "[green]Valid[/green]",
            f"[green]{s['an_valid']} video[/green]"
        )
        tbl.add_row(
            "[magenta]Hits[/magenta]",
            f"[magenta]{s['an_hits']} momen[/magenta]"
        )
    else:
        tbl.add_row("[dim]menunggu bypass...[/dim]", "")

    return tbl

def _status_icon(status: str) -> str:
    return {
        "ok":            "[green]✓[/green]",
        "no_sub":        "[dim]—[/dim]",
        "unavailable":   "[red]✗[/red]",
        "still_blocked": "[red]🔞[/red]",
        "rate_limited":  "[yellow]⏸[/yellow]",
        "network_error": "[yellow]![/yellow]",
        "error":         "[red]![/red]",
    }.get(status, "[dim]?[/dim]")

# ==============================================================================
# MAIN PROCESS — BYPASS PHASE
# ==============================================================================
def run_bypass_phase(
    video_ids:    list,
    channel:      str,
    cookies_path: str,
    lang:         str,
    jobs:         int,
    session_dir:  str,
    log_path:     str,
    session_label: str,
) -> list:
    """
    Download subtitle untuk semua video_ids via yt-dlp + cookies.
    Return list of {video_id, status, vtt_path, ...}
    """
    global _stats

    vtt_dir = os.path.join(session_dir, "vtt_tmp")
    os.makedirs(vtt_dir, exist_ok=True)

    with _stats_lock:
        _stats["phase"]      = "bypass"
        _stats["done"]       = 0
        _stats["total"]      = len(video_ids)
        _stats["start_time"] = time.time()
        _stats["_jobs"]      = jobs

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold red]{task.description}"),
        BarColumn(bar_width=_BAR_WIDTH),
        MofNCompleteColumn(),
        TextColumn("[cyan]{task.percentage:>5.1f}%"),
        TimeRemainingColumn(),
        console=_console, transient=False,
    )
    task = progress.add_task(f"BYPASS @{channel}", total=len(video_ids))

    layout = Layout()
    layout.split_column(
        Layout(name="dash",     ratio=5),
        Layout(name="progress", ratio=1),
    )
    layout["dash"].update(_make_dashboard(channel, session_label))
    layout["progress"].update(progress)

    bypass_results   = []
    consecutive_rl   = 0

    def _worker(vid_id: str) -> dict:
        # Delay adaptif: makin banyak rate limit, makin panjang delay
        rl_now = _stats.get("rate_limited", 0)
        base   = 2.0 + min(rl_now * 0.3, 8.0)  # naik bertahap, max +8 detik
        time.sleep(random.uniform(base, base + 2.5))
        dl = _download_subtitle_with_cookies(vid_id, cookies_path, lang, vtt_dir)
        dl["video_id"] = vid_id
        return dl

    with Live(layout, console=_console, refresh_per_second=4, transient=False) as live:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_worker, vid): vid for vid in video_ids}

            for future in as_completed(futures):
                res    = future.result()
                vid_id = res["video_id"]
                status = res["status"]

                with _stats_lock:
                    _stats["done"] += 1
                    if status == "ok":
                        _stats["ok"] += 1; consecutive_rl = 0
                    elif status == "no_sub":
                        _stats["no_sub"] += 1; consecutive_rl = 0
                    elif status == "still_blocked":
                        _stats["still_blocked"] += 1; consecutive_rl = 0
                        _append_failed_log(log_path, vid_id,
                                           "still_blocked_after_bypass", channel, lang)
                    elif status == "unavailable":
                        _stats["unavailable"] += 1; consecutive_rl = 0
                    elif status == "rate_limited":
                        _stats["rate_limited"] += 1; consecutive_rl += 1
                        _append_failed_log(log_path, vid_id, "rate_limited", channel, lang)
                    elif status == "network_error":
                        _stats["network"] += 1; consecutive_rl = 0
                        _append_failed_log(log_path, vid_id,
                                           f"network:{res.get('error_type','')}", channel, lang)
                    else:
                        _stats["error"] += 1; consecutive_rl = 0
                        _append_failed_log(log_path, vid_id,
                                           f"error:{res.get('error_type','')}", channel, lang)

                bypass_results.append(res)

                # Log per baris via progress console (tidak ganggu Live)
                icon = _status_icon(status)
                lbl  = status[:30]
                progress.console.print(
                    f"  {icon} [dim]{vid_id}[/dim] [dim]{lbl}[/dim]"
                )

                progress.update(task, advance=1)
                layout["dash"].update(_make_dashboard(channel, session_label))
                layout["progress"].update(progress)
                live.refresh()

                if JSON_MODE:
                    _emit_json("progress", {
                        "phase": "bypass", "channel": channel,
                        "done": _stats["done"], "total": _stats["total"],
                        "status": status,
                    })

                # Rate limit guard: 12 berturut-turut → cooldown dulu, bukan shutdown
                if consecutive_rl >= 12:
                    safe_print(
                        f"\n[yellow][⚠️] {consecutive_rl}x rate limit berturut-turut "
                        f"— cooldown 90 detik sebelum lanjut...[/yellow]"
                    )
                    time.sleep(90)
                    consecutive_rl = 0  # reset setelah cooldown, lanjut proses

    return bypass_results

# ==============================================================================
# MAIN PROCESS — ANALYSIS PHASE
# ==============================================================================
def run_analysis_phase(
    bypass_results: list,
    channel:        str,
    lang:           str,
    session_dir:    str,
    session_label:  str,
) -> list:
    """Analisis VTT yang berhasil di-download."""
    global _stats

    ok_results = [r for r in bypass_results if r["status"] == "ok" and r.get("vtt_path")]
    if not ok_results:
        safe_print("[yellow]  Tidak ada subtitle berhasil didownload untuk dianalisis.[/yellow]")
        return []

    COMPILED_TIERS, ALL_PATTERNS, use_fuzzy = _load_fuzzy_engine(lang)

    with _stats_lock:
        _stats["phase"]      = "analysis"
        _stats["done"]       = 0
        _stats["total"]      = len(ok_results)
        _stats["start_time"] = time.time()

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        BarColumn(bar_width=_BAR_WIDTH),
        MofNCompleteColumn(),
        TextColumn("[green]{task.percentage:>5.1f}%"),
        TimeRemainingColumn(),
        TextColumn("[yellow]Valid:{task.fields[valid]}"),
        console=_console, transient=False,
    )
    task = progress.add_task("ANALISIS", total=len(ok_results), valid=0)

    final_results = []

    with progress:
        for res in ok_results:
            vid_id   = res["video_id"]
            vtt_path = res["vtt_path"]

            segments = []
            try:
                with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
                    vtt_text = f.read()
                segments = _parse_vtt(vtt_text)
            except Exception:
                pass

            result = _analyze_segments(vid_id, channel, segments,
                                        COMPILED_TIERS, ALL_PATTERNS)
            result["title"] = _VIDEO_TITLES.get(vid_id, vid_id)
            final_results.append(result)

            with _stats_lock:
                _stats["done"] += 1
                if result.get("is_valid"):
                    _stats["an_valid"] += 1
                    _stats["an_hits"]  += len(result.get("hits", []))

            progress.update(task, advance=1, valid=_stats["an_valid"])

            if JSON_MODE:
                _emit_json("progress", {
                    "phase": "analysis", "channel": channel,
                    "done": _stats["done"], "total": _stats["total"],
                })
                if result.get("hits"):
                    _emit_json("match", {
                        "channel": channel,
                        "video_id": vid_id,
                        "title": result["title"],
                        "tier_counts": result.get("tier_counts"),
                        "persentase": result.get("persentase"),
                        "hits": [
                            {"time": h["time"], "text": h["text"], "tiers": h["tiers"], "url": h["url"]}
                            for h in result["hits"]
                        ],
                    })

            # Hapus VTT setelah diproses
            try:
                os.remove(vtt_path)
            except Exception:
                pass

    return final_results

# ==============================================================================
# HTML BUILD (reuse cs20_engine.build_html)
# ==============================================================================
def _build_html(channel: str, executor: str, results: list, lang: str,
                session_label: str) -> str:
    try:
        from cs20_engine import build_html as _bh
        return _bh(channel, f"{executor} [AGE-BYPASS {session_label}]", results, lang)
    except ImportError:
        pass

    valid  = [r for r in results if r.get("is_valid")]
    now_s  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cards  = ""
    for r in sorted(results, key=lambda x: x.get("persentase", 0), reverse=True):
        if r.get("html_rows"):
            cards += (
                f"<div style='border:1px solid #1a3a5c;margin:10px 0;padding:10px'>"
                f"<a href='https://youtu.be/{r['video_id']}' style='color:#4fc3f7'>"
                f"{r['video_id']}</a> "
                f"<span style='color:#ffc107'>{r.get('persentase',0)}%</span>"
                f"<p style='color:#8ab0c8'>{r.get('kasta_label','')}</p>"
                f"<table style='width:100%;border-collapse:collapse'>"
                f"{r['html_rows']}</table></div>"
            )
    return (
        f"<!DOCTYPE html><html><head><meta charset='UTF-8'>"
        f"<title>CS20 Age Report — @{channel}</title>"
        f"<style>body{{background:#080c10;color:#8ab0c8;font-family:monospace}}"
        f".t-link{{color:#00ff88}}.tc{{padding:1px 4px;font-size:.7em}}"
        f".tc.core{{color:#ff3c3c}}</style></head><body>"
        f"<h2 style='color:#ff3c3c'>🔞 AGE BYPASS — @{channel} [{session_label}]</h2>"
        f"<p>Valid: {len(valid)} / {len(results)} | {now_s}</p>"
        f"{cards}</body></html>"
    )

# ==============================================================================
# DISCORD (reuse cs20_engine.send_discord)
# ==============================================================================
def _send_discord(webhook_url: str, channel: str, executor: str,
                  results: list, html_path: str, session_label: str):
    try:
        from cs20_engine import send_discord as _sd
        _sd(webhook_url, channel,
            f"{executor} [AGE-BYPASS {session_label}]", results, html_path)
        return
    except ImportError:
        pass

    if not webhook_url:
        return
    try:
        import requests as rq
        valid_count = sum(1 for r in results if r.get("is_valid"))
        payload     = {"embeds": [{"title": f"🔞 AGE BYPASS — @{channel}",
                                    "color": 16711680,
                                    "description": f"Sesi: {session_label}\n"
                                                   f"Valid: {valid_count}/{len(results)}"}]}
        rq.post(webhook_url, json=payload, timeout=30)
        if os.path.exists(html_path):
            sz = os.path.getsize(html_path) / (1024*1024)
            if sz <= 7.5:
                with open(html_path, "rb") as f:
                    rq.post(webhook_url,
                            data={"payload_json": json.dumps({"content": f"📄 @{channel} age report:"})},
                            files={"file": (os.path.basename(html_path), f, "text/html")},
                            timeout=60)
    except Exception:
        pass

# ==============================================================================
# COOKIES CHECK UI — dipanggil saat masuk mode
# ==============================================================================
def phase_check_cookies(cookies_path: str, test_video_id: str | None) -> bool:
    """
    Tampilkan proses validasi cookies secara bertahap dengan Rich.
    Return True jika cookies valid & siap dipakai.
    """
    safe_print("")
    safe_print(Panel(
        "[bold red]🔞 RECOVERY — AGE RESTRICTED BYPASS[/bold red]\n"
        "[dim]Validasi cookies sebelum proses dimulai...[/dim]",
        border_style="red",
        width=min(_TERM_WIDTH - 2, 60),
    ))

    # ── Step 1: File ada? ──────────────────────────────────────────
    safe_print("\n[dim]▶ Step 1/3 — Cek keberadaan cookies.txt...[/dim]")
    if not _check_cookies_exist(cookies_path):
        safe_print(Panel(
            f"[red]cookies.txt tidak ditemukan di:[/red]\n"
            f"[yellow]{cookies_path}[/yellow]\n\n"
            "[white]Jalankan script berikut untuk setup:[/white]\n"
            "[bold cyan]bash setup_cookies.sh[/bold cyan]",
            title="[red]❌ COOKIES TIDAK ADA[/red]",
            border_style="red",
            width=min(_TERM_WIDTH - 2, 60),
        ))
        return False
    safe_print(f"  [green]✓[/green] Ditemukan: [dim]{cookies_path}[/dim]")

    # ── Step 2: Parse & validasi Netscape ─────────────────────────
    safe_print("[dim]▶ Step 2/3 — Validasi format & expiry (Netscape)...[/dim]")
    netscape_result = _validate_cookies_netscape(cookies_path)
    if not netscape_result["valid"]:
        safe_print(Panel(
            f"[red]{netscape_result['reason']}[/red]\n\n"
            "[white]Cookies sudah expired atau format salah.[/white]\n"
            "[bold cyan]bash setup_cookies.sh[/bold cyan]",
            title="[red]❌ COOKIES TIDAK VALID[/red]",
            border_style="red",
            width=min(_TERM_WIDTH - 2, 60),
        ))
        return False
    safe_print(
        f"  [green]✓[/green] {netscape_result['reason']}"
        + (f" [yellow]({netscape_result['expired_count']} expired)[/yellow]"
           if netscape_result["expired_count"] else "")
    )

    # ── Step 3: Test live ──────────────────────────────────────────
    safe_print("[dim]▶ Step 3/3 — Test live via yt-dlp...[/dim]")
    if not test_video_id:
        safe_print("  [yellow]⚠ Tidak ada video ID untuk test live. Lewati.[/yellow]")
        safe_print(Panel(
            "[green]Cookies terlihat valid berdasarkan format Netscape.[/green]\n"
            "[dim]Test live dilewati (tidak ada video referensi).[/dim]",
            title="[yellow]⚠ COOKIES BELUM DITEST LIVE[/yellow]",
            border_style="yellow",
            width=min(_TERM_WIDTH - 2, 60),
        ))
        return True  # Lanjut dengan peringatan

    safe_print(f"  [dim]Menguji ke video: {test_video_id} ...[/dim]")
    live_result = _test_cookies_live(cookies_path, test_video_id)
    if not live_result["ok"]:
        safe_print(Panel(
            f"[red]{live_result['msg']}[/red]\n\n"
            "[white]Cookies perlu diperbarui.[/white]\n"
            "[bold cyan]bash setup_cookies.sh[/bold cyan]",
            title="[red]❌ TEST LIVE GAGAL[/red]",
            border_style="red",
            width=min(_TERM_WIDTH - 2, 60),
        ))
        return False

    safe_print(
        f"  [green]✓[/green] {live_result['msg']}"
    )
    safe_print("")
    safe_print(Panel(
        "[bold green]Semua validasi berhasil — siap bypass![/bold green]",
        border_style="green",
        width=min(_TERM_WIDTH - 2, 60),
    ))
    return True

# ==============================================================================
# INPUT CHANNEL & VIDEO IDS
# ==============================================================================
# Map video_id -> title, diisi dari _fetch_ids_flat (gratis, sekali per channel)
_VIDEO_TITLES: dict = {}

def _get_video_ids_from_channel(channel: str, content_type: str, limit: int) -> list:
    """Ambil video IDs dari channel via yt-dlp."""
    if content_type == "live":
        url       = f"https://www.youtube.com/@{channel}/streams"
        extra     = []
    elif content_type == "video":
        url       = f"https://www.youtube.com/@{channel}/videos"
        extra     = ["--match-filter", "duration>60"]
    else:
        ids_l = _fetch_ids_flat(f"https://www.youtube.com/@{channel}/streams", limit, [])
        ids_v = _fetch_ids_flat(f"https://www.youtube.com/@{channel}/videos", limit,
                                ["--match-filter", "duration>60"])
        seen, combined = set(), []
        for v in ids_l + ids_v:
            if v not in seen:
                seen.add(v); combined.append(v)
        return combined[:limit]
    return _fetch_ids_flat(url, limit, extra)

def _fetch_ids_flat(url: str, limit: int, extra: list) -> list:
    cmd = ["yt-dlp", "--flat-playlist", "--print", "%(id)s|||%(title)s",
           "--playlist-end", str(limit), *extra,
           "--quiet", "--no-warnings", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        seen, uniq = set(), []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||", 1)
            vid   = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else vid
            if vid and vid not in seen:
                seen.add(vid)
                uniq.append(vid)
                _VIDEO_TITLES[vid] = title
        return uniq[:limit]
    except Exception:
        return []

# ==============================================================================
# MAIN PROCESS ENTRY
# ==============================================================================
def process_age_mode(args):
    global _cleanup_done, _partial_results, _current_log_path

    channel      = args.channel
    lang         = args.lang
    executor     = args.executor
    webhook_url  = args.webhook_url
    config_dir   = args.config_dir
    cookies_path = os.path.join(config_dir, "cookies.txt")
    content_type = args.content_type
    limit        = args.limit
    jobs         = min(max(1, args.jobs), 4)  # 1–4 workers, default 2

    # ── SIGINT handler ───────────────────────────────────────────────
    def _on_sigint(sig, frame):
        safe_print("\n[yellow][⚠️] Ctrl+C — menyimpan log dan keluar...[/yellow]")
        sys.exit(0)
    signal.signal(signal.SIGINT, _on_sigint)

    # ──────────────────────────────────────────────────────────────────
    # 1. TENTUKAN VIDEO IDS — dari log atau channel input
    # ──────────────────────────────────────────────────────────────────
    video_ids       = []
    source_label    = ""
    test_video_id   = None   # untuk test cookies live

    # Cek pending log dari engine lain
    pending = _load_pending_log(channel)

    # Cek sesi lama dengan log yang masih punya video gagal
    old_sessions = _list_sessions(channel)
    old_failed_sessions = []
    for sd in old_sessions:
        lp = _log_path(sd, channel)
        if os.path.exists(lp):
            d = _read_log(lp)
            if d.get("videos"):
                old_failed_sessions.append((sd, lp, d))

    has_pending = pending is not None
    has_old     = len(old_failed_sessions) > 0

    if (has_pending or has_old) and not JSON_MODE:
        safe_print("")
        safe_print(Panel(
            "[bold yellow]Log video age-restricted ditemukan![/bold yellow]",
            border_style="yellow",
            width=min(_TERM_WIDTH - 2, 60),
        ))

        options = []
        if has_pending:
            pcount = len(pending["videos"])
            safe_print(
                f"  [cyan]P.[/cyan] Pending log dari engine lain "
                f"([yellow]{pcount} video[/yellow])"
            )
            options.append("P")
        for i, (sd, lp, d) in enumerate(old_failed_sessions[:5], 1):
            sname = os.path.basename(sd)
            vcount = len(d["videos"])
            safe_print(
                f"  [cyan]{i}.[/cyan] Sesi [dim]{sname}[/dim] "
                f"— [red]{vcount} video gagal[/red]"
            )
            options.append(str(i))
        safe_print(f"  [dim]N.[/dim] Mulai sesi baru dari @{channel}")
        safe_print("")

        choice = _console.input(
            "  [bold cyan]➤ Pilihan:[/bold cyan] "
        ).strip().upper()

        if choice == "P" and has_pending:
            video_ids   = [v["id"] for v in pending["videos"]]
            test_video_id = video_ids[0] if video_ids else None
            source_label  = f"pending_log ({len(video_ids)} video)"
            # Hapus pending log setelah diambil
            pending_path = os.path.join(_age_log_root(channel), "pending_log.json")
            try:
                os.remove(pending_path)
            except Exception:
                pass
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(old_failed_sessions):
                sd, lp, d = old_failed_sessions[idx]
                video_ids     = [v["id"] for v in d["videos"]]
                test_video_id = video_ids[0] if video_ids else None
                source_label  = f"retry sesi {os.path.basename(sd)} ({len(video_ids)} video)"
                # Hapus log lama supaya sesi retry bersih (akan dibuat ulang jika masih gagal)
                try:
                    os.remove(lp)
                except Exception:
                    pass
        else:
            # Mulai sesi baru — ambil dari channel
            video_ids = []

    # Jika belum ada video_ids (sesi baru atau tidak pilih log)
    if not video_ids:
        safe_print("")
        safe_print(Rule("[bold red]🔞 SESI BARU — AGE BYPASS[/bold red]", style="red"))
        safe_print("")

        # Input channel jika belum ada
        if not channel:
            if JSON_MODE:
                safe_print("[red]  Channel wajib diisi (--channel) di mode UI.[/red]")
                return
            ch_input = _console.input(
                "  [bold cyan]➤ Username channel (tanpa @):[/bold cyan] "
            ).strip().lstrip("@")
            if not ch_input:
                safe_print("[red]  Channel tidak boleh kosong.[/red]")
                return
            channel = ch_input

        if JSON_MODE:
            # Semua parameter sudah datang dari args (--content-type, --limit, --jobs),
            # tidak perlu tanya interaktif.
            pass
        else:
            # Input tipe konten
            safe_print("")
            safe_print("  [white]Tipe konten:[/white]")
            safe_print("     [cyan]1.[/cyan] 🔴 Arsip Live Stream")
            safe_print("     [cyan]2.[/cyan] 🎬 Video biasa")
            safe_print("     [cyan]3.[/cyan] 🌐 Semua (kecuali Shorts)")
            ct_in = _console.input("  [bold cyan]➤ Pilihan (1/2/3):[/bold cyan] ").strip()
            content_type = {"1": "live", "2": "video", "3": "all"}.get(ct_in, "all")

            # Input jumlah video
            safe_print("")
            lim_in = _console.input(
                "  [bold cyan]➤ Berapa video terakhir yang diambil? (default 50):[/bold cyan] "
            ).strip()
            try:
                limit = int(lim_in)
                if limit <= 0:
                    raise ValueError
            except ValueError:
                limit = 50

            # Input jobs
            safe_print("")
            safe_print("  [white]Mode kecepatan:[/white]")
            safe_print("     [cyan]1.[/cyan] 🐢 AMAN    — 1 worker  [dim](cookies paling aman)[/dim]")
            safe_print("     [cyan]2.[/cyan] ⚖️  BALANCE — 2 worker  [dim](default)[/dim]")
            safe_print("     [cyan]3.[/cyan] 🚀 TURBO   — 3 worker  [dim](cookies stabil)[/dim]")
            safe_print("     [cyan]4.[/cyan] ⚡ MAX     — 4 worker  [dim](risiko rate limit tinggi)[/dim]")
            j_in = _console.input("  [bold cyan]➤ Pilihan (1/2/3/4):[/bold cyan] ").strip()
            jobs = {"1": 1, "2": 2, "3": 3, "4": 4}.get(j_in, 2)

        safe_print("")
        safe_print(f"  [dim]Mengambil video dari @{channel}...[/dim]")
        video_ids = _get_video_ids_from_channel(channel, content_type, limit)

        if not video_ids:
            safe_print(f"[red]  Tidak ada video ditemukan untuk @{channel}.[/red]")
            return

        test_video_id = video_ids[0]
        source_label  = f"@{channel} ({len(video_ids)} video)"

    # ──────────────────────────────────────────────────────────────────
    # 2. VALIDASI COOKIES
    # ──────────────────────────────────────────────────────────────────
    cookies_ok = phase_check_cookies(cookies_path, test_video_id)
    if not cookies_ok:
        return

    # ──────────────────────────────────────────────────────────────────
    # 3. RINGKASAN & KONFIRMASI
    # ──────────────────────────────────────────────────────────────────
    safe_print("")
    safe_print(Panel(
        f"[white]👤 Eksekutor    :[/white] [bold]{executor}[/bold]\n"
        f"[white]📺 Target       :[/white] [cyan]@{channel}[/cyan]\n"
        f"[white]📹 Video        :[/white] {len(video_ids)} video\n"
        f"[white]🔑 Sumber       :[/white] [dim]{source_label}[/dim]\n"
        f"[white]🌐 Bahasa       :[/white] {lang}\n"
        f"[white]⚡ Workers      :[/white] {jobs}",
        title="[bold red]📋 RINGKASAN AGE BYPASS[/bold red]",
        border_style="red",
        width=min(_TERM_WIDTH - 2, 60),
    ))

    if JSON_MODE:
        _emit_json("phase", {"phase": "confirmed", "channel": channel, "total": len(video_ids)})
    else:
        konfirm = _console.input(
            "\n  [bold]➤ Mulai bypass? (y/n):[/bold] "
        ).strip().lower()
        if konfirm not in ("y", "ya", "yes"):
            safe_print("[yellow]  Dibatalkan.[/yellow]")
            return

    # ──────────────────────────────────────────────────────────────────
    # 4. BUAT SESI & LOG
    # ──────────────────────────────────────────────────────────────────
    session_dir   = _new_session_dir(channel)
    session_label = os.path.basename(session_dir)
    log_path      = _log_path(session_dir, channel)
    _current_log_path = log_path
    _init_log(log_path, channel, lang)

    safe_print(f"\n[dim]  Sesi: {session_dir}[/dim]\n")

    # ──────────────────────────────────────────────────────────────────
    # 5. BYPASS PHASE
    # ──────────────────────────────────────────────────────────────────
    safe_print(Rule("[bold red]⚡ BYPASS PHASE[/bold red]", style="red"))
    bypass_results = run_bypass_phase(
        video_ids, channel, cookies_path, lang,
        jobs, session_dir, log_path, session_label,
    )

    # ──────────────────────────────────────────────────────────────────
    # 6. ANALYSIS PHASE
    # ──────────────────────────────────────────────────────────────────
    safe_print("")
    safe_print(Rule("[bold green]🔍 ANALYSIS PHASE[/bold green]", style="green"))
    final_results = run_analysis_phase(
        bypass_results, channel, lang, session_dir, session_label,
    )

    # ──────────────────────────────────────────────────────────────────
    # 7. SUMMARY
    # ──────────────────────────────────────────────────────────────────
    elapsed    = time.time() - _stats["start_time"]
    valid_c    = _stats["an_valid"]
    hits_total = _stats["an_hits"]
    ok_c       = _stats["ok"]
    still_bl   = _stats["still_blocked"]

    safe_print("")
    safe_print(Panel(
        f"[white]📊 {len(video_ids)} video diproses\n"
        f"⚡ {ok_c} subtitle berhasil didownload\n"
        f"🔒 {still_bl} masih terblokir (tersimpan ke log)\n"
        f"✅ {valid_c} video valid ditemukan\n"
        f"🎯 {hits_total} total momen terdeteksi\n"
        f"⏱️  Waktu: {int(elapsed//60)}m {int(elapsed%60)}s[/white]",
        title=f"[bold green]🏁 SELESAI — @{channel}[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))

    # Cek log sisa (yang masih gagal)
    failed_log_data = _read_log(log_path)
    sisa_gagal      = len(failed_log_data.get("videos", []))
    if sisa_gagal > 0:
        safe_print(
            f"\n[yellow]⚠  {sisa_gagal} video masih gagal — tersimpan di log:[/yellow]\n"
            f"[dim]   {log_path}[/dim]\n"
            f"[dim]   Bisa di-retry dari menu Recovery AgeRestricted kapan saja.[/dim]"
        )
    else:
        # Log kosong — hapus
        try:
            os.remove(log_path)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────
    # 8. BUILD HTML & KIRIM DISCORD
    # ──────────────────────────────────────────────────────────────────
    if final_results:
        html_content = _build_html(channel, executor, final_results, lang, session_label)
        ts           = datetime.now().strftime("%d%m%Y_%H%M")
        html_path    = os.path.join(
            config_dir,
            f"age_{channel}_{ts}.html"
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        _send_discord(webhook_url, channel, executor,
                      final_results, html_path, session_label)
        if JSON_MODE:
            _emit_json("report_sent", {"channel": channel, "html_path": html_path})

        if os.path.exists(html_path):
            sz = os.path.getsize(html_path) / (1024 * 1024)
            if sz <= 7.5:
                try:
                    os.remove(html_path)
                except Exception:
                    pass
            else:
                safe_print(f"[yellow][📁] HTML disimpan: {html_path}[/yellow]")
    else:
        safe_print("[dim]  Tidak ada hasil analisis untuk dikirim.[/dim]")

    # Cleanup VTT tmp
    vtt_dir = os.path.join(session_dir, "vtt_tmp")
    if os.path.isdir(vtt_dir):
        try:
            shutil.rmtree(vtt_dir)
        except Exception:
            pass

    gc.collect()

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CS20 Age Restricted Bypass Engine")
    parser.add_argument("--channel",       required=True)
    parser.add_argument("--lang",          default="id")
    parser.add_argument("--executor",      default="Unknown")
    parser.add_argument("--webhook-url",   default="")
    parser.add_argument("--config-dir",    default=".cs20")
    parser.add_argument("--content-type",  default="all")
    parser.add_argument("--limit",         type=int, default=50)
    parser.add_argument("--jobs",          type=int, default=2)
    parser.add_argument("--json-events", action="store_true",
                         help="Output JSON event per baris ke stdout untuk UI (Streamlit)")

    args = parser.parse_args()
    if args.json_events:
        JSON_MODE = True
    process_age_mode(args)