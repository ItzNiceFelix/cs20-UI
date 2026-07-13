#!/usr/bin/env python3
# ==============================================================================
#   CHATSEEKER V3.0 — LIVE CHAT MINER
#   Full Python · Rich Dashboard · Error Detection · Checkpoint · Discord
#   Rewrite dari chatseeker.sh V2.1 + adaptasi error engine dari cs20_age_engine
# ==============================================================================

import gc
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
from datetime import datetime
from pathlib import Path

# ── Rich import ────────────────────────────────────────────────────────────────
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
    from rich.prompt import Prompt, Confirm
    from rich.columns import Columns
    from rich.padding import Padding
except ImportError:
    print("[❌] Install rich dulu: pip install rich --break-system-packages")
    sys.exit(1)

# ==============================================================================
# KONFIGURASI — edit di sini
# ==============================================================================
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1465401345648496640/lzk0AuBOVTOib4R3sU7AQSebuIfVTkUBnTJSDhIKBRJhQranES4AnEXcJfMCJzT1Kcdk"
LOG_FILE        = Path(".chatseeker_log.json")
CHECKPOINT_DIR  = Path(".cs_checkpoints")
WORKERS         = 4          # paralel download
DL_SLEEP_BASE   = 0.8        # detik jeda antar request (lebih rendah dari v2)
SOCK_TIMEOUT    = 15
EXTRACTOR_RETRY = 3

# ==============================================================================
# TERMINAL SIZING — aman untuk Termux sempit
# ==============================================================================
_TERM_W       = shutil.get_terminal_size(fallback=(50, 24)).columns
_COMPACT      = _TERM_W < 52
_PANEL_W      = min(_TERM_W - 2, 64)
_BAR_W        = max(8, min(20, _TERM_W - 38))

# ==============================================================================
# GLOBALS
# ==============================================================================
_console      = Console(highlight=False)
_stats_lock   = threading.Lock()
_html_lock    = threading.Lock()

# Stats real-time (diupdate dari thread)
_stats = {
    "phase":        "dl",       # "dl" | "parse"
    "done":         0,
    "total":        0,
    "ok":           0,
    "no_chat":      0,
    "unavailable":  0,
    "rate_limited": 0,
    "network":      0,
    "error":        0,
    "lvl4":         0,
    "lvl3":         0,
    "lvl2":         0,
    "lvl1":         0,
    "hits":         0,
    "start_time":   0.0,
}

# Untuk partial-report saat SIGINT
_current_target   = ""
_operator_name    = ""
_partial_html     = []        # list string HTML rows
_html_kolektif    = []        # list string HTML sections (thread-safe via _html_lock)
_score_buckets    = {4: [], 3: [], 2: [], 1: []}  # bucket score per level
_score_lock       = threading.Lock()
_shutdown_flag    = threading.Event()

# ==============================================================================
# HELPER UMUM
# ==============================================================================

def _fmt_eta(done: int, total: int, start_time: float) -> str:
    if done == 0 or start_time == 0:
        return "menghitung..."
    elapsed   = time.time() - start_time
    rate      = done / elapsed if elapsed > 0 else 0
    remaining = (total - done) / rate if rate > 0 else 0
    m, s      = divmod(int(remaining), 60)
    return f"~{m}m{s:02d}s"


def _safe_wc(path: Path) -> int:
    """Hitung baris file tanpa fork wc."""
    try:
        return sum(1 for _ in path.open())
    except Exception:
        return 0


def _ytdlp_cmd() -> list[str]:
    """
    Deteksi cara invoke yt-dlp yang tersedia di sistem.
    Prioritas: binary 'yt-dlp' → 'python3 -m yt_dlp' → error.
    Di-cache setelah deteksi pertama.
    """
    if _ytdlp_cmd._cache is not None:
        return _ytdlp_cmd._cache

    # Coba binary langsung (paling umum di Termux)
    try:
        r = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            _ytdlp_cmd._cache = ["yt-dlp"]
            return _ytdlp_cmd._cache
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Coba python3 -m yt_dlp
    try:
        r = subprocess.run(
            ["python3", "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            _ytdlp_cmd._cache = ["python3", "-m", "yt_dlp"]
            return _ytdlp_cmd._cache
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    _ytdlp_cmd._cache = []   # tidak ditemukan
    return []

_ytdlp_cmd._cache = None   # type: ignore


def _run(cmd: list, timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    """Wrapper subprocess.run dengan timeout."""
    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, env=env or os.environ.copy(),
    )


def _classify_ytdlp_error(combined: str) -> str:
    """
    Klasifikasi error output yt-dlp → status string.
    Diadaptasi dari cs20_age_engine._download_subtitle_with_cookies.
    """
    c = combined.lower()
    if "video unavailable" in c or "private video" in c or "this video is not available" in c:
        return "unavailable"
    if "confirm your age" in c or "age-restricted" in c or "age restricted" in c:
        return "unavailable"          # age-restricted → tidak bisa diproses tanpa cookies
    if "no subtitles" in c or "there are no" in c or "subtitles not available" in c \
            or "no live_chat" in c or "live chat replay" not in c and "live_chat" not in c:
        # yt-dlp akan tulis error jika tidak ada live_chat
        return "no_chat"
    if "429" in c or "too many requests" in c or "http error 429" in c:
        return "rate_limited"
    if any(k in c for k in ("timeout", "timed out", "connection reset",
                             "remotedisconnected", "ssl", "broken pipe",
                             "network", "connectionerror", "socket")):
        return "network_error"
    return "error"


# ==============================================================================
# RICH DASHBOARD — Full Panel (Opsi C)
# ==============================================================================

def _make_dashboard(target: str) -> Panel:
    """Buat panel dashboard stats untuk Live display."""
    s   = _stats
    tot = s["total"] or 1
    pct = s["done"] * 100 // tot
    eta = _fmt_eta(s["done"], s["total"], s["start_time"])

    phase_txt = (
        "[bold yellow]⬇  DOWNLOAD CHAT[/bold yellow]"
        if s["phase"] == "dl"
        else "[bold green]🔍 PARSING & SCORE[/bold green]"
    )

    # Tabel inner kiri: progress + phase
    left = Table(box=None, show_header=False, padding=(0, 1))
    left.add_column("k", style="dim",        min_width=12)
    left.add_column("v", style="bold white",  min_width=14)

    left.add_row("Target",  f"[cyan]@{target}[/cyan]")
    left.add_row("Fase",    phase_txt)
    left.add_row(
        "Progress",
        f"[white]{s['done']}[/white][dim]/{s['total']}[/dim]  "
        f"[{'green' if pct >= 80 else 'yellow' if pct >= 40 else 'red'}]{pct}%[/]"
    )
    left.add_row("ETA",     f"[yellow]{eta}[/yellow]")
    left.add_row("", "")
    left.add_row("[dim]── Download ──[/dim]", "")
    left.add_row("[green]Chat OK[/green]",    f"[green]{s['ok']}[/green]")
    left.add_row("[dim]No chat[/dim]",        f"[dim]{s['no_chat']}[/dim]")
    left.add_row("[orange1]Unavail[/orange1]", f"[orange1]{s['unavailable']}[/orange1]")
    left.add_row("[red]RateLimit[/red]",      f"[red]{s['rate_limited']}[/red]")
    left.add_row("[yellow]Network[/yellow]",  f"[yellow]{s['network']}[/yellow]")
    left.add_row("[red]Error[/red]",          f"[red]{s['error']}[/red]")

    # Tabel inner kanan: score buckets
    right = Table(box=None, show_header=False, padding=(0, 1))
    right.add_column("k", style="dim",       min_width=10)
    right.add_column("v", style="bold white", min_width=6)

    right.add_row("[dim]── Hasil ──[/dim]", "")
    right.add_row(
        "[bold red]🔥 LVL-4[/bold red]",
        f"[bold red]{s['lvl4']}[/bold red]"
    )
    right.add_row(
        "[red]🔴 LVL-3[/red]",
        f"[red]{s['lvl3']}[/red]"
    )
    right.add_row(
        "[yellow]🟡 LVL-2[/yellow]",
        f"[yellow]{s['lvl2']}[/yellow]"
    )
    right.add_row(
        "[dim]⚪ LVL-1[/dim]",
        f"[dim]{s['lvl1']}[/dim]"
    )
    right.add_row("", "")
    right.add_row(
        "[magenta]💬 Hits[/magenta]",
        f"[magenta]{s['hits']}[/magenta]"
    )

    # Gabung kiri-kanan
    if _COMPACT:
        body = left
    else:
        body = Columns([left, right], padding=(0, 4))

    return Panel(
        body,
        title=f"[bold]CHATSEEKER V3.0[/bold]",
        subtitle=f"[dim]operator: {_operator_name}[/dim]",
        border_style="cyan",
        width=_PANEL_W,
        padding=(0, 1),
    )


def _make_progress_bar(label: str, color: str = "cyan") -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold {color}]{{task.description}}"),
        BarColumn(bar_width=_BAR_W),
        MofNCompleteColumn(),
        TextColumn(f"[{color}]{{task.percentage:>5.1f}}%"),
        TimeRemainingColumn(),
        console=_console,
        transient=False,
    )


