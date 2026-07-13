#!/usr/bin/env python3
# ==============================================================================
# 👑 CEGUKAN SEEKER V20.0 — PYTHON ENGINE
# Handles: Transcript fetch, Fuzzy regex analysis, Scoring, HTML, Discord
# ==============================================================================

import argparse
import json
import os
import re
import sys
import time
import random
import subprocess
import threading
import urllib.request
import urllib.error
import gc
import signal
import atexit
import glob
import shutil

# ── DETEKSI LEBAR TERMINAL ────────────────────────────────────────
_TERM_WIDTH   = shutil.get_terminal_size(fallback=(42, 24)).columns
_COMPACT_MODE = _TERM_WIDTH < 50   # portrait HP sempit
_BAR_WIDTH    = max(10, min(30, _TERM_WIDTH - 30))  # lebar bar dinamis

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Hook age-restricted log (opsional, tidak crash jika engine tidak ada) ──
try:
    from cs20_age_engine import hook_log_age_restricted as _hook_age
    _AGE_HOOK_AVAILABLE = True
except ImportError:
    _AGE_HOOK_AVAILABLE = False
    def _hook_age(*a, **kw): pass

try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )
    
except ImportError as e:
    print(f"[❌] Gagal memuat modul: {e}")
    print("     Jalankan: pip install youtube-transcript-api --break-system-packages")

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, MofNCompleteColumn
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.table import Table
    from rich.layout import Layout
    from rich import box as rbox
except ImportError:
    print("[❌] Install rich dulu: pip install rich --break-system-packages")
    sys.exit(1)

# ==============================================================================
# JSON EVENTS (untuk Web UI / Streamlit) — konsisten dengan cs20_index_engine.py
# dan cs20_age_engine.py. Saat --json-events aktif, engine print event JSON
# per baris (prefix "CS20JSON:") ke stdout, TANPA mengubah perilaku CLI biasa.
# ==============================================================================
JSON_MODE: bool = False

def _emit_json(event_type: str, payload: dict):
    line = {"type": event_type, **payload}
    print("CS20JSON:" + json.dumps(line, ensure_ascii=False), flush=True)

# Cache judul video, diisi gratis bareng fetch ID (yt-dlp --print id|||title)
_VIDEO_TITLES: dict = {}

# ==============================================================================
# WARNA TERMINAL
# ==============================================================================
class C:
    R  = '\033[0;31m'
    GR = '\033[0;32m'
    Y  = '\033[0;33m'
    B  = '\033[0;34m'
    M  = '\033[0;35m'
    CY = '\033[0;36m'
    W  = '\033[1;37m'
    DIM= '\033[2m'
    BD = '\033[1m'
    NC = '\033[0m'

def c(color, text): return f"{color}{text}{C.NC}"

# ==============================================================================
# FUZZY KEYWORD DICTIONARY — SEMUA BAHASA
# ==============================================================================
_ALL_KEYWORD_TIERS = {
    "id": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"ce+g+[uo]+k+[ae]+n+",
                r"ce+g+[uo]+k+[ae]+n+nya",
                r"ce+g+[uo]+k+[ae]+n+ku",
                r"ce+c+e+g+[uo]+k+[ae]+n+",
                r"ce+k+[uo]+k+[ae]+n+",
                r"ce+k+[uo]+k+[ae]+n+nya",
                r"ce+k+[uo]+k+[ae]+n+ku",
                r"je+g+[uo]+k+[ae]+n+",
                r"ke+je+g+[uo]+k+[ae]+n+",
                r"ke+ce+g+[uo]+k+[ae]+n*",
                r"ce+g+[uo]+k+e+n+",
                r"ce+k+[uo]+k+e+n+",
                r"\*hi+k+\*",
                r"\*hi+c+\*",
                r"\*ngi+k+\*",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"aduh\s*c[uo]+k+[ae]*n+",
                r"duh\s*c[uo]+k+[ae]*n+",
                r"kok\s*c[uo]+k+[ae]*n+",
                r"lagi\s*c[uo]+k+[ae]*n+",
                r"masih\s*c[uo]+k+[ae]*n+",
                r"\bcu+k+[uo]+k+[ae]+n+\b",
                r"(?<!se)se+g+[uo]+k+[ae]*n+\b",
                r"\bce+g+[uo]+[ae]*n+\b",
                r"\bce+k+[uo]+[ae]*n+\b",
                r"\bce+g+[uo]+k+\s+[ae]*n+\b",
                r"\bju+g+[uo]+k+[ae]*n+\b",
                r"\bce+k+[uo]+g+[ae]*n+\b",
                r"\bce+g+[uo]+g+[ae]*n+\b",
                r"\bje+g+[uo]+g+[ae]*n+\b",
                r"\bje+k+[uo]+g+[ae]*n+\b",
                r"\bc+e*b+u+k+a+n+\b",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"ce+g+[uo]+k+[ae]*n+\s*(dari\s*tadi|terus|mulu|melulu|lagi)",
                r"(ga|gak|tidak|nggak)\s*(ilang|hilang)\s*[\w\s]*ce+g+[uo]+k+[ae]*n*",
                r"ce+g+[uo]+k+[ae]*n+\s*(ga|gak|nggak)\s*(ilang|hilang)",
                r"capek\s*ce+g+[uo]+k+[ae]*n*",
                r"ce+g+[uo]+k+[ae]*n+\s*ga\s*ilang",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"te+rs+e+d+[ae]+k+",
                r"ke+s+e+d+[ae]+k+",
                r"(?<![a-zA-Z])ce+g+[uo]+k+(?![a-zA-Z])",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"ny+e+nd+[ao]+w+[ao]*",
                r"se+nd+[ao]+w+[ao]*",
                r"\bhi+k+\b",
                r"\bse+se+g+[uo]+k+[ae]*n+\b",
            ]
        }
    },
    "en": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"\bhiccup+s?\b",
                r"\bhiccu+p+s?\b",
                r"\*hic\*",
                r"\bhic+\b",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"\bhicup+s?\b",
                r"\bhickup+s?\b",
                r"\bh[ie]ccup+s?\b",
                r"\bhic\s+cup+s?\b",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"hiccup+s?\s*(won'?t|can'?t|don'?t|not)\s*(stop|go away)",
                r"(can'?t|won'?t)\s*(stop|get rid of)\s*(the\s*)?hiccup",
                r"hiccup+s?\s*(for|like)\s*(an?\s*)?(hour|minute|while)",
                r"still\s*(have|got)\s*(the\s*)?hiccup",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"\bhiccough+s?\b",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"(economic|minor|small|little|technical|temporary)\s*hiccup+s?",
                r"hiccup+s?\s*(in|with|for)\s*(the|our|my|their)\s*\w+",
            ]
        }
    },
    "jp": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"しゃっくり",
                r"シャックリ",
                r"シャッくり",
                r"しゃッくり",
                r"吃逆",
                r"しゃっ\s*くり",
                r"シャッ\s*クリ",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"しゃくり",
                r"シャクリ",
                r"ひゃっくり",
                r"ヒャックリ",
                r"ヒック",
                r"ひっく",
                r"吃\s*逆",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"しゃっくりが止まら",
                r"シャックリが止まら",
                r"しゃっくり.*止まらない",
                r"しゃっくり.*続く",
                r"しゃっくり.*止め",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"(?!)",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"びっくり",
                r"ビックリ",
            ]
        }
    },
    "kr": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"딸꾹질",
                r"딸꾹",
                r"딸각",
                r"딸깍",
                r"딸구질",
                r"딸국",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"\[딸꾹\]",
                r"\[딸깍\]",
                r"사레",
                r"사레들",
                r"캑캑",
                r"컥컥",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"딸꾹질이 안",
                r"딸꾹질 계속",
                r"딸꾹질 멈추",
                r"멈추질 않",
                r"딸꾹질 때문에",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"트림",
                r"거억",
                r"꺼억",
                r"끄억",
                r"\[트림\]",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"(?!)",
            ]
        }
    },
    "in": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"हिचकी",
                r"\bhiccup\b",
                r"\bhichki\b",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"इचकी",
                r"हिचकि",
                r"\bhicup\b",
                r"\bh[ae]cup\b",
                r"\bhichky\b",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"(?!)",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"हिचकियाँ",
                r"हिचकिया",
                r"हिचकीं",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"(?!)",
            ]
        }
    },

    "te": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"ఎక్కిళ్లు",
                r"ఎక్కిళ్ళు",
                r"ఎక్కిలి",
                r"\bekkillu\b",
                r"\bekkili\b",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"ఎకిళ్లు",
                r"ఎకిళ్ళు",
                r"\bekilu\b",
                r"\bekkilu\b",
                r"హిచ్కి",
                r"హిచ్‌కి",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"(?!)",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"ఎక్కిళ్లతో",
                r"త్రేనుపు",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"(?!)",
            ]
        }
    },
    "th": {
        "CORE": {
            "bobot": 5,
            "patterns": [
                r"สะอึก",
                r"อาการสะอึก",
            ]
        },
        "TYPO": {
            "bobot": 4,
            "patterns": [
                r"สอึก",
                r"สะอิก",
            ]
        },
        "SILENT": {
            "bobot": 4,
            "patterns": [
                r"(?!)",
            ]
        },
        "CONTEXT": {
            "bobot": 2,
            "patterns": [
                r"สำลัก",
                r"เรอ",
            ]
        },
        "FP": {
            "bobot": 1,
            "patterns": [
                r"(?!)",
            ]
        }
    },
}

# Placeholder global — akan diisi oleh _init_lang() saat process_channel
KEYWORD_TIERS       = {}
COMPILED_TIERS      = {}
ALL_PATTERNS_COMBINED = re.compile(r"(?!)")  # dummy, diganti saat runtime

def _init_lang(lang: str):
    """Inisialisasi KEYWORD_TIERS, COMPILED_TIERS, ALL_PATTERNS_COMBINED, dan TRANSCRIPT_LANGS."""
    global KEYWORD_TIERS, COMPILED_TIERS, ALL_PATTERNS_COMBINED, TRANSCRIPT_LANGS

    _LANG_TRANSCRIPT_MAP = {
        "id": ["id", "en", "id-ID"],
        "en": ["en", "en-US", "en-GB"],
        "jp": ["ja", "ja-JP"],
        "kr": ["ko", "ko-KR"],
        "in": ["hi", "hi-IN"],
        "te": ["te", "en"],
        "th": ["th", "en"],
    }
    TRANSCRIPT_LANGS = _LANG_TRANSCRIPT_MAP.get(lang, ["id", "en", "id-ID"])

    KEYWORD_TIERS = _ALL_KEYWORD_TIERS.get(lang, _ALL_KEYWORD_TIERS["id"])

    compiled = {}
    for tier_name, tier_data in KEYWORD_TIERS.items():
        c_list = []
        for pat in tier_data["patterns"]:
            try:
                c_list.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                pass
        compiled[tier_name] = {
            "bobot":    tier_data["bobot"],
            "patterns": c_list,
        }
    COMPILED_TIERS = compiled

    valid_pats = [
        pat for tier_data in KEYWORD_TIERS.values()
        for pat in tier_data["patterns"]
        if pat != r"(?!)"
    ]
    ALL_PATTERNS_COMBINED = re.compile(
        "|".join(valid_pats) if valid_pats else r"(?!)",
        re.IGNORECASE
    )