# ==============================================================================
# LOG SYSTEM
# ==============================================================================

def _load_log() -> dict:
    try:
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_log(data: dict):
    LOG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_log(channel: str, filter_mode: str, total_vid: int, total_hits: int):
    data = _load_log()
    if channel not in data:
        data[channel] = []
    data[channel].append({
        "filter_mode": filter_mode,
        "scanned_at":  datetime.now().strftime("%d-%m-%Y %H:%M"),
        "total_video": total_vid,
        "total_hits":  total_hits,
    })
    _save_log(data)


def _show_log_channel(channel: str):
    data    = _load_log()
    entries = data.get(channel, [])
    if not entries:
        _console.print(f"  [dim]Belum ada riwayat scan untuk @{channel}.[/dim]")
        return
    tbl = Table(box=rbox.SIMPLE, show_header=True, header_style="bold cyan",
                width=_PANEL_W)
    tbl.add_column("#",       width=3)
    tbl.add_column("Filter",  width=18)
    tbl.add_column("Video",   width=6,  justify="right")
    tbl.add_column("Hits",    width=6,  justify="right")
    tbl.add_column("Waktu",   width=16)
    for i, e in enumerate(entries[-10:], 1):   # max 10 entri terbaru
        tbl.add_row(
            str(i),
            e.get("filter_mode", "?"),
            str(e.get("total_video", 0)),
            str(e.get("total_hits", 0)),
            e.get("scanned_at", "?"),
        )
    _console.print(tbl)


# ==============================================================================
# CHECKPOINT SYSTEM
# ==============================================================================

def _cp_file(channel: str) -> Path:
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    return CHECKPOINT_DIR / f"{channel}.json"