# ==============================================================================
# AMBIL VIDEO ID LIST VIA YT-DLP
# ==============================================================================
def get_video_ids(channel: str, limit: int, content_type: str) -> list:
    """Ambil list video ID dari channel via yt-dlp."""

    # Tentukan URL tab + filter yang tepat per tipe konten
    # was_live tidak tersedia saat --flat-playlist, jadi pakai tab yang benar
    if content_type == "live":
        # Tab /streams = khusus arsip live, tidak perlu filter tambahan
        url        = f"https://www.youtube.com/@{channel}/streams"
        extra_args = []
    elif content_type == "video":
        # Tab /videos = video biasa, filter durasi >60 detik (exclude shorts)
        url        = f"https://www.youtube.com/@{channel}/videos"
        extra_args = ["--match-filter", "duration>60"]
    else:
        # "all" = gabung /streams + /videos, filter durasi >60 detik
        # Jalankan dua fetch terpisah lalu gabungkan
        ids_live  = _fetch_ids(f"https://www.youtube.com/@{channel}/streams", limit, [])
        ids_video = _fetch_ids(f"https://www.youtube.com/@{channel}/videos",  limit, ["--match-filter", "duration>60"])
        # Gabung, deduplikasi, batasi limit
        seen, combined = set(), []
        for vid in ids_live + ids_video:
            if vid not in seen:
                seen.add(vid)
                combined.append(vid)
        return combined[:limit]

    return _fetch_ids(url, limit, extra_args)


def _fetch_ids(url: str, limit: int, extra_args: list) -> list:
    """Helper: jalankan yt-dlp flat-playlist dan return list ID.
    Sekalian ambil judul (format id|||title) dalam satu request yang sama —
    tidak menambah request/rate-limit risk. Judul di-cache ke _VIDEO_TITLES.
    """
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(id)s|||%(title)s",
        "--playlist-end", str(limit),
        *extra_args,
        "--quiet",
        "--no-warnings",
        url
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        seen, unique = set(), []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|||", 1)
            vid = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else ""
            if not vid:
                continue
            if title:
                _VIDEO_TITLES[vid] = title
            if vid not in seen:
                seen.add(vid)
                unique.append(vid)
        return unique[:limit]
    except subprocess.TimeoutExpired:
        return []
    except Exception:
        return []

# ==============================================================================
# COOKIES PATH GLOBAL — diisi saat process_channel dipanggil
# ==============================================================================
COOKIES_PATH: str = ""
TRANSCRIPT_LANGS: list = ["id", "en", "id-ID"]  # default ID, diisi ulang oleh _init_lang

# Rotasi User-Agent — fallback kalau tidak ada cookies
_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; POCO X4 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Xiaomi 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
_ua_index = 0
_ua_lock  = threading.Lock()

def _next_user_agent() -> str:
    global _ua_index
    with _ua_lock:
        ua = _USER_AGENTS[_ua_index % len(_USER_AGENTS)]
        _ua_index += 1
    return ua

def _build_session_with_cookies(cookies_path: str) -> "requests.Session":
    """
    Load Netscape cookies.txt ke requests.Session.
    Kompatibel dengan youtube-transcript-api >= 0.6 (http_client= param).
    """
    import requests, http.cookiejar, time
    session = requests.Session()
    session.headers.update({"User-Agent": _next_user_agent()})
    try:
        jar = http.cookiejar.MozillaCookieJar(cookies_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        now = int(time.time())
        for ck in jar:
            # Skip cookies yang sudah expired (kecuali expiry=0 = session cookie)
            if ck.expires and ck.expires < now:
                continue
            session.cookies.set(ck.name, ck.value, domain=ck.domain, path=ck.path)
    except Exception:
        pass  # gagal load cookies → session tetap jalan tanpa cookies
    return session

def _fetch_transcript(video_id: str) -> list:
    """
    Fetch transkrip dengan cookies jika tersedia,
    fallback ke rotasi User-Agent untuk mengurangi risiko rate limit.
    """
    langs = TRANSCRIPT_LANGS

    # ── Dengan cookies (load Netscape → Session → http_client) ──────
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        try:
            session = _build_session_with_cookies(COOKIES_PATH)
            api = YouTubeTranscriptApi(http_client=session)
            result = api.fetch(video_id, languages=langs)
            return result.to_raw_data() if hasattr(result, "to_raw_data") else list(result)
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
            raise
        except Exception:
            pass  # fallback ke bawah

    # ── Tanpa cookies — rotasi User-Agent ───────────────────────────
    try:
        import requests
        session = requests.Session()
        session.headers.update({"User-Agent": _next_user_agent()})
        api = YouTubeTranscriptApi(http_client=session)
        result = api.fetch(video_id, languages=langs)
        return result.to_raw_data() if hasattr(result, "to_raw_data") else list(result)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
        raise
    except Exception:
        pass

    # ── Fallback paling dasar ────────────────────────────────────────
    api = YouTubeTranscriptApi()
    result = api.fetch(video_id, languages=langs)
    return result.to_raw_data() if hasattr(result, "to_raw_data") else list(result)

# ==============================================================================
# ANALISIS SATU VIDEO
# ==============================================================================
def classify_text(text: str) -> dict:
    """Klasifikasikan teks ke tier-tier keyword."""
    result = {tier: 0 for tier in COMPILED_TIERS}

    for tier_name, tier_data in COMPILED_TIERS.items():
        for pat in tier_data["patterns"]:
            if pat.search(text):
                result[tier_name] += 1

    return result

def sec_to_hms(sec: int) -> str:
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def analyze_video(video_id: str, channel: str) -> dict:
    """Fetch transkrip dan analisis. Return dict hasil."""

    base_result = {
        "video_id": video_id,
        "channel": channel,
        "title": _VIDEO_TITLES.get(video_id, video_id),
        "status": "no_transcript",
        "status_label": "🚫 Tanpa Transkrip",
        "hits": [],
        "score": 0,
        "persentase": 0,
        "tier_counts": {t: 0 for t in COMPILED_TIERS},
        "cluster_count": 0,
        "maraton_mins": 0,
        "is_valid": False,
        "kasta": "ZONK",
        "kasta_label": "💀 ZONK",
        "html_rows": ""
    }

    # ── FETCH TRANSKRIP ──────────────────────────────────────────────
    try:
        segments = _fetch_transcript(video_id)
        base_result["status"] = "ok"

    except NoTranscriptFound:
        base_result["status"] = "no_transcript"
        base_result["status_label"] = "🚫 Tidak Ada Transkrip untuk Bahasa ini"
        return base_result

    except TranscriptsDisabled:
        base_result["status"] = "disabled"
        base_result["status_label"] = "🔒 Transkrip Dinonaktifkan oleh Channel"
        return base_result

    except VideoUnavailable:
        base_result["status"] = "unavailable"
        base_result["status_label"] = "💀 Video Tidak Tersedia (privat/dihapus/geoblocked)"
        return base_result

    except Exception as e:
        err   = str(e)
        etype = type(e).__name__

        if etype == "NoTranscriptAvailable":
            base_result["status"] = "no_transcript"
            base_result["status_label"] = "📭 Tidak Ada Transkrip Sama Sekali"
            return base_result

        elif etype in ("TranscriptsAgeRestricted", "AgeRestricted"):
            base_result["status"] = "age_restricted"
            base_result["status_label"] = "🔞 Age Restricted"
            # Hook: catat ke log age_restricted supaya bisa di-bypass nanti
            _hook_age(
                config_dir=_emergency_info.get("config_dir", ".cs20"),
                channel=channel,
                video_id=video_id,
                lang=_emergency_info.get("lang", "id"),
            )
            return base_result

        elif etype in ("IpBlocked", "RequestBlocked"):
            _append_blocked_log(_current_blocked_log, video_id, etype)
            base_result["status"] = "blocked"
            base_result["status_label"] = f"🚫 Diblokir YouTube ({etype})"
            return base_result

        elif "429" in err or "too many requests" in err.lower() or "ratelimit" in err.lower() or "rate_limit" in err.lower():
            jeda = random.uniform(20, 35)
            time.sleep(jeda)
            try:
                segments = _fetch_transcript(video_id)
                base_result["status"] = "ok"
            except Exception as e2:
                err2   = str(e2)
                etype2 = type(e2).__name__
                _append_blocked_log(
                    _current_blocked_log, video_id,
                    f"rate_limit_retry_failed:{etype2}:{err2[:120]}"
                )
                base_result["status"] = "rate_limited"
                base_result["status_label"] = "⏸️ Rate Limited (retry gagal)"
                return base_result

        elif etype == "VideoUnplayable":
            base_result["status"] = "no_sub"
            base_result["status_label"] = "🔒 Member Only / No Transcript"
            return base_result

        elif any(kw in err.lower() for kw in ("timed out", "timeout", "connection", "network", "ssl", "remotedisconnected", "broken pipe")):
            _append_blocked_log(
                _current_blocked_log, video_id,
                f"network_error:{etype}:{err[:120]}"
            )
            base_result["status"] = "error"
            base_result["status_label"] = f"🌐 Network Error ({etype})"
            return base_result

        else:
            _append_blocked_log(
                _current_blocked_log, video_id,
                f"unknown:{etype}:{err[:120]}"
            )
            base_result["status"] = "error"
            base_result["status_label"] = f"⚠️ Unknown Error ({etype})"
            return base_result

    # ── PRE-CHECK CEPAT ─────────────────────────────────────────────
    full_text = " ".join(seg.get("text", "") for seg in segments)
    if not ALL_PATTERNS_COMBINED.search(full_text):
        # Tidak ada keyword sama sekali — skip cepat
        base_result["status"] = "no_match"
        base_result["status_label"] = "⬜ Tidak Ada Indikasi"
        return base_result

    # ── ANALISIS PER SEGMEN ─────────────────────────────────────────
    HIT_LIST = []
    LAST_TEXT = ""
    LAST_SEC  = -1
    tier_counts = {t: 0 for t in COMPILED_TIERS}

    for seg in segments:
        text = seg.get("text", "").strip()
        start_sec = int(seg.get("start", 0))

        if not text or text == LAST_TEXT:
            continue

        # Cek keyword
        if not ALL_PATTERNS_COMBINED.search(text):
            continue

        # Dedup by time (dalam 1 detik yang sama)
        if abs(start_sec - LAST_SEC) < 1:
            continue

        # Klasifikasi tier
        hit_tiers = classify_text(text)
        has_hit = any(v > 0 for v in hit_tiers.values())
        if not has_hit:
            continue

        # Update counter
        for tier, count in hit_tiers.items():
            if count > 0:
                tier_counts[tier] += 1

        HIT_LIST.append({
            "sec":   start_sec,
            "time":  sec_to_hms(start_sec),
            "text":  text,
            "tiers": hit_tiers,
            "url":   f"https://youtu.be/{video_id}?t={start_sec}"
        })

        LAST_TEXT = text
        LAST_SEC  = start_sec

    if not HIT_LIST:
        base_result["status"] = "no_match"
        base_result["status_label"] = "⬜ Tidak Ada Indikasi"
        return base_result

    # ── CLUSTER ANALYSIS ────────────────────────────────────────────
    # Gap dinamis berdasarkan durasi video — makin panjang, makin besar gap
    total_duration_min = (HIT_LIST[-1]["sec"] - HIT_LIST[0]["sec"]) // 60 if HIT_LIST else 0
    if total_duration_min > 180:      # > 3 jam → gap 60 menit
        CLUSTER_GAP = 60 * 60
    elif total_duration_min > 60:     # 1-3 jam → gap 30 menit
        CLUSTER_GAP = 30 * 60
    else:                             # < 1 jam → gap 20 menit
        CLUSTER_GAP = 20 * 60

    clusters = []
    current_cluster = []

    for hit in HIT_LIST:
        if not current_cluster:
            current_cluster = [hit]
        elif hit["sec"] - current_cluster[-1]["sec"] >= CLUSTER_GAP:
            clusters.append(current_cluster)
            current_cluster = [hit]
        else:
            current_cluster.append(hit)
    if current_cluster:
        clusters.append(current_cluster)

    # ── SCORING V20 ─────────────────────────────────────────────────
    # CORE hits wajib ada untuk kasta di atas AMBIGU
    CORE_HITS = tier_counts.get("CORE", 0)
    SCORE_CAP = 60
    GLOBAL_SCORE = 0
    MARATON_MINS = 0
    VALID_CLUSTERS = 0  # cluster dengan >= 2 hits CORE

    for cluster in clusters:
        c_hits = len(cluster)
        c_dur_sec = cluster[-1]["sec"] - cluster[0]["sec"]
        c_dur_min = max(1, c_dur_sec // 60)

        c_core   = sum(1 for h in cluster if h["tiers"].get("CORE", 0) > 0)
        c_typo   = sum(1 for h in cluster if h["tiers"].get("TYPO", 0) > 0)
        c_silent = sum(1 for h in cluster if h["tiers"].get("SILENT", 0) > 0)
        c_ctx    = sum(1 for h in cluster if h["tiers"].get("CONTEXT", 0) > 0)
        c_fp     = sum(1 for h in cluster if h["tiers"].get("FP", 0) > 0)

        c_base = (c_core * 5) + (c_typo * 4) + (c_silent * 4) + (c_ctx * 2) + (c_fp * 1)

        c_density_bonus = min(10, (c_hits // c_dur_min) * 2)
        c_silent_bonus  = 15 if c_silent > 0 else 0

        c_total = c_base + c_density_bonus + c_silent_bonus
        GLOBAL_SCORE += c_total

        if c_dur_min > MARATON_MINS:
            MARATON_MINS = c_dur_min
        # Cluster valid hanya kalau ada >= 2 hit CORE (bukan sembarang hit)
        if c_core >= 2:
            VALID_CLUSTERS += 1

    # Multisesi bonus hanya kalau masing-masing cluster punya CORE hit
    clusters_with_core = sum(
        1 for cl in clusters
        if any(h["tiers"].get("CORE", 0) > 0 for h in cl)
    )
    if clusters_with_core > 1:
        GLOBAL_SCORE += 20 * (clusters_with_core - 1)

    PERSENTASE = min(100, (GLOBAL_SCORE * 100) // SCORE_CAP)

    # ── KASTA CLASSIFICATION V20 ────────────────────────────────────
    IS_MARATON   = (len(clusters) == 1 and MARATON_MINS >= 30 and GLOBAL_SCORE >= 8)
    IS_MULTISESI = (VALID_CLUSTERS >= 2)
    HAS_SILENT   = tier_counts.get("SILENT", 0) > 0

    kasta = "ZONK"
    kasta_label = "💀 ZONK"
    is_valid = False

    if CORE_HITS == 0:
        # Gate utama: 0 hit CORE = tidak bisa valid apapun skornya
        # Reset persentase ke nilai rendah agar tidak menyesatkan
        PERSENTASE = min(PERSENTASE, 15)
        kasta = "AMBIGU"
        kasta_label = "⚠️ AMBIGU — Indikasi Lemah (0 Hit Core)"

    elif IS_MARATON and PERSENTASE >= 60:
        PERSENTASE = 100
        kasta = "GOD_MODE"
        kasta_label = f"👑 GOD MODE — MARATON {MARATON_MINS} MENIT NON-STOP"
        is_valid = True

    elif IS_MULTISESI and PERSENTASE >= 60:
        kasta = "VALID_HIGH"
        kasta_label = f"🔥 VALID HIGH — {VALID_CLUSTERS} SESI KAMBUHAN"
        is_valid = True

    elif HAS_SILENT and CORE_HITS >= 1:
        PERSENTASE = max(PERSENTASE, 75)
        kasta = "SILENT"
        kasta_label = "🤫 VALID — SILENT TREATMENT DETECTED"
        is_valid = True

    elif CORE_HITS >= 3 and PERSENTASE >= 60:
        kasta = "VALID_HIGH"
        kasta_label = "✅ VALID HIGH"
        is_valid = True

    elif CORE_HITS >= 1 and PERSENTASE >= 40:
        kasta = "VALID"
        kasta_label = "✅ VALID"
        is_valid = True

    elif CORE_HITS >= 1:
        kasta = "LOW"
        kasta_label = "📋 LOW INDICATOR"

    else:
        PERSENTASE = min(PERSENTASE, 15)
        kasta = "AMBIGU"
        kasta_label = "⚠️ AMBIGU"

    # Tambahkan info cluster ke label
    kasta_label += f" | {len(clusters)} cluster, {len(HIT_LIST)} hit"

    # ── BUILD HTML ROWS ─────────────────────────────────────────────
    html_rows = ""
    for hit in HIT_LIST:
        safe_text = (hit["text"]
                     .replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;"))

        # Tentukan kelas highlight dominan per baris (prioritas: SILENT > CORE > TYPO > CTX)
        if hit["tiers"].get("SILENT", 0):
            hl_class = "silent"
        elif hit["tiers"].get("CORE", 0):
            hl_class = "core"
        elif hit["tiers"].get("TYPO", 0):
            hl_class = "typo"
        elif hit["tiers"].get("CONTEXT", 0):
            hl_class = "ctx"
        else:
            hl_class = ""

        # Highlight teks menggunakan regex pattern yang cocok
        highlighted_text = safe_text
        if hl_class:
            for tier_name, tier_data in COMPILED_TIERS.items():
                tier_hl = {
                    "CORE":    "core",
                    "TYPO":    "typo",
                    "SILENT":  "silent",
                    "CONTEXT": "ctx",
                }.get(tier_name, "")
                if not tier_hl:
                    continue
                for pat in tier_data["patterns"]:
                    highlighted_text = pat.sub(
                        lambda m, cls=tier_hl: f"<span class='hl {cls}'>{m.group(0)}</span>",
                        highlighted_text
                    )

        # Tier badge strip (kotak kecil kiri kolom)
        tier_strip = ""
        if hit["tiers"].get("CORE", 0):    tier_strip += f"<span class='tc core'>CORE</span> "
        if hit["tiers"].get("TYPO", 0):    tier_strip += f"<span class='tc typo'>TYPO</span> "
        if hit["tiers"].get("SILENT", 0):  tier_strip += f"<span class='tc silent'>SILENT</span> "
        if hit["tiers"].get("CONTEXT", 0): tier_strip += f"<span class='tc ctx'>CTX</span> "

        html_rows += (
            f"<tr>"
            f"<td><a href='{hit['url']}' target='_blank' class='t-link'>[{hit['time']}]</a></td>"
            f"<td>{tier_strip}{highlighted_text}</td>"
            f"</tr>\n"
        )
     
    base_result.update({
        "status": "analyzed",
        "status_label": kasta_label,
        "hits": HIT_LIST,
        "score": GLOBAL_SCORE,
        "persentase": PERSENTASE,
        "tier_counts": tier_counts,
        "cluster_count": len(clusters),
        "maraton_mins": MARATON_MINS,
        "is_valid": is_valid,
        "kasta": kasta,
        "kasta_label": kasta_label,
        "html_rows": html_rows
    })
    del segments
    del full_text
    del HIT_LIST
    return base_result

def _update_stats_from_result(result: dict):
    """Update _stats counters berdasarkan hasil satu video. Dipanggil dari semua run_display_*."""
    status = result.get("status", "")
    if result.get("hits"):
        _stats["hits_total"] += len(result["hits"])
    if result.get("is_valid"):
        _stats["valid_count"] += 1

    # Error counters
    if status == "blocked":
        _stats["err_blocked"] += 1
    elif status == "age_restricted":
        _stats["err_age"] += 1
    elif status == "rate_limited":
        _stats["err_ratelimit"] += 1
        _stats["rate_limit_count"] += 1
    elif status == "error":
        sl = result.get("status_label", "")
        if "Network" in sl or "network" in sl:
            _stats["err_timeout"] += 1
        else:
            _stats["err_unknown"] += 1

    # Tier counters live
    for tier in ("CORE", "TYPO", "SILENT", "CONTEXT", "FP"):
        cnt = result.get("tier_counts", {}).get(tier, 0)
        if cnt:
            _stats[f"tier_{tier}"] += cnt

# ==============================================================================
# DISPLAY FUNCTIONS — rich edition
# ==============================================================================
_console     = Console()
_print_lock  = threading.Lock()
_hype_events = []
_stats = {
    "done": 0, "total": 0,
    "hits_total": 0, "valid_count": 0,
    "rate_limit_count": 0,
    "start_time": 0,
    # ── Error counters ──
    "err_blocked":   0,
    "err_age":       0,
    "err_ratelimit": 0,
    "err_timeout":   0,
    "err_unknown":   0,
    # ── Tier counters live ──
    "tier_CORE":    0,
    "tier_TYPO":    0,
    "tier_SILENT":  0,
    "tier_CONTEXT": 0,
    "tier_FP":      0,
}

_partial_results   = []   # hasil sementara untuk laporan darurat
_emergency_info    = {
    "channel":     "",
    "executor":    "",
    "webhook_url": "",
    "config_dir":  "",
    "checkpoint_dir": "",
    "lang":        "",
}
_current_blocked_log = ""  # path log blocked aktif, diisi saat process_channel

_current_mode    = "semi"
_live_instance   = None   # rich Live object — dipakai mode pantau
_progress_instance = None # rich Progress object — dipakai mode semi & tidur

# ── Progress bar columns ──────────────────────────────────────────
def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold green]{task.description}"),
        BarColumn(bar_width=max(10, min(30, _BAR_WIDTH))),
        MofNCompleteColumn(),
        TextColumn("[cyan]{task.percentage:>5.1f}%"),
        TimeRemainingColumn(),
        TextColumn("[yellow]H:{task.fields[hits]}"),
        console=_console,
        transient=False,
    )

def safe_print(*args, **kwargs):
    """Print thread-safe via rich console."""
    with _print_lock:
        _console.print(*args, **kwargs)

def format_eta(done, total, start_time):
    if done == 0: return "menghitung..."
    elapsed  = time.time() - start_time
    rate     = done / elapsed
    remaining = (total - done) / rate if rate > 0 else 0
    mins = int(remaining // 60)
    secs = int(remaining % 60)
    return f"~{mins}m {secs}s"

def draw_progress_bar(done, total, width=30):
    """Legacy — tidak dipakai rich mode, tapi dipertahankan agar tidak NameError."""
    if total == 0: return "[" + "░" * width + "] 0%"
    pct    = done / total
    filled = int(width * pct)
    bar    = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct*100:.1f}%"

# ── STATUS ICON ───────────────────────────────────────────────────
def _status_icon(status: str) -> str:
    return {
        "analyzed":       "[green]✓[/green]",
        "no_match":       "[dim]·[/dim]",
        "no_transcript":  "[red]✗[/red]",
        "disabled":       "[red]✗[/red]",
        "unavailable":    "[red]✗[/red]",
        "age_restricted": "[yellow]🔞[/yellow]",
        "rate_limited":   "[yellow]⏸[/yellow]",
        "error":          "[yellow]![/yellow]",
        "blocked":        "[red]🚫[/red]",
    }.get(status, "?")

# ── HYPE MOMENT ───────────────────────────────────────────────────
def print_hype(result):
    kasta = result.get("kasta", "")
    if kasta not in ("GOD_MODE", "VALID_HIGH"):
        return

    vid   = result["video_id"]
    label = result["kasta_label"]
    pct   = result["persentase"]

    if kasta == "GOD_MODE":
        border_style = "bold yellow"
        title_style  = "[bold yellow]★ GOD MODE[/bold yellow]"
    else:
        border_style = "bold magenta"
        title_style  = "[bold magenta]★ VALID HIGH[/bold magenta]"

    content = Text()
    content.append(f"{vid}\n", style="bold cyan")
    content.append(f"{label}\n", style="white")
    content.append(f"Score: {pct}%", style="bold green")

    panel = Panel(
        content,
        title=title_style,
        border_style=border_style,
        width=min(_TERM_WIDTH - 2, 56),
    )

    with _print_lock:
        _console.print(panel)

# ==============================================================================
# MODE TIDUR — log per video + progress bar bawah
# ==============================================================================
def run_display_tidur(channel, video_ids, worker_fn, checkpoint_info: dict, webhook_url: str = ""):
    global _stats

    results = []
    consecutive_errors = 0

    progress = _make_progress()
    task = progress.add_task(
        f"@{channel}",
        total=len(video_ids),
        hits=0,
    )

    _console.print(f"\n[cyan]▶ Memulai @{channel} — {len(video_ids)} video[/cyan]\n")

    with progress:
        with ThreadPoolExecutor(max_workers=_stats.get("_jobs", 4)) as pool:
            futures = {pool.submit(worker_fn, vid): vid for vid in video_ids}

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                _partial_results.append(result)

                _stats["done"] += 1
                done = _stats["done"]

                if result["status"] == "rate_limited":
                    consecutive_errors += 1
                elif result["status"] in ("error", "blocked"):
                    consecutive_errors = max(0, consecutive_errors - 1)
                else:
                    consecutive_errors = 0

                _update_stats_from_result(result)

                # Log per video
                icon     = _status_icon(result["status"])
                vid_id   = result["video_id"]
                lbl      = result["status_label"][:48]
                hits_str = f"[yellow]{len(result.get('hits', []))} hits[/yellow]" if result.get("hits") else ""
                progress.console.print(f"  {icon} [dim]{vid_id}[/dim] {lbl} {hits_str}")

                # Update progress bar
                progress.update(task, advance=1, hits=_stats["hits_total"])

                # Hype moment
                if result.get("kasta") in ("GOD_MODE", "VALID_HIGH"):
                    _hype_events.append(result)
                    print_hype(result)

                # Checkpoint setiap 10 video
                if done % 10 == 0:
                    save_checkpoint(
                        checkpoint_info["dir"],
                        checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done,
                        checkpoint_info["total"]
                    )

                # Rate limit emergency stop
                if consecutive_errors >= 10:
                    save_checkpoint(
                        checkpoint_info["dir"],
                        checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done,
                        checkpoint_info["total"]
                    )
                    handle_rate_limit(
                        webhook_url, channel, "engine",
                        checkpoint_info["start_from"] + done,
                        checkpoint_info["total"]
                    )
                    _kirim_laporan_darurat("rate limit darurat")
                    _console.print("[red]✗ Rate limit darurat — proses dihentikan.[/red]")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

    return results, consecutive_errors

# ==============================================================================
# MODE SEMI PANTAU — progress bar + valid box + error tally + tier counter
# ==============================================================================
def run_display_semi(channel, video_ids, worker_fn, checkpoint_info: dict, webhook_url: str = ""):
    global _stats

    results            = []
    consecutive_errors = 0
    _no_match_buffer   = []   # buffer ID no-match untuk di-group
    _err_tally = {
        "blocked": 0, "age_restricted": 0,
        "rate_limited": 0, "error": 0,
    }

    progress = _make_progress()
    task = progress.add_task(f"@{channel}", total=len(video_ids), hits=0)

    _console.print(f"\n[cyan]▶ @{channel} — {len(video_ids)} video[/cyan]")

    with progress:
        with ThreadPoolExecutor(max_workers=_stats.get("_jobs", 4)) as pool:
            futures = {pool.submit(worker_fn, vid): vid for vid in video_ids}

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                _partial_results.append(result)

                _stats["done"] += 1
                done   = _stats["done"]
                status = result["status"]
                vid_id = result["video_id"]
                kasta  = result.get("kasta", "")

                # ── Consecutive error tracker ──────────────────────
                if status == "rate_limited":
                    consecutive_errors += 1
                elif status in ("error", "blocked"):
                    consecutive_errors = max(0, consecutive_errors - 1)
                else:
                    consecutive_errors = 0

                # ── Update stats ───────────────────────────────────
                _update_stats_from_result(result)

                # ── Tally error per tipe ───────────────────────────
                if status in _err_tally:
                    _err_tally[status] += 1

                # ── Routing output per status ──────────────────────
                if status in ("no_match", "no_transcript", "disabled", "unavailable") \
                        or kasta in ("AMBIGU", "ZONK"):
                    _no_match_buffer.append(vid_id)
                    # Flush buffer tiap 6 video supaya tidak numpuk lama
                    if len(_no_match_buffer) >= 6:
                        ids_str = " · ".join(f"[dim]{v}[/dim]" for v in _no_match_buffer)
                        progress.console.print(f"  [dim]·[/dim] {ids_str} [dim](no match/no trans)[/dim]")
                        _no_match_buffer.clear()

                elif status == "analyzed":
                    # Semua kasta valid tampil box
                    hits_c = len(result.get("hits", []))
                    tc     = result.get("tier_counts", {})
                    tier_badges = ""
                    for t, label, col in [
                        ("SILENT",  "SILENT", "magenta"),
                        ("CORE",    "CORE",   "red"),
                        ("TYPO",    "TYPO",   "yellow"),
                        ("CONTEXT", "CTX",    "cyan"),
                    ]:
                        if tc.get(t, 0):
                            tier_badges += f"[{col}]{label}×{tc[t]}[/{col}] "

                    if kasta in ("GOD_MODE", "VALID_HIGH"):
                        box_color  = "yellow" if kasta == "GOD_MODE" else "magenta"
                        icon       = "★"
                    elif kasta == "SILENT":
                        box_color  = "magenta"
                        icon       = "★"
                    elif kasta in ("VALID", "VALID_HIGH"):
                        box_color  = "green"
                        icon       = "✓"
                    elif kasta == "LOW":
                        box_color  = "cyan"
                        icon       = "·"
                    else:
                        box_color  = "dim"
                        icon       = "·"

                    pct   = result.get("persentase", 0)
                    label = result.get("kasta_label", kasta)[:50]

                    if kasta not in ("AMBIGU", "ZONK"):
                        progress.console.print(
                            f"  [{box_color}]{icon}[/{box_color}] [cyan]{vid_id}[/cyan] "
                            f"[{box_color}]{pct}%[/{box_color}] {tier_badges}"
                        )
                        progress.console.print(Panel(
                            f"[bold cyan]{vid_id}[/bold cyan]\n"
                            f"[white]{label}[/white]\n"
                            f"Score: [{box_color}]{pct}%[/{box_color}]  Hits: [yellow]{hits_c}[/yellow]  {tier_badges}",
                            border_style=box_color,
                            width=min(_TERM_WIDTH - 2, 56),
                            padding=(0, 1),
                        ))

                else:
                    # error/blocked/rate_limited/age_restricted — silent (sudah masuk tally)
                    pass

                progress.update(task, advance=1, hits=_stats["hits_total"])

                # Hype moment — GOD_MODE dan VALID_HIGH saja
                if kasta in ("GOD_MODE", "VALID_HIGH"):
                    _hype_events.append(result)

                # ── Checkpoint setiap 10 video ─────────────────────
                if done % 10 == 0:
                    save_checkpoint(
                        checkpoint_info["dir"], checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )

                # ── Rate limit emergency stop ──────────────────────
                if consecutive_errors >= 10:
                    save_checkpoint(
                        checkpoint_info["dir"], checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )
                    handle_rate_limit(
                        webhook_url, channel, "engine",
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )
                    _kirim_laporan_darurat("rate limit darurat")
                    _console.print("[red]✗ Rate limit darurat.[/red]")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

        # ── Flush sisa buffer ──────────────────────────────────────
        if _no_match_buffer:
            ids_str = " · ".join(f"[dim]{v}[/dim]" for v in _no_match_buffer[:4])
            more    = f" [dim]+{len(_no_match_buffer)-4}[/dim]" if len(_no_match_buffer) > 4 else ""
            progress.console.print(f"  [dim]·[/dim] {ids_str}{more} [dim](no match/no trans)[/dim]")

        # ── Error summary baris tunggal ────────────────────────────
        err_parts = []
        if _err_tally["blocked"]:      err_parts.append(f"blocked×{_err_tally['blocked']}")
        if _err_tally["age_restricted"]:err_parts.append(f"age_restr×{_err_tally['age_restricted']}")
        if _err_tally["rate_limited"]: err_parts.append(f"ratelimit×{_err_tally['rate_limited']}")
        if _err_tally["error"]:        err_parts.append(f"error×{_err_tally['error']}")
        if err_parts:
            total_err = sum(_err_tally.values())
            _console.print(
                f"  [yellow]⚠ Errors [{total_err}]:[/yellow] "
                f"[orange1]{' | '.join(err_parts)}[/orange1]"
            )

        # ── Final tier summary ─────────────────────────────────────
        _console.print(
            f"  [dim]Tier final:[/dim] "
            f"[red]CORE {_stats['tier_CORE']}[/red] "
            f"[yellow]TYPO {_stats['tier_TYPO']}[/yellow] "
            f"[magenta]SILENT {_stats['tier_SILENT']}[/magenta] "
            f"[cyan]CTX {_stats['tier_CONTEXT']}[/cyan]"
        )

    return results, consecutive_errors

# ==============================================================================
# MODE PANTAU — live dashboard
# ==============================================================================
_parallel_status = {}
_parallel_lock   = threading.Lock()

def _make_dashboard(channel: str) -> Table:
    done  = _stats["done"]
    total = _stats["total"]
    hits  = _stats["hits_total"]
    valid = _stats["valid_count"]
    eta   = format_eta(done, total, _stats["start_time"])
    pct   = f"{done*100//total}%" if total else "0%"

    tbl = Table(
        box=rbox.SIMPLE_HEAVY,
        show_header=False,
        width=min(_TERM_WIDTH - 2, 56),
        pad_edge=True,
    )
    tbl.add_column("key", style="dim",       width=16)
    tbl.add_column("val", style="bold white", width=30)

    tbl.add_row("[bold green]Channel[/bold green]",  f"[cyan]@{channel}[/cyan]")
    tbl.add_row("Progress", f"[white]{done}[/white] / {total}  [cyan]{pct}[/cyan]")
    tbl.add_row("ETA",      f"[yellow]{eta}[/yellow]")
    tbl.add_row("", "")

    # Valid & hits
    tbl.add_row("Valid",      f"[green]{valid} video[/green]")
    tbl.add_row("Hits total", f"[magenta]{hits} momen[/magenta]")
    tbl.add_row("", "")

    # Tier hits live
    tier_str = (
        f"[red]CORE {_stats['tier_CORE']}[/red]  "
        f"[yellow]TYPO {_stats['tier_TYPO']}[/yellow]  "
        f"[magenta]SILENT {_stats['tier_SILENT']}[/magenta]  "
        f"[cyan]CTX {_stats['tier_CONTEXT']}[/cyan]"
    )
    tbl.add_row("Tier hits", tier_str)
    tbl.add_row("", "")

    # Error breakdown
    tbl.add_row("[dim]── Errors ──[/dim]", "")
    tbl.add_row(
        "[dim]blocked / IP[/dim]",
        f"[orange1]{_stats['err_blocked']}[/orange1]"
        + "    "
        + f"[dim]age restr[/dim]  [orange1]{_stats['err_age']}[/orange1]"
    )
    tbl.add_row(
        "[dim]no trans[/dim]",
        f"[yellow]{_stats.get('err_notrans', 0)}[/yellow]"
        + "       "
        + f"[dim]timeout[/dim]  [yellow]{_stats['err_timeout']}[/yellow]"
    )
    tbl.add_row(
        "[dim]ratelimit[/dim]",
        f"[yellow]{_stats['err_ratelimit']}[/yellow]"
        + "    "
        + f"[dim]unknown[/dim]   [yellow]{_stats['err_unknown']}[/yellow]"
    )
    tbl.add_row("", "")

    # Job slots
    tbl.add_row("[dim]── Jobs ──[/dim]", "")
    with _parallel_lock:
        active = list(_parallel_status.items())[-6:]

    for i in range(6):
        if i < len(active):
            vid, sts = active[i]
            if sts in ("GOD_MODE",):
                sts_str = f"[bold yellow]{sts}[/bold yellow]"
            elif sts in ("VALID_HIGH", "VALID", "SILENT"):
                sts_str = f"[green]{sts}[/green] [green]baru![/green]"
            elif sts in ("rate_limited", "error", "blocked"):
                sts_str = f"[orange1]{sts}[/orange1]"
            elif sts == "age_restricted":
                sts_str = f"[yellow]{sts}[/yellow]"
            else:
                sts_str = f"[dim]{sts}[/dim]"
            tbl.add_row(f"[cyan]{vid[:13]}[/cyan]", sts_str)
        else:
            tbl.add_row("", "")

    return tbl

def run_display_pantau(channel, video_ids, worker_fn, checkpoint_info: dict, webhook_url: str = ""):
    global _stats, _parallel_status

    results            = []
    consecutive_errors = 0
    _parallel_status   = {}

    progress = _make_progress()
    task = progress.add_task(f"@{channel}", total=len(video_ids), hits=0)

    layout = Layout()
    layout.split_column(
        Layout(name="dashboard", ratio=5),
        Layout(name="progress",  ratio=1),
    )
    layout["dashboard"].update(_make_dashboard(channel))
    layout["progress"].update(progress)

    live_active = True

    with Live(
        layout,
        console=_console,
        refresh_per_second=4,
        transient=False,
    ) as live:

        with ThreadPoolExecutor(max_workers=_stats.get("_jobs", 4)) as pool:
            futures = {pool.submit(worker_fn, vid): vid for vid in video_ids}

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                _partial_results.append(result)

                _stats["done"] += 1
                done   = _stats["done"]
                status = result["status"]
                kasta  = result.get("kasta", "")

                # ── Consecutive error tracker ──────────────────────
                if status == "rate_limited":
                    consecutive_errors += 1
                elif status in ("error", "blocked"):
                    consecutive_errors = max(0, consecutive_errors - 1)
                else:
                    consecutive_errors = 0

                # ── Update stats ───────────────────────────────────
                _update_stats_from_result(result)

                # ── Update job slot ────────────────────────────────
                with _parallel_lock:
                    _parallel_status[result["video_id"]] = (
                        kasta if kasta else status
                    )[:14]

                # ── Refresh dashboard ──────────────────────────────
                progress.update(task, advance=1, hits=_stats["hits_total"])
                layout["dashboard"].update(_make_dashboard(channel))
                layout["progress"].update(progress)
                live.refresh()

                if JSON_MODE:
                    _emit_json("progress", {
                        "phase": "pantau", "channel": channel,
                        "done": done, "total": _stats["total"],
                        "hits_total": _stats["hits_total"],
                    })
                    if result.get("hits"):
                        _emit_json("match", {
                            "channel": channel,
                            "video_id": result["video_id"],
                            "title": result.get("title", result["video_id"]),
                            "transcript_lang": result.get("transcript_lang", ""),
                            "tier_counts": result.get("tier_counts"),
                            "persentase": result.get("persentase"),
                            "hits": [
                                {"time": h["time"], "text": h["text"], "tiers": h["tiers"], "url": h["url"]}
                                for h in result["hits"]
                            ],
                        })

                # ── Hype moment ────────────────────────────────────
                if kasta in ("GOD_MODE", "VALID_HIGH"):
                    _hype_events.append(result)
                    if live_active:
                        live.stop()
                        live_active = False
                    print_hype(result)
                    if not pool._shutdown:
                        live.start()
                        live_active = True

                # ── Checkpoint setiap 10 video ─────────────────────
                if done % 10 == 0:
                    save_checkpoint(
                        checkpoint_info["dir"], checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )

                # ── Rate limit emergency stop ──────────────────────
                if consecutive_errors >= 10:
                    save_checkpoint(
                        checkpoint_info["dir"], checkpoint_info["channel"],
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )
                    handle_rate_limit(
                        webhook_url, channel, "engine",
                        checkpoint_info["start_from"] + done, checkpoint_info["total"]
                    )
                    _kirim_laporan_darurat("rate limit darurat")
                    if JSON_MODE:
                        _emit_json("rate_limit_stop", {
                            "channel": channel, "done": done, "total": _stats["total"],
                        })
                    if live_active:
                        live.stop()
                        live_active = False
                    _console.print("[red]✗ Rate limit darurat — proses dihentikan.[/red]")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

    return results, consecutive_errors

# ==============================================================================
# BUILD HTML REPORT
# ==============================================================================
def build_html(channel: str, executor: str, results: list, lang: str = "id") -> str:
    valid_results   = [r for r in results if r.get("is_valid")]
    no_trans        = [r for r in results if r["status"] in ("no_transcript","disabled","unavailable")]
    no_match        = [r for r in results if r["status"] == "no_match"]
    analyzed        = [r for r in results if r["status"] == "analyzed"]

    sorted_results  = sorted(analyzed, key=lambda x: x["persentase"], reverse=True)
    now_str         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_str        = datetime.now().strftime("%Y-%m-%d")
    LANG_LABELS     = {
                      "id": ("id", "INDONESIA"),
                      "en": ("en", "ENGLISH"),
                      "jp": ("ja", "JAPANESE"),
                      "kr": ("ko", "KOREAN"),
                      "in": ("hi", "HINDI / INDIA"),
                       }
    html_lang, lang_name = LANG_LABELS.get(lang, ("id", "INDONESIA"))
    total_hits      = sum(len(r.get("hits", [])) for r in results)

    # ── KASTA CSS CLASS per kartu ─────────────────────────────────
    def _card_class(kasta: str) -> str:
        return {
            "GOD_MODE":   "god",
            "VALID_HIGH": "valid-high",
            "SILENT":     "silent",
            "VALID":      "valid",
            "LOW":        "low",
        }.get(kasta, "")

    # ── CONFIDENCE COLOR ─────────────────────────────────────────
    def _pct_class(pct: int) -> str:
        if pct >= 75: return "high"
        if pct >= 40: return "mid"
        return ""

    # ── TIER STRIP per kartu ─────────────────────────────────────
    def _tier_strip(tier_counts: dict) -> str:
        parts = []
        if tier_counts.get("CORE",    0): parts.append(f"<span class='tc core'>CORE &times;{tier_counts['CORE']}</span>")
        if tier_counts.get("TYPO",    0): parts.append(f"<span class='tc typo'>TYPO &times;{tier_counts['TYPO']}</span>")
        if tier_counts.get("SILENT",  0): parts.append(f"<span class='tc silent'>SILENT &times;{tier_counts['SILENT']}</span>")
        if tier_counts.get("CONTEXT", 0): parts.append(f"<span class='tc ctx'>CTX &times;{tier_counts['CONTEXT']}</span>")
        if tier_counts.get("FP",      0): parts.append(f"<span class='tc fp'>FP &times;{tier_counts['FP']}</span>")
        return " ".join(parts)

    # ── CARDS HTML ────────────────────────────────────────────────
    cards_html = ""
    for r in sorted_results:
        if not r.get("html_rows"):
            continue
        cc    = _card_class(r.get("kasta", ""))
        pc    = _pct_class(r.get("persentase", 0))
        ts    = _tier_strip(r.get("tier_counts", {}))
        cards_html += f"""
    <div class="card {cc}">
      <div class="card-head">
        <a class="vid-id" href="https://youtu.be/{r['video_id']}" target="_blank">&#9889; {r['video_id']}</a>
        <div class="confidence">
          <span class="pct {pc}">{r['persentase']}%</span> CONFIDENCE
        </div>
        <div class="card-status">{r['kasta_label']}</div>
      </div>
      <div class="tier-strip">{ts}</div>
      <table>
        <tr><th>TIMESTAMP</th><th>TRANSCRIPT EVIDENCE</th></tr>
        {r['html_rows']}
      </table>
    </div>"""

    # ── NO-TRANSCRIPT GRID ────────────────────────────────────────
    no_trans_links = "".join(
        f"<a href='https://youtu.be/{r['video_id']}' target='_blank'>{r['video_id']}</a>"
        for r in no_trans
    )

    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CS20 Intel Report — @{channel}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #080c10;
  color: #8ab0c8;
  font-family: 'Courier New', monospace;
  min-height: 100vh;
}}

/* ── HEADER ─────────────────────────────────────────────────── */
.header {{
  background: linear-gradient(135deg, #080c10 0%, #0d1520 100%);
  border-bottom: 1px solid #1a3a5c;
  padding: 20px 28px;
}}
.header-top {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}}
.header h1 {{
  color: #4fc3f7;
  font-size: 1.0em;
  letter-spacing: 3px;
  text-transform: uppercase;
  text-shadow: 0 0 12px #4fc3f755;
}}
.header h1 span {{ color: #fff; }}
.op-tag {{
  background: #ff3c3c22;
  border: 1px solid #ff3c3c;
  color: #ff3c3c;
  font-size: 0.65em;
  padding: 4px 14px;
  letter-spacing: 2px;
  animation: blink 1.5s step-end infinite;
}}
@keyframes blink {{ 50% {{ opacity: 0.3; }} }}
.header-meta {{
  display: flex;
  gap: 24px;
  margin-top: 14px;
  flex-wrap: wrap;
}}
.meta-item {{ font-size: 0.68em; }}
.meta-item .key {{ color: #1a3a5c; text-transform: uppercase; letter-spacing: 1px; }}
.meta-item .val {{ color: #4fc3f7; margin-left: 6px; }}

/* ── CONTENT ─────────────────────────────────────────────────── */
.content {{
  padding: 24px 28px;
  max-width: 980px;
  margin: 0 auto;
}}

/* ── STATS GRID ──────────────────────────────────────────────── */
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 28px;
}}
.stat-card {{
  background: #0d1520;
  border: 1px solid #1a3a5c;
  padding: 14px;
  text-align: center;
  position: relative;
  overflow: hidden;
}}
.stat-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: #4fc3f7;
}}
.stat-card.red::before  {{ background: #ff3c3c; }}
.stat-card.green::before {{ background: #00ff88; }}
.stat-card.yellow::before {{ background: #ffc107; }}
.stat-card .n {{ font-size: 1.8em; font-weight: bold; color: #4fc3f7; }}
.stat-card.red .n    {{ color: #ff3c3c; }}
.stat-card.green .n  {{ color: #00ff88; }}
.stat-card.yellow .n {{ color: #ffc107; }}
.stat-card .l {{
  font-size: 0.62em;
  color: #1a4a6c;
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-top: 5px;
}}

/* ── LANG BADGE ──────────────────────────────────────────────── */
.lang-badge {{
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: #0d1520;
  border: 1px solid #4fc3f755;
  color: #4fc3f7;
  font-size: 0.7em;
  padding: 5px 14px;
  margin-bottom: 20px;
  letter-spacing: 1px;
}}
.lang-badge .dot {{
  width: 6px; height: 6px;
  background: #4fc3f7;
  border-radius: 50%;
  animation: pulse 1.2s ease-in-out infinite;
}}
@keyframes pulse {{
  0%,100% {{ opacity:1; transform:scale(1); }}
  50%      {{ opacity:0.4; transform:scale(0.7); }}
}}

/* ── SECTION HEADING ─────────────────────────────────────────── */
.section-head {{
  font-size: 0.68em;
  color: #1a3a5c;
  text-transform: uppercase;
  letter-spacing: 2px;
  padding: 4px 0;
  border-bottom: 1px solid #1a3a5c;
  margin-bottom: 16px;
}}
.section-head span {{ color: #4fc3f7; }}

/* ── CARDS ───────────────────────────────────────────────────── */
.card {{
  background: #0a0f18;
  border: 1px solid #1a3a5c;
  margin-bottom: 14px;
  position: relative;
}}
.card::before {{
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: #4fc3f7;
}}
.card.god::before        {{ background: linear-gradient(180deg, #ffd700, #ff8c00); }}
.card.valid-high::before {{ background: #ff3c3c; }}
.card.silent::before     {{ background: #a020f0; }}
.card.valid::before      {{ background: #00ff88; }}
.card.low::before        {{ background: #445566; }}

.card-head {{
  padding: 10px 16px 10px 20px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  border-bottom: 1px solid #0f1e2e;
}}
.vid-id {{
  color: #4fc3f7;
  font-weight: bold;
  font-size: 0.88em;
  text-decoration: none;
}}
.vid-id:hover {{ text-shadow: 0 0 8px #4fc3f7; }}
.confidence {{
  margin-left: auto;
  font-size: 0.68em;
  color: #445566;
}}
.pct {{
  font-size: 1.5em;
  font-weight: bold;
  color: #4fc3f7;
}}
.pct.high {{ color: #ff3c3c; }}
.pct.mid  {{ color: #ffc107; }}
.card-status {{
  font-size: 0.70em;
  color: #4fc3f788;
  width: 100%;
  padding: 4px 0 2px 0;
}}

/* ── TIER STRIP ──────────────────────────────────────────────── */
.tier-strip {{
  padding: 6px 16px 6px 20px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  background: #060a10;
  border-bottom: 1px solid #0f1e2e;
}}
.tc {{
  font-size: 0.65em;
  padding: 2px 8px;
  font-weight: bold;
  border-left: 2px solid;
}}
.tc.core   {{ color: #ff3c3c; border-color: #ff3c3c; }}
.tc.typo   {{ color: #ffa050; border-color: #ffa050; }}
.tc.silent {{ color: #c060ff; border-color: #c060ff; }}
.tc.ctx    {{ color: #4fc3f7; border-color: #4fc3f7; }}
.tc.fp     {{ color: #556677; border-color: #556677; }}

/* ── TABLE ───────────────────────────────────────────────────── */
table {{ width: 100%; border-collapse: collapse; }}
th {{
  padding: 7px 16px 7px 20px;
  font-size: 0.65em;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #1a3a5c;
  border-bottom: 1px solid #0f1e2e;
  text-align: left;
  background: #060a10;
}}
td {{
  padding: 6px 16px 6px 20px;
  font-size: 0.80em;
  border-bottom: 1px solid #0a1018;
  color: #8ab0c8;
  vertical-align: top;
  line-height: 1.5;
}}
tr:last-child td {{ border-bottom: none; }}
tr:hover td {{ background: #0d1520; color: #c0d8e8; }}
.t-link {{
  color: #00ff88;
  text-decoration: none;
  font-weight: bold;
  font-size: 0.85em;
  white-space: nowrap;
}}
.t-link:hover {{ text-shadow: 0 0 6px #00ff88; }}

/* ── INLINE HIGHLIGHT ────────────────────────────────────────── */
.hl {{ padding: 1px 3px; font-weight: bold; border-radius: 1px; }}
.hl.core   {{ background: #ff3c3c33; color: #ff8080; border-bottom: 1px solid #ff3c3c; }}
.hl.typo   {{ background: #ffa05033; color: #ffc080; border-bottom: 1px solid #ffa050; }}
.hl.silent {{ background: #a020f033; color: #d080ff; border-bottom: 1px solid #a020f0; }}
.hl.ctx    {{ background: #4fc3f722; color: #90d8ff; border-bottom: 1px solid #4fc3f7; }}

/* ── NO-TRANSCRIPT ───────────────────────────────────────────── */
.no-trans-section {{ margin-top: 28px; }}
.no-trans-grid {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}}
.no-trans-grid a {{
  color: #1a3a5c;
  font-size: 0.72em;
  border: 1px solid #1a3a5c;
  padding: 3px 10px;
  text-decoration: none;
}}
.no-trans-grid a:hover {{ color: #4fc3f7; border-color: #4fc3f7; }}

/* ── FOOTER ──────────────────────────────────────────────────── */
footer {{
  text-align: center;
  color: #1a3a5c;
  font-size: 0.65em;
  padding: 24px;
  border-top: 1px solid #0f1e2e;
  margin-top: 28px;
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <h1>CEGUKAN SEEKER &mdash; <span>INTEL REPORT</span> V20</h1>
    <div class="op-tag">&#9679; REPORT SELESAI</div>
  </div>
  <div class="header-meta">
    <div class="meta-item"><span class="key">TARGET</span><span class="val">@{channel}</span></div>
    <div class="meta-item"><span class="key">OPERATOR</span><span class="val">{executor}</span></div>
    <div class="meta-item"><span class="key">ENGINE LANG</span><span class="val">{lang_name}</span></div>
    <div class="meta-item"><span class="key">TIMESTAMP</span><span class="val">{now_str}</span></div>
  </div>
</div>

<div class="content">
  <div class="lang-badge">
    <span class="dot"></span>
    ENGINE AKTIF &mdash; BAHASA {lang_name}
  </div>

  <div class="stats-grid">
    <div class="stat-card red">
      <div class="n">{len(valid_results)}</div>
      <div class="l">CONFIRMED HITS</div>
    </div>
    <div class="stat-card">
      <div class="n">{len(results)}</div>
      <div class="l">TOTAL ANALYZED</div>
    </div>
    <div class="stat-card yellow">
      <div class="n">{len(no_trans)}</div>
      <div class="l">NO TRANSCRIPT</div>
    </div>
    <div class="stat-card green">
      <div class="n">{total_hits}</div>
      <div class="l">TOTAL MOMENTS</div>
    </div>
  </div>

  <div class="section-head">CONFIRMED &mdash; <span>{len(valid_results)} VIDEO TERDETEKSI</span></div>
  {cards_html}

  <div class="no-trans-section">
    <div class="section-head">NO TRANSCRIPT &mdash; <span>{len(no_trans)} VIDEO EXCLUDED</span></div>
    <div class="no-trans-grid">
      {no_trans_links}
    </div>
  </div>

  <footer>CS20 Intel Report V20.0 | Fuzzy Regex Engine | {date_str}</footer>
</div>

</body>
</html>"""

# ==============================================================================
# KIRIM WEBHOOK DISCORD
# ==============================================================================
def load_webhook_url(config_dir: str, webhook_url: str = "") -> str:
    """Return webhook URL dari argumen (sudah di-inject ke cs20.sh)."""
    return webhook_url.strip() if webhook_url else ""

def send_discord(webhook_url: str, channel: str, executor: str,
                 results: list, html_path: str):
    """Kirim laporan ke Discord webhook."""
    if not webhook_url:
        safe_print(f"[yellow][⚠️] Webhook URL tidak ditemukan. Skip Discord.[/yellow]")
        return

    valid_results  = [r for r in results if r.get("is_valid")]
    no_trans_count = sum(1 for r in results if r["status"] in ("no_transcript","disabled","unavailable"))
    all_hits       = sum(len(r.get("hits",[])) for r in results)

    # Tentukan kasta global
    has_god = any(r.get("kasta") == "GOD_MODE" for r in results)
    has_valid_high = any(r.get("kasta") == "VALID_HIGH" for r in results)

    if has_god or len(valid_results) >= 3:
        kasta_global = "👑 [GOD MODE] ANOMALI MEDIS TERDETEKSI!"
        warna = 16761035
    elif has_valid_high or valid_results:
        kasta_global = "🔥 [HIGH HYPE] INDIKASI KUAT DITEMUKAN!"
        warna = 2359050
    elif results:
        kasta_global = "📋 [ALERT] INDIKASI LEMAH / FALSE ALARM"
        warna = 16744192
    else:
        kasta_global = "💀 [ZONK] TARGET DIET CEGUKAN!"
        warna = 8421504

    # Scoreboard top 3
    sorted_r = sorted(
        [r for r in results if r.get("persentase",0) > 0],
        key=lambda x: x["persentase"], reverse=True
    )
    scoreboard = ""
    hidden = 0
    for i, r in enumerate(sorted_r):
        if i < 3:
            scoreboard += f"{i+1}. `{r['video_id']}` ➡️ **{r['persentase']}%** {r['kasta_label'][:40]}\n"
        else:
            hidden += 1

    if not scoreboard:
        scoreboard = "Tidak ada indikasi cegukan yang terdeteksi."

    if hidden > 0:
        scoreboard += f"\n**+{hidden} video lainnya** — lihat HTML untuk detail lengkap!"
    # Hitung tier breakdown total + top keyword
    all_tier_counts = {"CORE": 0, "TYPO": 0, "SILENT": 0, "CONTEXT": 0, "FP": 0}
    keyword_freq = {}
    for r in results:
        for tier, cnt in r.get("tier_counts", {}).items():
            if tier in all_tier_counts:
                all_tier_counts[tier] += cnt
        for hit in r.get("hits", []):
            txt = hit.get("text", "").strip()[:35]
            if txt:
                keyword_freq[txt] = keyword_freq.get(txt, 0) + 1

    tier_text = (
        f"CORE: **{all_tier_counts['CORE']}** | "
        f"TYPO: **{all_tier_counts['TYPO']}** | "
        f"SILENT: **{all_tier_counts['SILENT']}** | "
        f"CTX: **{all_tier_counts['CONTEXT']}**"
    )

    top_kw = sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    kw_text = "\n".join(f"• `{k}` ×{v}" for k, v in top_kw) if top_kw else "—"

    payload = {
        "embeds": [{
            "title": kasta_global,
            "color": warna,
            "description": f"Laporan forensik V20 untuk @{channel} telah selesai.",
            "fields": [
                {"name": "👤 Target", "value": f"@{channel}", "inline": True},
                {"name": "👷 Eksekutor", "value": executor, "inline": True},
                {"name": "📊 Statistik",
                 "value": f"Total: {len(results)} | Valid: {len(valid_results)} | No-Trans: {no_trans_count} | Hits: {all_hits}",
                 "inline": False},
                {"name": "🔑 Keyword Terdeteksi (Top 5)", "value": kw_text, "inline": False},
                {"name": "📊 Tier Breakdown", "value": tier_text, "inline": False},
                {"name": "📈 Top Scoreboard", "value": scoreboard, "inline": False},
            ]
        }]
    }

    payload_json = json.dumps(payload)

    try:
        import requests as req_lib
    except ImportError:
        safe_print(f"[yellow][📦] Install requests...[/yellow]")
        import subprocess
        subprocess.run(
            ["pip", "install", "requests", "--break-system-packages", "-q"],
            check=True
        )
        import requests as req_lib

    # ── LANGKAH 1: Kirim embed dulu (tanpa file) ──────────────────
    try:
        resp = req_lib.post(
            webhook_url,
            json=payload,
            timeout=30
        )
        if resp.status_code in (200, 204):
            safe_print(f"[green][✅] Ringkasan terkirim ke Discord![/green]")
        elif resp.status_code == 403:
            safe_print(f"[red][❌] Discord 403 Forbidden[/red]")
            safe_print(f"[yellow]     Cek apakah webhook masih aktif di Discord:[/yellow]")
            safe_print(f"[yellow]     Server → Edit Channel → Integrations → Webhooks[/yellow]")
            safe_print(f"[yellow]     File HTML disimpan lokal: {html_path}[/yellow]")
            return
        else:
            safe_print(f"[yellow][⚠️] Discord response: {resp.status_code} — {resp.text[:100]}[/yellow]")
    except Exception as e:
        safe_print(f"[red][❌] Gagal kirim embed: {e}[/red]")
        safe_print(f"[yellow]     File HTML disimpan lokal: {html_path}[/yellow]")
        return

    # ── LANGKAH 2: Attach file HTML hanya jika ukuran aman ────────
    if not os.path.exists(html_path):
        return

    file_size_mb = os.path.getsize(html_path) / (1024 * 1024)

    if file_size_mb > 7.5:
        safe_print(f"[yellow][⚠️] HTML terlalu besar ({file_size_mb:.1f}MB), tidak bisa attach ke Discord.[/yellow]")
        safe_print(f"[yellow]     File disimpan lokal: {html_path}[/yellow]")
        return

    safe_print(f"[dim][📎] Mengirim file HTML ({file_size_mb:.2f}MB)...[/dim]")
    try:
        fname = os.path.basename(html_path)
        with open(html_path, "rb") as f:
            resp2 = req_lib.post(
                webhook_url,
                data={"payload_json": json.dumps({"content": f"📄 Laporan lengkap @{channel}:"})},
                files={"file": (fname, f, "text/html")},
                timeout=60
            )
        if resp2.status_code in (200, 204):
            safe_print(f"[green][✅] File HTML berhasil dikirim ke Discord![/green]")
        else:
            safe_print(f"[yellow][⚠️] File response: {resp2.status_code} — file disimpan lokal[/yellow]")
            safe_print(f"[yellow]     {html_path}[/yellow]")
    except Exception as e:
        safe_print(f"[red][❌] Gagal kirim file: {e}[/red]")
        safe_print(f"[yellow]     File disimpan lokal: {html_path}[/yellow]")

# ==============================================================================
# CHECKPOINT
# ==============================================================================
def save_checkpoint(checkpoint_dir: str, channel: str, done: int, total: int):
    os.makedirs(checkpoint_dir, exist_ok=True)
    cp_file = os.path.join(checkpoint_dir, f"{channel}.checkpoint")
    now_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    with open(cp_file, "w") as f:
        f.write(f"DONE={done}\nTOTAL={total}\nTIME={now_str}\n")

def clear_checkpoint(checkpoint_dir: str, channel: str):
    cp_file = os.path.join(checkpoint_dir, f"{channel}.checkpoint")
    if os.path.exists(cp_file):
        os.remove(cp_file)

# ==============================================================================
# BLOCKED VIDEO LOG
# ==============================================================================
def _init_blocked_log(path: str, channel: str, lang: str,
                      engine_name: str):
    """Buat file log blocked jika belum ada."""
    if os.path.exists(path):
        return
    data = {
        "channel":    channel,
        "engine":     engine_name,
        "lang":       lang,
        "created_at": datetime.now().strftime("%d-%m-%Y %H:%M"),
        "videos":     []
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _append_blocked_log(path: str, video_id: str, reason: str):
    """Tambah satu video ke log blocked (thread-safe)."""
    try:
        with _print_lock:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                return  # _init belum dipanggil, skip
            # Hindari duplikasi
            ids = [v["id"] for v in data.get("videos", [])]
            if video_id not in ids:
                data["videos"].append({"id": video_id, "reason": reason})
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

# ==============================================================================
# RATE LIMIT HANDLER
# ==============================================================================
def handle_rate_limit(webhook_url: str, channel: str, executor: str,
                      done: int, total: int):
    """Kirim notif darurat rate limit ke Discord."""
    safe_print(f"\n[red][⚠️] RATE LIMIT TERDETEKSI! Mengirim notif darurat...[/red]")

    if not webhook_url:
        return

    payload = {
        "embeds": [{
            "title": "⚠️ PROSES TERHENTI — RATE LIMIT / CAPTCHA",
            "color": 16711680,
            "description": (
                f"Proses untuk @{channel} terhenti karena rate limit YouTube.\n\n"
                f"📊 **Progres tersimpan:** {done}/{total} video selesai\n"
                f"👷 **Eksekutor:** {executor}\n\n"
                f"💾 Checkpoint tersimpan. Jalankan ulang skrip untuk melanjutkan dari video ke-{done+1}."
            )
        }]
    }

    try:
        payload_json = json.dumps(payload).encode()
        req = urllib.request.Request(
            webhook_url,
            data=payload_json,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass

# ==============================================================================
# LAPORAN DARURAT — dipanggil saat henti paksa
# ==============================================================================
def _kirim_laporan_darurat(alasan: str = "henti paksa"):
    channel     = _emergency_info["channel"]
    executor    = _emergency_info["executor"]
    webhook_url = _emergency_info["webhook_url"]
    config_dir  = _emergency_info["config_dir"]

    if not _partial_results:
        safe_print(f"[yellow]  ⚠ Tidak ada hasil untuk dikirim.[/yellow]")
        return

    safe_print(f"\n[yellow]📤 Menyusun laporan darurat ({len(_partial_results)} video)...[/yellow]")

    try:
        html_content = build_html(channel, executor, _partial_results, _emergency_info.get("lang", "id"))
        html_path = os.path.join(
            config_dir,
            f"laporan_darurat_{channel}_{datetime.now().strftime('%d%m%Y_%H%M')}.html"
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        send_discord(webhook_url, channel, executor, _partial_results, html_path)
        safe_print(f"[green]✅ Laporan darurat terkirim.[/green]")
    except Exception as e:
        safe_print(f"[red]❌ Gagal kirim laporan darurat: {e}[/red]")

# ==============================================================================
# MAIN PROCESSING
# ==============================================================================
def process_channel(args):
    channel      = args.channel
    limit        = args.limit
    jobs         = args.jobs
    content_type = args.content_type
    executor     = args.executor
    mode         = args.mode
    start_from   = args.start_from
    checkpoint_dir = args.checkpoint_dir
    config_dir   = args.config_dir

    webhook_url  = load_webhook_url(config_dir, args.webhook_url)
    
    # ── Isi info darurat & blocked log ───────────────────────────
    global _emergency_info, _current_blocked_log, _partial_results
    _partial_results = []  # reset tiap channel baru
    global _cleanup_done
    _cleanup_done = False  # reset tiap channel baru
    _emergency_info.update({
        "channel":        channel,
        "executor":       executor,
        "webhook_url":    webhook_url,
        "config_dir":     config_dir,
        "checkpoint_dir": checkpoint_dir,
        "lang":           args.lang,
    })
    _current_blocked_log = os.path.join(
        config_dir, f"{channel}_blocked.json"
    )
    engine_name = os.path.basename(__file__)
    lang        = args.lang
    _init_lang(lang)
    _init_blocked_log(_current_blocked_log, channel, lang, engine_name)

    # Reset flag display untuk channel baru
    global _current_mode, _parallel_status
    _current_mode       = mode
    _parallel_status    = {}

    # Set cookies path global — dipakai oleh _fetch_transcript
    global COOKIES_PATH
    COOKIES_PATH = os.path.join(config_dir, "cookies.txt")
    # ── TRAP Ctrl+C ──────────────────────────────────────────────
    def _on_sigint(sig, frame):
        safe_print(f"\n[yellow][⚠️] Ctrl+C — membersihkan sisa proses...[/yellow]")
        save_checkpoint(checkpoint_dir, channel, _stats["done"], _stats["total"])
        _kirim_laporan_darurat("Ctrl+C")
        cleanup_all(channel, checkpoint_dir)
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_sigint)
    atexit.register(cleanup_all, channel, checkpoint_dir)
    if os.path.exists(COOKIES_PATH):
        safe_print(f"[green][🍪] Cookies ditemukan — rate limit protection aktif![/green]")
    else:
        safe_print(f"[yellow][⚠️] Cookies tidak ditemukan — rentan rate limit.[/yellow]")
        safe_print(f"[yellow]     Jalankan: bash setup_cookies.sh untuk setup cookies[/yellow]")
    
    # ── Mode retry blocked log ────────────────────────────────────
    if args.retry_blocked_log and os.path.exists(args.retry_blocked_log):
        safe_print(f"[cyan][🔄] Mode retry blocked log: {args.retry_blocked_log}[/cyan]")
        try:
            with open(args.retry_blocked_log, "r", encoding="utf-8") as f:
                blocked_data = json.load(f)
            video_ids = [v["id"] for v in blocked_data.get("videos", [])]
            safe_print(f"[green]  {len(video_ids)} video akan di-retry[/green]")
        except Exception as e:
            safe_print(f"[red]❌ Gagal baca log: {e}[/red]")
            return
    else:
        safe_print(f"\n[cyan][📥] Mengambil daftar video untuk @{channel}...[/cyan]")
        video_ids = get_video_ids(channel, limit, content_type)


    if not video_ids:
        safe_print(f"[red][❌] Tidak ada video ditemukan untuk @{channel}.[/red]")
        return

    # Apply checkpoint
    if start_from > 0 and start_from < len(video_ids):
        safe_print(f"[yellow][💾] Melanjutkan dari video ke-{start_from+1}/{len(video_ids)}[/yellow]")
        video_ids_to_process = video_ids[start_from:]
    else:
        video_ids_to_process = video_ids

    total = len(video_ids_to_process)
    safe_print(f"[green][✅] {total} video akan diproses dengan {jobs} jobs[/green]\n")

 # Init stats
    _stats["done"]          = 0
    _stats["total"]         = total
    _stats["hits_total"]    = 0
    _stats["valid_count"]   = 0
    _stats["rate_limit_count"] = 0
    _stats["start_time"]    = time.time()
    _stats["tier_counts"]   = {}
    _stats["_jobs"]         = jobs
    # ── Reset error counters ──
    _stats["err_blocked"]   = 0
    _stats["err_age"]       = 0
    _stats["err_ratelimit"] = 0
    _stats["err_timeout"]   = 0
    _stats["err_unknown"]   = 0
    # ── Reset tier counters live ──
    _stats["tier_CORE"]    = 0
    _stats["tier_TYPO"]    = 0
    _stats["tier_SILENT"]  = 0
    _stats["tier_CONTEXT"] = 0
    _stats["tier_FP"]      = 0

    # ── Worker function ───────────────────────────────────────────
    def worker(vid_id):
        if _current_mode == "pantau":
            with _parallel_lock:
                _parallel_status[vid_id] = "fetching..."
        result = analyze_video(vid_id, channel)
        if _stats["done"] % 20 == 0:
            gc.collect()
        time.sleep(random.uniform(2.0, 4.0))
        return result

    # ── Checkpoint info — diteruskan ke run_display_* ─────────────
    cp_info = {
        "dir":        checkpoint_dir,
        "channel":    channel,
        "start_from": start_from,
        "total":      len(video_ids),
    }

    # ── Jalankan sesuai mode ──────────────────────────────────────
    if mode == "tidur":
        results, consecutive_errors = run_display_tidur(
            channel, video_ids_to_process, worker, cp_info, webhook_url
        )
    elif mode == "semi":
        results, consecutive_errors = run_display_semi(
            channel, video_ids_to_process, worker, cp_info, webhook_url
        )
    elif mode == "pantau":
        results, consecutive_errors = run_display_pantau(
            channel, video_ids_to_process, worker, cp_info, webhook_url
        )
    else:
        results, consecutive_errors = run_display_semi(
            channel, video_ids_to_process, worker, cp_info, webhook_url
        )

    # ── SUMMARY TERMINAL ────────────────────────────────────────────
    elapsed = time.time() - _stats["start_time"]
    elapsed_min = int(elapsed // 60)
    elapsed_sec = int(elapsed % 60)

    valid_count = _stats["valid_count"]
    hits_total  = _stats["hits_total"]

    _console.print(Panel(
        f"[white]📊 {total} video diproses\n"
        f"✅ {valid_count} video valid\n"
        f"🎯 {hits_total} total momen terdeteksi\n"
        f"⏱️  Waktu: {elapsed_min}m {elapsed_sec}s\n"
        f"📤 Laporan dikirim ke Discord[/white]",
        title=f"[green]🏁 SELESAI — @{channel}[/green]",
        border_style="green",
        padding=(1, 2),
    ))

    # ── BUILD & KIRIM HTML ──────────────────────────────────────────
    html_content = build_html(channel, executor, results, lang)
    html_path = os.path.join(
        config_dir,
        f"laporan_{channel}_{datetime.now().strftime('%d%m%Y_%H%M')}.html"
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    send_discord(webhook_url, channel, executor, results, html_path)

    if JSON_MODE:
        _emit_json("report_sent", {"channel": channel, "html_path": html_path})

    # Hapus HTML lokal hanya jika ukurannya aman dan sudah terkirim
    # Jika terlalu besar atau forbidden, file dibiarkan untuk akses manual
    if os.path.exists(html_path):
        file_size_mb = os.path.getsize(html_path) / (1024 * 1024)
        if file_size_mb <= 7.5:
            os.remove(html_path)
        else:
            safe_print(f"[yellow][📁] HTML disimpan di: {html_path}[/yellow]")

    # Cleanup memori dan file temporer — checkpoint TIDAK dihapus otomatis
    cleanup_all(channel, checkpoint_dir)

    # ── Cek blocked log — tawarkan hapus ─────────────────────────
    if os.path.exists(_current_blocked_log):
        try:
            with open(_current_blocked_log, "r", encoding="utf-8") as f:
                blocked_data = json.load(f)
            blocked_count = len(blocked_data.get("videos", []))
        except Exception:
            blocked_count = 0

        if blocked_count > 0:
            safe_print(f"\n[yellow]⚠️  {blocked_count} video kena block saat proses.[/yellow]")
            safe_print(f"[dim]   Log disimpan di: {_current_blocked_log}[/dim]")
            safe_print(f"[dim]   Bisa di-retry dari menu Log Blocked di cs20.sh[/dim]")
        else:
            # Log kosong — hapus saja
            os.remove(_current_blocked_log)
    
def clear_transcript_cache():
    """Bersihkan cache transkrip di memori jika library support."""
    try:
        if hasattr(YouTubeTranscriptApi, '_cache'):
            YouTubeTranscriptApi._cache.clear()
        if hasattr(YouTubeTranscriptApi, 'cache_clear'):
            YouTubeTranscriptApi.cache_clear()
    except Exception:
        pass

_cleanup_done = False

def cleanup_all(channel: str = "", checkpoint_dir: str = ""):
    """Bersihkan semua sisa memori dan file temporer."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    clear_transcript_cache()
    gc.collect()

    global _parallel_status
    with _parallel_lock:
        _parallel_status.clear()

    for ext in ("*.vtt", "*.json", "*.part"):
        for f in glob.glob(ext):
            try:
                if "_blocked" not in f:  # jangan hapus blocked log
                    os.remove(f)
            except Exception:
                pass

    safe_print(f"[dim][🧹] Cleanup selesai.[/dim]")

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cegukan Seeker V20 Engine")
    parser.add_argument("--channel",       required=True)
    parser.add_argument("--limit",         type=int, default=50)
    parser.add_argument("--jobs",          type=int, default=4)
    parser.add_argument("--content-type",  default="all")
    parser.add_argument("--executor",      default="Unknown")
    parser.add_argument("--mode",          default="semi")
    parser.add_argument("--start-from",    type=int, default=0)
    parser.add_argument("--checkpoint-dir",default=".cs20/checkpoints")
    parser.add_argument("--config-dir",    default=".cs20")
    parser.add_argument("--webhook-url",   default="")
    parser.add_argument("--lang",               default="id")
    parser.add_argument("--retry-blocked-log",  default="")
    parser.add_argument("--json-events", action="store_true",
                         help="Print event JSON (prefix CS20JSON:) buat konsumsi Web UI. "
                              "Tanpa flag ini, perilaku 100% sama seperti sebelumnya.")

    args = parser.parse_args()
    if args.json_events:
        JSON_MODE = True
    process_channel(args)