def _load_checkpoint(channel: str) -> set:
    cp = _cp_file(channel)
    try:
        return set(json.loads(cp.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_checkpoint(channel: str, done_ids: set):
    cp = _cp_file(channel)
    cp.write_text(json.dumps(sorted(done_ids), indent=2), encoding="utf-8")


def _reset_checkpoint(channel: str):
    _cp_file(channel).write_text("[]", encoding="utf-8")


# ==============================================================================
# FASE 0: AMBIL ID VIDEO DARI CHANNEL
# ==============================================================================

def _fetch_video_ids(channel: str) -> list[str]:
    """
    Ambil daftar video ID dari channel/streams via yt-dlp --flat-playlist.
    Return list of video_id string, kosong jika gagal.
    """
    ytdlp = _ytdlp_cmd()
    if not ytdlp:
        _console.print(
            "[red]  [\u2717] yt-dlp tidak ditemukan![/red]\n"
            "  [dim]Install: pip install yt-dlp --break-system-packages[/dim]"
        )
        return []

    url = f"https://www.youtube.com/@{channel}/streams"
    cmd = [
        *ytdlp,
        "--flat-playlist",
        "--print", "id",
        "--no-warnings",
        "--socket-timeout", "20",
        url,
    ]
    try:
        r   = _run(cmd, timeout=120)
        ids = [l.strip() for l in r.stdout.splitlines() if l.strip()]

        # Kalau kosong tapi ada stderr — tampilkan ringkasan error
        if not ids and r.stderr.strip():
            err = r.stderr.strip()[:300]
            _console.print(f"  [yellow][!] yt-dlp output:[/yellow] [dim]{err}[/dim]")

        # Deduplikasi, urutan tetap
        seen, uniq = set(), []
        for v in ids:
            if v not in seen:
                seen.add(v); uniq.append(v)
        return uniq

    except subprocess.TimeoutExpired:
        _console.print("[red]  [\u2717] Timeout saat fetch (>120 detik). Cek koneksi.[/red]")
        return []
    except FileNotFoundError:
        _console.print("[red]  [\u2717] yt-dlp tidak ditemukan di PATH.[/red]")
        return []
    except Exception as e:
        _console.print(f"[red]  [\u2717] Error fetch: {e}[/red]")
        return []


# ==============================================================================
# FASE 1: DOWNLOAD LIVE CHAT (per video, dipanggil dari ThreadPoolExecutor)
# ==============================================================================

def _download_chat(video_id: str, work_dir: Path) -> dict:
    """
    Download live_chat subtitle untuk satu video_id.
    Return dict {video_id, status, chat_file, error_type, error_msg}.

    Status:
      "ok"          — chat_file tersedia
      "no_chat"     — video tidak punya live chat / bukan livestream
      "unavailable" — video private/dihapus/age-restricted
      "rate_limited"— HTTP 429
      "network_error"— timeout / koneksi gagal
      "error"       — error lain
    """
    url    = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = str(work_dir / f"chat_{video_id}")

    ytdlp = _ytdlp_cmd()
    if not ytdlp:
        return {**base, "status": "error",
                "error_type": "ytdlp_missing", "error_msg": "yt-dlp tidak ditemukan"}

    cmd = [
        *ytdlp,
        "--skip-download",
        "--write-subs",
        "--sub-langs", "live_chat",
        "--sleep-requests", str(DL_SLEEP_BASE),
        "--extractor-retries", str(EXTRACTOR_RETRY),
        "--socket-timeout", str(SOCK_TIMEOUT),
        "--no-warnings",
        "--user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36",
        "-o", outtmpl,
        url,
    ]

    base = {"video_id": video_id, "chat_file": None,
            "error_type": None, "error_msg": None}

    try:
        r    = _run(cmd, timeout=60)
        comb = (r.stdout + " " + r.stderr)

        # Cari output file
        chat_path = work_dir / f"chat_{video_id}.live_chat.json"
        if chat_path.exists() and chat_path.stat().st_size > 10:
            return {**base, "status": "ok", "chat_file": chat_path}

        # Tidak ada file — parse error dari output yt-dlp
        status = _classify_ytdlp_error(comb)
        return {**base, "status": status,
                "error_type": status,
                "error_msg":  r.stderr.strip()[:300] or r.stdout.strip()[:300]}

    except subprocess.TimeoutExpired:
        return {**base, "status": "network_error",
                "error_type": "timeout", "error_msg": "yt-dlp timeout"}
    except FileNotFoundError:
        return {**base, "status": "error",
                "error_type": "ytdlp_missing", "error_msg": "yt-dlp tidak ditemukan"}
    except Exception as e:
        return {**base, "status": "error",
                "error_type": "unknown", "error_msg": str(e)[:200]}


# ==============================================================================
# FASE 2: PARSE & SCORE (single-pass, efisien)
# ==============================================================================

# Keyword regex — dikompile sekali saja
_KW_UTAMA = re.compile(
    r'"text"\s*:\s*"[^"]*(cegukan|cekukan|kecegukan)[^"]*"',
    re.IGNORECASE
)
_KW_ONOMA = re.compile(
    r'"text"\s*:\s*"[^"]*(hicc|hikk|ngikk)[^"]*"',
    re.IGNORECASE
)
_KW_DASAR = re.compile(
    r'"text"\s*:\s*"[^"]*(minum|nafas|napas|kagetin)[^"]*"',
    re.IGNORECASE
)
_KW_KOMBI = re.compile(
    r'"text"\s*:\s*"[^"]*((coba|tahan|tarik|buang|kasih)\s*(nafas|napas)'
    r'|(minum)\s*(dulu|kak|bang|dir|obat|dlu)'
    r'|[0-9]+\s*tegukan'
    r'|(gas|coba|di)\s*kagetin)[^"]*"',
    re.IGNORECASE
)
_KW_ANY = re.compile(
    r'cegukan|cekukan|hicc|hikk|ngikk|minum|nafas|napas|kagetin',
    re.IGNORECASE
)
_TEXT_RE = re.compile(r'"text"\s*:\s*"([^"]*)"')


def _score_file(chat_path: Path) -> int:
    """
    Hitung skor dari file chat JSON dengan single-pass baca file.
    Return int skor.
    """
    try:
        content = chat_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0

    c_utama = len(_KW_UTAMA.findall(content))
    c_onoma = len(_KW_ONOMA.findall(content))
    c_dasar = len(_KW_DASAR.findall(content))
    c_kombi = len(_KW_KOMBI.findall(content))

    r_dasar = max(0, c_dasar - c_kombi)
    score   = int(c_utama * 35 + c_onoma * 10 + r_dasar * 0.5 + c_kombi * 4.5)
    return score


def _extract_hits(video_id: str, chat_path: Path) -> list[dict]:
    """
    Single-pass JSON parse → ekstrak baris chat yang match keyword.
    Return list of {sec, ts, text, url}.
    """
    hits = []
    try:
        with chat_path.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                if not _KW_ANY.search(line):
                    continue
                try:
                    data    = json.loads(line)
                    actions = (data.get("replayChatItemAction", {})
                                   .get("actions", []))
                    for act in actions:
                        msg = (act.get("addChatItemAction", {})
                                  .get("item", {})
                                  .get("liveChatTextMessageRenderer", {}))
                        if not msg:
                            continue
                        runs = msg.get("message", {}).get("runs", [])
                        text = "".join(r.get("text", "") for r in runs)
                        if not _KW_ANY.search(text):
                            continue
                        msec = int(str(
                            data.get("replayChatItemAction", {})
                                .get("videoOffsetTimeMsec", 0)
                        ))
                        sec = msec // 1000
                        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
                        hits.append({
                            "sec":  sec,
                            "ts":   f"{h:02d}:{m:02d}:{s:02d}",
                            "text": text,
                            "url":  f"https://www.youtube.com/watch?v={video_id}&t={sec}s",
                        })
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except Exception:
        pass
    return hits


def _parse_and_score(video_id: str, chat_path: Path, target_ch: str) -> dict:
    """
    Parse satu file chat:
    1. Hitung skor (single-pass)
    2. Ekstrak hits
    3. Buat HTML section
    Return dict result.
    """
    score = _score_file(chat_path)
    if score == 0:
        return {"video_id": video_id, "score": 0, "level": 0, "hits": [], "html": ""}

    if   score >= 100: level = 4
    elif score >= 75:  level = 3
    elif score >= 50:  level = 2
    else:              level = 1

    level_labels = {
        4: "LVL4-ABSOLUTE-HYPE",
        3: "LVL3-POTENSI-TINGGI",
        2: "LVL2-POTENSI-SEDANG",
        1: "LVL1-POTENSI-RENDAH",
    }
    lvl_tag = level_labels[level]

    hits    = _extract_hits(video_id, chat_path)

    # HTML section
    rows = ""
    for h in hits:
        safe_text = (h["text"]
                     .replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;"))
        rows += (
            f"<div style='padding:6px 0;border-bottom:1px solid #21262d;'>"
            f"<a href='{h['url']}' target='_blank' "
            f"style='color:#58a6ff;text-decoration:none;font-weight:bold;margin-right:10px;'>"
            f"[{h['ts']}]</a>"
            f"<span style='color:#ff7b72;font-weight:bold;'>{safe_text}</span>"
            f"</div>"
        )

    html_section = (
        f"<h3>[{lvl_tag}] @{target_ch} — Skor: {score}</h3>"
        f"<div style='background:#161b22;padding:12px;border-radius:6px;"
        f"margin-bottom:20px;border:1px solid #30363d;'>"
        f"<p style='color:#8b949e;margin:0 0 8px;'>"
        f"<a href='https://www.youtube.com/watch?v={video_id}' "
        f"style='color:#58a6ff;'>{video_id}</a></p>"
        f"{rows}</div>"
    )

    return {
        "video_id": video_id,
        "score":    score,
        "level":    level,
        "hits":     hits,
        "html":     html_section,
    }


# ==============================================================================
# HTML BUILDER
# ==============================================================================

def _build_html(target: str, results: list[dict]) -> str:
    header = (
        "<!DOCTYPE html><html><head>"
        "<meta charset='UTF-8'>"
        f"<title>ChatSeeker V3 — @{target}</title>"
        "<style>"
        "body{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:20px}"
        "h2{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:8px}"
        "h3{color:#ff7b72;margin-top:24px}"
        "a{color:#58a6ff;text-decoration:none}"
        "a:hover{text-decoration:underline}"
        "</style></head><body>"
        f"<h2>CHATSEEKER V3.0 — @{target}</h2>"
        f"<p style='color:#8b949e'>Operator: {_operator_name} | "
        f"Dibuat: {datetime.now().strftime('%d-%m-%Y %H:%M')}</p>"
        f"<p style='color:#8b949e'>Klik timestamp biru untuk lompat ke momen di YouTube.</p>"
    )
    body  = "\n".join(r["html"] for r in results if r.get("html"))
    footer = "</body></html>"
    return header + body + footer


# ==============================================================================
# DISCORD SENDER
# ==============================================================================

def _send_discord(
    target: str, filter_label: str,
    t4: int, t3: int, t2: int, t1: int,
    total_scanned: int,
    html_path: Path | None = None,
    partial: bool = False,
):
    if not DISCORD_WEBHOOK:
        return

    total_hits = t4 + t3 + t2 + t1
    mention    = "@everyone 🚨 LEVEL-4 TERDETEKSI! 🚨\n" if t4 > 0 else ""
    title_pfx  = "⚠️ [PARTIAL — INTERRUPTED]" if partial else "🗃️ LAPORAN FORENSIK"

    payload = {
        "content": mention,
        "embeds": [{
            "title": f"{title_pfx} [CHATSEEKER V3]",
            "color": 16776960 if partial else 65535,
            "fields": [
                {"name": "👤 Operator",          "value": _operator_name or "?",   "inline": True},
                {"name": "🦊 Channel",           "value": f"@{target}",            "inline": True},
                {"name": "📅 Filter",            "value": filter_label,            "inline": True},
                {"name": "🎬 Total Diperiksa",   "value": f"{total_scanned} video","inline": True},
                {"name": "🔥 LVL-4-HYPE",        "value": f"`{t4}` arsip",         "inline": True},
                {"name": "🔴 LVL-3-TINGGI",      "value": f"`{t3}` arsip",         "inline": True},
                {"name": "🟡 LVL-2-SEDANG",      "value": f"`{t2}` arsip",         "inline": True},
                {"name": "⚪ LVL-1-RENDAH",       "value": f"`{t1}` arsip",         "inline": True},
            ],
            "footer": {"text": "ChatSeeker V3.0 | Python · Rich"},
        }]
    }

    payload_str = json.dumps(payload)

    cmd_base = ["curl", "-s"]
    if html_path and html_path.exists():
        cmd = [
            *cmd_base,
            "-F", f"payload_json={payload_str}",
            "-F", f"file=@{html_path}",
            DISCORD_WEBHOOK,
        ]
    else:
        cmd = [
            *cmd_base,
            "-H", "Content-Type: application/json",
            "-X", "POST",
            "-d", payload_str,
            DISCORD_WEBHOOK,
        ]

    try:
        subprocess.run(cmd, timeout=30, capture_output=True)
    except Exception:
        pass


# ==============================================================================
# CLEANUP & SIGNAL HANDLER
# ==============================================================================

_tmp_dir: Path | None = None

def _cleanup(work_dir: Path | None = None):
    """Hapus semua file temp."""
    target = work_dir or _tmp_dir
    if target and target.exists():
        try:
            shutil.rmtree(target)
        except Exception:
            pass


def _sigint_handler(sig, frame):
    """SIGINT — kirim partial report lalu exit bersih."""
    _shutdown_flag.set()
    _console.print("\n")
    _console.print(Rule("[bold red][!] INTERRUPT TERDETEKSI[/bold red]"))

    with _score_lock:
        t4 = len(_score_buckets[4])
        t3 = len(_score_buckets[3])
        t2 = len(_score_buckets[2])
        t1 = len(_score_buckets[1])
    total_hits = t4 + t3 + t2 + t1

    if total_hits > 0 and _current_target:
        _console.print(
            f"  [yellow][~] Ada {total_hits} temuan — mengirim partial report...[/yellow]"
        )
        # Buat HTML parsial
        with _html_lock:
            html_sections = list(_html_kolektif)

        if html_sections:
            partial_path = Path(f"{_current_target}_PARTIAL.html")
            partial_html = (
                f"<html><head><title>PARTIAL @{_current_target}</title>"
                "<style>body{background:#0d1117;color:#c9d1d9;"
                "font-family:monospace;padding:20px}"
                "h2{color:#ffb347;border-bottom:1px solid #30363d}"
                "h3{color:#ff7b72;margin-top:24px}"
                "a:hover{text-decoration:underline!important}</style></head><body>"
                f"<h2>⚠️ PARTIAL REPORT (INTERRUPTED) — @{_current_target}</h2>"
                + "".join(html_sections)
                + "</body></html>"
            )
            partial_path.write_text(partial_html, encoding="utf-8")
            _send_discord(
                _current_target, "PARTIAL/INTERRUPTED",
                t4, t3, t2, t1,
                _stats["done"],
                partial_path,
                partial=True,
            )
            try:
                partial_path.unlink()
            except Exception:
                pass
        else:
            _send_discord(
                _current_target, "PARTIAL/INTERRUPTED",
                t4, t3, t2, t1,
                _stats["done"],
                partial=True,
            )
        _console.print("  [green][✓] Partial report terkirim.[/green]")

    _console.print("  [yellow][~] Membersihkan cache...[/yellow]")
    _cleanup()
    _console.print(f"\n  [green][✓] Sampai jumpa, {_operator_name}![/green]\n")
    sys.exit(1)


signal.signal(signal.SIGINT, _sigint_handler)


# ==============================================================================
# ENGINE UTAMA — FORENSIK
# ==============================================================================

def run_forensik_engine(
    target:      str,
    filter_mode: str,   # "ALL" | "LIMIT" | "CHECKPOINT" | "LIMIT_CHECKPOINT"
    max_vid:     int | str,   # int atau "ALL"
) -> bool:
    """
    Engine utama: fetch ID → download chat (paralel) → parse+score (paralel) → laporan.
    Return True jika sukses.
    """
    global _current_target, _tmp_dir, _html_kolektif, _score_buckets

    _current_target = target
    _html_kolektif  = []
    _score_buckets  = {4: [], 3: [], 2: [], 1: []}

    # Reset stats
    with _stats_lock:
        for k in list(_stats.keys()):
            if k not in ("start_time",):
                _stats[k] = 0
        _stats["phase"] = "dl"

    # ── FASE 0: Fetch ID ───────────────────────────────────────────────────────
    _console.print("")
    _console.print(Panel(
        f"[white]Target  :[/white] [cyan]@{target}[/cyan]\n"
        f"[white]Filter  :[/white] [yellow]{filter_mode}[/yellow]"
        + (f" — max [bold]{max_vid}[/bold] video" if max_vid != "ALL" else ""),
        title="[bold cyan]CHATSEEKER V3.0 — MULAI SCAN[/bold cyan]",
        border_style="cyan",
        width=_PANEL_W,
    ))

    with _console.status("[cyan]Menghubungkan ke YouTube...[/cyan]"):
        all_ids = _fetch_video_ids(target)

    if not all_ids:
        _console.print("[red]  [✗] Tidak ada video ditemukan / channel tidak valid.[/red]")
        return False

    # ── Filter checkpoint ──────────────────────────────────────────────────────
    done_checkpoint: set = set()
    if filter_mode in ("CHECKPOINT", "LIMIT_CHECKPOINT"):
        done_checkpoint = _load_checkpoint(target)
        all_ids         = [i for i in all_ids if i not in done_checkpoint]
        _console.print(
            f"  [dim]Checkpoint: {len(done_checkpoint)} sudah diproses, "
            f"{len(all_ids)} sisa.[/dim]"
        )
        if not all_ids:
            _console.print(
                "  [yellow][i] Semua video sudah pernah diproses.[/yellow]"
            )
            reset = Confirm.ask("  Reset checkpoint dan mulai ulang?", default=False)
            if reset:
                _reset_checkpoint(target)
                all_ids = _fetch_video_ids(target)
            else:
                return False

    # ── Potong sesuai limit ────────────────────────────────────────────────────
    if max_vid != "ALL":
        all_ids = all_ids[:int(max_vid)]

    total = len(all_ids)
    if total == 0:
        _console.print("[red]  [✗] Tidak ada video untuk diproses.[/red]")
        return False

    _console.print(
        f"  [green]Ditemukan [bold]{total}[/bold] video untuk diproses.[/green]"
    )

    # Work dir temp
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = Path(f".cs_tmp_{target}_{ts}")
    work_dir.mkdir(exist_ok=True)
    _tmp_dir = work_dir

    with _stats_lock:
        _stats["total"]      = total
        _stats["done"]       = 0
        _stats["start_time"] = time.time()
        _stats["phase"]      = "dl"

    # ══════════════════════════════════════════════════════════════════════════
    # FASE 1: DOWNLOAD CHAT PARALEL — Full Dashboard (Opsi C)
    # ══════════════════════════════════════════════════════════════════════════
    progress_dl = _make_progress_bar("DL CHAT", "yellow")
    task_dl     = progress_dl.add_task(f"DL @{target}", total=total)

    layout = Layout()
    layout.split_column(
        Layout(name="dash",     ratio=6),
        Layout(name="progress", ratio=1),
    )
    layout["dash"].update(_make_dashboard(target))
    layout["progress"].update(progress_dl)

    dl_results: list[dict] = []

    # Error tracking untuk retry summary
    failed_ids: dict[str, dict] = {}    # video_id → {status, error_msg}
    retryable_statuses = {"network_error", "rate_limited", "error"}

    consecutive_rl = 0

    def _worker_dl(vid_id: str) -> dict:
        if _shutdown_flag.is_set():
            return {"video_id": vid_id, "status": "skipped",
                    "chat_file": None, "error_type": "shutdown", "error_msg": ""}
        # Adaptive delay — makin banyak rate limit, makin panjang jeda
        rl_now = _stats.get("rate_limited", 0)
        base   = DL_SLEEP_BASE + min(rl_now * 0.25, 6.0)
        time.sleep(random.uniform(base, base + 1.5))
        return _download_chat(vid_id, work_dir)

    with Live(layout, console=_console, refresh_per_second=4, transient=False) as live:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(_worker_dl, vid): vid for vid in all_ids}

            for future in as_completed(futures):
                res    = future.result()
                vid_id = res["video_id"]
                status = res["status"]

                with _stats_lock:
                    _stats["done"] += 1
                    if status == "ok":
                        _stats["ok"] += 1
                        consecutive_rl = 0
                    elif status == "no_chat":
                        _stats["no_chat"] += 1
                        consecutive_rl = 0
                    elif status == "unavailable":
                        _stats["unavailable"] += 1
                        consecutive_rl = 0
                    elif status == "rate_limited":
                        _stats["rate_limited"] += 1
                        consecutive_rl += 1
                        failed_ids[vid_id] = res
                    elif status == "network_error":
                        _stats["network"] += 1
                        consecutive_rl = 0
                        failed_ids[vid_id] = res
                    elif status not in ("skipped",):
                        _stats["error"] += 1
                        consecutive_rl = 0
                        failed_ids[vid_id] = res

                dl_results.append(res)

                # Log baris per video (via progress.console agar tidak ganggu Live)
                icon_map = {
                    "ok":           "[green]✓[/green]",
                    "no_chat":      "[dim]—[/dim]",
                    "unavailable":  "[orange1]✗[/orange1]",
                    "rate_limited": "[red]⏸[/red]",
                    "network_error":"[yellow]![/yellow]",
                    "error":        "[red]![/red]",
                    "skipped":      "[dim]·[/dim]",
                }
                icon = icon_map.get(status, "[dim]?[/dim]")
                progress_dl.console.print(
                    f"  {icon} [dim]{vid_id}[/dim]  [dim]{status}[/dim]"
                )

                progress_dl.update(task_dl, advance=1)
                layout["dash"].update(_make_dashboard(target))
                layout["progress"].update(progress_dl)
                live.refresh()

                # Rate limit guard: 10 berturut → cooldown, bukan shutdown
                if consecutive_rl >= 10:
                    live.stop()
                    _console.print(
                        f"\n[yellow][⚠] {consecutive_rl}× rate limit berturut — "
                        f"cooldown 90 detik...[/yellow]"
                    )
                    time.sleep(90)
                    consecutive_rl = 0
                    live.start()

    # File chat yang berhasil
    ok_files = [(r["video_id"], r["chat_file"])
                for r in dl_results if r["status"] == "ok" and r.get("chat_file")]

    if not ok_files:
        _console.print("\n[red]  [✗] Tidak ada file chat berhasil diunduh.[/red]")
        _cleanup(work_dir)
        return False

    _console.print(
        f"\n  [green][✓] Download selesai — "
        f"[bold]{len(ok_files)}[/bold] file chat.[/green]"
    )

    # ══════════════════════════════════════════════════════════════════════════
    # FASE 2: PARSE & SCORE PARALEL — Full Dashboard (Opsi C)
    # ══════════════════════════════════════════════════════════════════════════
    with _stats_lock:
        _stats["phase"]      = "parse"
        _stats["done"]       = 0
        _stats["total"]      = len(ok_files)
        _stats["start_time"] = time.time()

    progress_parse = _make_progress_bar("PARSE", "green")
    task_parse     = progress_parse.add_task(f"PARSE @{target}", total=len(ok_files))

    layout2 = Layout()
    layout2.split_column(
        Layout(name="dash",     ratio=6),
        Layout(name="progress", ratio=1),
    )
    layout2["dash"].update(_make_dashboard(target))
    layout2["progress"].update(progress_parse)

    parse_results: list[dict] = []

    def _worker_parse(args_tuple) -> dict:
        vid_id, chat_path = args_tuple
        return _parse_and_score(vid_id, chat_path, target)

    with Live(layout2, console=_console, refresh_per_second=4, transient=False) as live2:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures2 = {pool.submit(_worker_parse, item): item[0] for item in ok_files}

            for future in as_completed(futures2):
                res    = future.result()
                vid_id = res["video_id"]
                level  = res["level"]
                score  = res["score"]

                with _stats_lock:
                    _stats["done"] += 1
                    if level > 0:
                        _stats[f"lvl{level}"] += 1
                        _stats["hits"] += len(res.get("hits", []))

                with _score_lock:
                    if level > 0:
                        _score_buckets[level].append(score)

                if res.get("html"):
                    with _html_lock:
                        _html_kolektif.append(res["html"])

                parse_results.append(res)

                # Log baris
                if level > 0:
                    lvl_colors = {4: "bold red", 3: "red", 2: "yellow", 1: "dim"}
                    progress_parse.console.print(
                        f"  [bold]LVL{level}[/bold] "
                        f"[{lvl_colors[level]}]{vid_id}[/{lvl_colors[level]}] "
                        f"[dim]skor={score}[/dim]"
                    )

                progress_parse.update(task_parse, advance=1)
                layout2["dash"].update(_make_dashboard(target))
                layout2["progress"].update(progress_parse)
                live2.refresh()

    # ══════════════════════════════════════════════════════════════════════════
    # KALKULASI FINAL
    # ══════════════════════════════════════════════════════════════════════════
    with _score_lock:
        t4 = len(_score_buckets[4])
        t3 = len(_score_buckets[3])
        t2 = len(_score_buckets[2])
        t1 = len(_score_buckets[1])

    def _range_str(bucket: list) -> str:
        if not bucket:
            return "—"
        return f"{min(bucket)}–{max(bucket)}"

    total_hits = t4 + t3 + t2 + t1
    elapsed    = time.time() - _stats["start_time"]

    # Tampil hasil
    _console.print("")
    _console.print(Rule(f"[bold green]HASIL FORENSIK @{target}[/bold green]"))

    result_tbl = Table(box=rbox.SIMPLE_HEAVY, show_header=False,
                       width=_PANEL_W)
    result_tbl.add_column("k", style="dim",       width=22)
    result_tbl.add_column("v", style="bold white", width=30)

    result_tbl.add_row("Diperiksa",   f"[white]{total} video[/white]")
    result_tbl.add_row("Chat OK",     f"[green]{len(ok_files)}[/green]")
    result_tbl.add_row("Terdeteksi",  f"[bold]{total_hits} arsip[/bold]")
    result_tbl.add_row("", "")
    result_tbl.add_row(
        "[bold red]🔥 LVL-4-HYPE[/bold red]",
        f"[bold red]{t4} arsip[/bold red]  [dim]skor {_range_str(_score_buckets[4])}[/dim]"
    )
    result_tbl.add_row(
        "[red]🔴 LVL-3-TINGGI[/red]",
        f"[red]{t3} arsip[/red]  [dim]skor {_range_str(_score_buckets[3])}[/dim]"
    )
    result_tbl.add_row(
        "[yellow]🟡 LVL-2-SEDANG[/yellow]",
        f"[yellow]{t2} arsip[/yellow]  [dim]skor {_range_str(_score_buckets[2])}[/dim]"
    )
    result_tbl.add_row(
        "[dim]⚪ LVL-1-RENDAH[/dim]",
        f"[dim]{t1} arsip  skor {_range_str(_score_buckets[1])}[/dim]"
    )
    result_tbl.add_row("", "")
    result_tbl.add_row(
        "⏱ Waktu",
        f"[dim]{int(elapsed//60)}m {int(elapsed%60)}s[/dim]"
    )

    _console.print(result_tbl)

    # ── Error summary + tawaran retry ─────────────────────────────────────────
    retryable = {vid: info for vid, info in failed_ids.items()
                 if info["status"] in retryable_statuses}

    if retryable:
        _console.print("")
        _console.print(Panel(
            f"[yellow]{len(retryable)} video gagal karena error yang bisa di-retry:[/yellow]\n"
            + "\n".join(
                f"  [dim]{vid}[/dim]  [{info['status']}] "
                f"[dim]{(info.get('error_msg') or '')[:60]}[/dim]"
                for vid, info in list(retryable.items())[:10]
            )
            + ("\n  [dim]...(lebih banyak)[/dim]" if len(retryable) > 10 else ""),
            title=f"[yellow]⚠ {len(retryable)} Video Gagal (Retryable)[/yellow]",
            border_style="yellow",
            width=_PANEL_W,
        ))

        do_retry = Confirm.ask(
            f"  Retry {len(retryable)} video yang gagal sekarang?",
            default=True,
        )
        if do_retry:
            _console.print(f"  [cyan]Retry {len(retryable)} video...[/cyan]")
            retry_ids  = list(retryable.keys())
            retry_ok   = []

            retry_prog = _make_progress_bar("RETRY", "cyan")
            retry_task = retry_prog.add_task("RETRY", total=len(retry_ids))

            with retry_prog:
                with ThreadPoolExecutor(max_workers=max(1, WORKERS // 2)) as pool:
                    retry_futures = {
                        pool.submit(_download_chat, vid, work_dir): vid
                        for vid in retry_ids
                    }
                    for f in as_completed(retry_futures):
                        r = f.result()
                        if r["status"] == "ok" and r.get("chat_file"):
                            retry_ok.append((r["video_id"], r["chat_file"]))
                            # Parse langsung
                            pr = _parse_and_score(r["video_id"], r["chat_file"], target)
                            if pr["level"] > 0:
                                with _stats_lock:
                                    _stats[f"lvl{pr['level']}"] += 1
                                    _stats["hits"] += len(pr.get("hits", []))
                                with _score_lock:
                                    _score_buckets[pr["level"]].append(pr["score"])
                                with _html_lock:
                                    if pr.get("html"):
                                        _html_kolektif.append(pr["html"])
                                parse_results.append(pr)
                                # Update totals
                                t4 = len(_score_buckets[4])
                                t3 = len(_score_buckets[3])
                                t2 = len(_score_buckets[2])
                                t1 = len(_score_buckets[1])
                                total_hits = t4 + t3 + t2 + t1

                        retry_prog.update(retry_task, advance=1)

            _console.print(
                f"  [green][✓] Retry selesai — "
                f"{len(retry_ok)}/{len(retry_ids)} berhasil.[/green]"
            )

    # ── Checkpoint update ──────────────────────────────────────────────────────
    if filter_mode in ("CHECKPOINT", "LIMIT_CHECKPOINT"):
        new_done = done_checkpoint | {r["video_id"] for r in dl_results}
        _save_checkpoint(target, new_done)
        _console.print(
            f"  [green][✓] Checkpoint diperbarui: {len(new_done)} total.[/green]"
        )

    # ── Log ───────────────────────────────────────────────────────────────────
    filter_label = {
        "ALL":              "Semua Arsip",
        "LIMIT":            f"Max {max_vid} Video",
        "CHECKPOINT":       "Resume Checkpoint",
        "LIMIT_CHECKPOINT": f"Max {max_vid} + Checkpoint",
    }.get(filter_mode, filter_mode)

    _append_log(target, filter_label, total, total_hits)

    # ── Build HTML & kirim Discord ─────────────────────────────────────────────
    with _html_lock:
        html_sections = list(_html_kolektif)

    if html_sections:
        all_results_sorted = sorted(parse_results, key=lambda x: x.get("score", 0), reverse=True)
        html_content = _build_html(target, all_results_sorted)
        ts_str       = datetime.now().strftime("%d%m%Y_%H%M")
        html_path    = Path(f"{target}_FORENSIK_{ts_str}.html")
        html_path.write_text(html_content, encoding="utf-8")
        _console.print(f"  [dim]HTML: {html_path}[/dim]")
    else:
        html_path = None

    _console.print("  [cyan][~] Mengirim laporan ke Discord...[/cyan]")
    _send_discord(
        target, filter_label,
        t4, t3, t2, t1,
        total,
        html_path,
    )

    # Hapus HTML jika berhasil dikirim (ukuran kecil)
    if html_path and html_path.exists():
        if html_path.stat().st_size <= 7.5 * 1024 * 1024:
            try:
                html_path.unlink()
            except Exception:
                pass
        else:
            _console.print(f"  [yellow][📁] HTML disimpan (>7.5MB): {html_path}[/yellow]")

    _console.print(f"  [green][✓] Laporan terkirim ke Discord.[/green]")
    _console.print(f"  [green][✓] Log tersimpan.[/green]")

    # ── Cleanup temp ──────────────────────────────────────────────────────────
    _cleanup(work_dir)
    _console.print(f"  [green][✓] Cache dibersihkan.[/green]")
    _console.print(Rule(style="dim"))

    gc.collect()
    return True


# ==============================================================================
# MENU LOG
# ==============================================================================

def menu_log():
    while True:
        _console.clear()
        _console.print(Rule("[bold cyan]CHATSEEKER V3 — LOG PENCARIAN[/bold cyan]"))
        data = _load_log()

        if not data:
            _console.print("  [dim]Log masih kosong.[/dim]")
        else:
            tbl = Table(box=rbox.SIMPLE, show_header=True, header_style="bold cyan",
                        width=_PANEL_W)
            tbl.add_column("Channel", width=20)
            tbl.add_column("Scan", width=5, justify="right")
            for ch, entries in data.items():
                tbl.add_row(f"@{ch}", str(len(entries)))
            _console.print(tbl)

        _console.print("")
        _console.print("  [cyan]1.[/cyan] Lihat detail channel")
        _console.print("  [cyan]2.[/cyan] Hapus log channel")
        _console.print("  [cyan]3.[/cyan] Hapus SEMUA log")
        _console.print("  [cyan]4.[/cyan] Info checkpoint")
        _console.print("  [cyan]5.[/cyan] Reset checkpoint channel")
        _console.print("  [dim]6. Kembali[/dim]")
        _console.print("")

        choice = Prompt.ask("  [bold cyan]Pilihan[/bold cyan]",
                            choices=["1","2","3","4","5","6"], default="6")

        if choice == "1":
            ch = Prompt.ask("  [cyan]Username channel[/cyan]").strip().lstrip("@")
            _show_log_channel(ch)
            input("\n  [Enter] lanjut...")

        elif choice == "2":
            ch   = Prompt.ask("  [cyan]Username channel[/cyan]").strip().lstrip("@")
            data = _load_log()
            if ch in data:
                del data[ch]
                _save_log(data)
                _console.print(f"  [green][✓] Log @{ch} dihapus.[/green]")
            else:
                _console.print(f"  [red][!] @{ch} tidak ditemukan.[/red]")
            input("\n  [Enter] lanjut...")

        elif choice == "3":
            if Confirm.ask("  [red]Yakin hapus SEMUA log?[/red]", default=False):
                _save_log({})
                _console.print("  [green][✓] Semua log dihapus.[/green]")
            input("\n  [Enter] lanjut...")

        elif choice == "4":
            ch = Prompt.ask("  [cyan]Username channel[/cyan]").strip().lstrip("@")
            done = _load_checkpoint(ch)
            _console.print(
                f"  [cyan]Checkpoint @{ch}:[/cyan] "
                f"[bold]{len(done)}[/bold] video sudah diproses."
            )
            input("\n  [Enter] lanjut...")

        elif choice == "5":
            ch = Prompt.ask("  [cyan]Username channel[/cyan]").strip().lstrip("@")
            if Confirm.ask(f"  Reset checkpoint @{ch}?", default=False):
                _reset_checkpoint(ch)
                _console.print(f"  [green][✓] Checkpoint @{ch} direset.[/green]")
            input("\n  [Enter] lanjut...")

        elif choice == "6":
            break


# ==============================================================================
# INPUT FILTER
# ==============================================================================

def input_filter(channel: str) -> tuple[str, int | str]:
    """
    Interaktif pilih filter mode.
    Return (filter_mode, max_vid).
    """
    _console.print("")
    _console.print("  [white]PILIH MODE FILTER:[/white]")
    _console.print("  [cyan]1.[/cyan] Max N Video Terbaru")
    _console.print("  [cyan]2.[/cyan] Lanjut dari Checkpoint")
    _console.print("  [cyan]3.[/cyan] Tanpa Filter (semua arsip)")
    _console.print("")

    fc = Prompt.ask("  [bold cyan]Pilihan[/bold cyan]",
                    choices=["1", "2", "3"], default="3")

    if fc == "1":
        while True:
            try:
                n = int(Prompt.ask("  [cyan]Jumlah video teratas[/cyan]"))
                if n > 0:
                    break
                _console.print("  [red]Harus lebih dari 0.[/red]")
            except ValueError:
                _console.print("  [red]Masukkan angka valid.[/red]")

        # Cek checkpoint
        done = _load_checkpoint(channel)
        if done:
            _console.print(
                f"  [yellow][i] Ada checkpoint: {len(done)} video sudah diproses.[/yellow]"
            )
            use_cp = Confirm.ask("  Lanjut dari checkpoint?", default=False)
            if use_cp:
                return "LIMIT_CHECKPOINT", n
        return "LIMIT", n

    elif fc == "2":
        done = _load_checkpoint(channel)
        if not done:
            _console.print(
                "  [yellow][i] Belum ada checkpoint untuk channel ini. "
                "Mulai fresh.[/yellow]"
            )
            return "CHECKPOINT", "ALL"

        while True:
            try:
                raw = Prompt.ask(
                    "  [cyan]Proses berapa video per sesi? "
                    "(Enter = semua sisa)[/cyan]",
                    default="ALL",
                )
                if raw.upper() == "ALL" or raw == "":
                    return "CHECKPOINT", "ALL"
                n = int(raw)
                if n > 0:
                    return "CHECKPOINT", n
                _console.print("  [red]Harus lebih dari 0.[/red]")
            except ValueError:
                _console.print("  [red]Masukkan angka valid atau Enter.[/red]")

    else:
        return "ALL", "ALL"


# ==============================================================================
# MAIN — BANNER + MODE SELECTION
# ==============================================================================

def main():
    global _operator_name

    _console.clear()

    # Banner
    _console.print(Panel(
        Align.center(
            "[bold cyan]"
            "  ██████╗██╗  ██╗ █████╗ ████████╗\n"
            " ██╔════╝██║  ██║██╔══██╗╚══██╔══╝\n"
            " ██║     ███████║███████║   ██║    \n"
            " ██║     ██╔══██║██╔══██║   ██║    \n"
            " ╚██████╗██║  ██║██║  ██║   ██║    \n"
            "  ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝  ╚═╝    \n"
            "[/bold cyan]"
            "[bold white]  SEEKER V3.0 — LIVE CHAT FORENSIK[/bold white]\n"
            "[dim]  Python · Rich · Paralel · Error Detection[/dim]"
        ),
        border_style="cyan",
        width=_PANEL_W,
    ))

    # Input operator
    _operator_name = Prompt.ask("\n  [bold cyan]Operator name[/bold cyan]").strip()
    if not _operator_name:
        _operator_name = "Operator"

    _console.print(f"\n  Selamat datang, [bold green]{_operator_name}[/bold green]!")

    # Mode
    while True:
        _console.print("")
        _console.print(Rule("[dim]MODE OPERASI[/dim]"))
        _console.print("  [cyan]1.[/cyan] Mode Pantau     [dim](foreground, progress bar)[/dim]")
        _console.print("  [cyan]2.[/cyan] Mode Background [dim](AFK, multi-channel, silent)[/dim]")
        _console.print("  [cyan]L.[/cyan] Kelola Log & Checkpoint")
        _console.print("")

        mode = Prompt.ask(
            "  [bold cyan]Pilihan[/bold cyan]",
            choices=["1", "2", "L", "l"],
            default="1",
        ).upper()

        if mode == "L":
            menu_log()
            _console.clear()
            _console.print(Panel(
                f"[bold green]Selamat datang kembali, {_operator_name}![/bold green]",
                border_style="cyan", width=_PANEL_W,
            ))
            continue

        break

    # ── MODE 2: BACKGROUND ─────────────────────────────────────────────────────
    if mode == "2":
        _console.print("")
        multi = Confirm.ask("  Multi-channel?", default=False)
        max_ch = 5 if multi else 1

        channels    = []
        filter_modes = []
        max_vids    = []

        for i in range(1, max_ch + 1):
            _console.print("")
            ch_in = Prompt.ask(f"  [cyan]Channel ke-{i}[/cyan] (Enter selesai)" if i > 1
                               else "  [cyan]Username channel target[/cyan]",
                               default="" if i > 1 else None)
            if not ch_in and i > 1:
                break
            if not ch_in:
                _console.print("  [red]Channel wajib diisi![/red]")
                continue

            clean = ch_in.lstrip("@")
            channels.append(clean)
            _show_log_channel(clean)

            fm, mv = input_filter(clean)
            filter_modes.append(fm)
            max_vids.append(mv)

        if not channels:
            _console.print("  [red]Tidak ada channel. Keluar.[/red]")
            return

        _console.print("")
        _console.print(Panel(
            f"[bold magenta]💤 MODE BACKGROUND AKTIF[/bold magenta]\n"
            f"[white]Antrean :[/white] {len(channels)} channel\n"
            f"[dim]Layar HP aman dimatikan.[/dim]",
            border_style="magenta", width=_PANEL_W,
        ))

        # Termux wake lock
        subprocess.run(["termux-wake-lock"], capture_output=True)

        def _bg_worker():
            for ch, fm, mv in zip(channels, filter_modes, max_vids):
                if _shutdown_flag.is_set():
                    break
                run_forensik_engine(ch, fm, mv)
            subprocess.run(["termux-wake-unlock"], capture_output=True)

        t = threading.Thread(target=_bg_worker, daemon=True)
        t.start()

        _console.print("  [green][✓] Berjalan di background. Selamat tidur![/green]")
        _console.print("  [dim](Script tetap berjalan. Jangan tutup Termux.)[/dim]")
        t.join()   # tunggu selesai (foreground proses tetap jalan)
        return

    # ── MODE 1: PANTAU (foreground loop) ──────────────────────────────────────
    while True:
        _console.print("")
        ch_in = Prompt.ask("  [bold cyan]Username channel target[/bold cyan]").strip()
        if not ch_in:
            _console.print("  [red]Target kosong![/red]")
            continue

        clean = ch_in.lstrip("@")
        _show_log_channel(clean)

        fm, mv = input_filter(clean)
        run_forensik_engine(clean, fm, mv)

        _console.print("")
        lanjut = Confirm.ask("  Cari channel lain?", default=False)
        if not lanjut:
            break

        _console.clear()
        _console.print(Panel(
            f"[bold green]Operator: {_operator_name}[/bold green]",
            border_style="cyan", width=_PANEL_W,
        ))

    _console.print("")
    _console.print(Panel(
        f"[bold green]Sampai jumpa, {_operator_name}! 👋[/bold green]",
        border_style="green", width=_PANEL_W,
    ))


# ==============================================================================
if __name__ == "__main__":
    main()
