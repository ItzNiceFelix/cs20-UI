#!/usr/bin/env python3
# ==============================================================================
# 👑 CEGUKAN SEEKER V20.0 — STREAMLIT UI
# Ringan, responsive, multi-device (PC + HP)
# ==============================================================================

import streamlit as st
import json
import os
import re
import subprocess
import threading
import queue
import time
import gc
import glob
from datetime import datetime
from pathlib import Path

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="Cegukan Seeker V20",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS Custom ────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp {
        background: #0d1117;
    }
    .cs-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px;
        margin: 8px 0;
    }
    .cs-card-god {
        border-left: 4px solid #ffd700;
        background: linear-gradient(90deg, #ffd70011, #161b22);
    }
    .cs-card-high {
        border-left: 4px solid #ff3c3c;
    }
    .cs-card-valid {
        border-left: 4px solid #00ff88;
    }
    .cs-card-silent {
        border-left: 4px solid #a020f0;
    }
    .cs-card-low {
        border-left: 4px solid #445566;
    }
    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 11px;
        font-weight: bold;
        margin: 2px;
    }
    .badge-core { background: #ff3c3c33; color: #ff8080; border: 1px solid #ff3c3c; }
    .badge-typo { background: #ffa05033; color: #ffc080; border: 1px solid #ffa050; }
    .badge-silent { background: #a020f033; color: #d080ff; border: 1px solid #a020f0; }
    .badge-ctx { background: #4fc3f722; color: #90d8ff; border: 1px solid #4fc3f7; }
    .ts-link {
        color: #00ff88;
        text-decoration: none;
        font-weight: bold;
        font-family: monospace;
    }
    .ts-link:hover {
        text-shadow: 0 0 8px #00ff88;
    }
    /* Mobile */
    @media (max-width: 768px) {
        .stApp { font-size: 14px; }
        .cs-card { padding: 10px; }
        .stButton>button { width: 100%; }
    }
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# CONFIG SYSTEM
# ==============================================================================

CONFIG_FILE = "config.json"
CONFIG_SAMPLE = "config_sample.json"

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def validate_webhook(url: str) -> bool:
    return bool(re.match(r'^https://discord\.com/api/webhooks/\d+/[\w-]+$', url.strip()))

def check_config() -> dict | None:
    cfg = load_config()
    if not cfg:
        return None
    webhooks = cfg.get("webhooks", {})
    has_valid = any(
        url and "PASTE_" not in url and validate_webhook(url)
        for url in webhooks.values()
    )
    return cfg if has_valid else None

# ==============================================================================
# SESSION STATE INIT
# ==============================================================================

def init_session():
    defaults = {
        "config": None,
        "page": "setup" if not check_config() else "home",
        "scan_results": [],
        "scan_running": False,
        "scan_progress": 0,
        "scan_total": 0,
        "scan_logs": [],
        "mobile": False,
        "channels_validated": {},
        "executor": "Operator",
        "current_lang": "id",
        "cookies_path": "",
        "scan_stop": False,
        "index_results": [],
        "chat_results": [],
        "age_results": [],
        "index_running": False,
        "age_running": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

# ==============================================================================
# YT-DLP HELPERS
# ==============================================================================

def run_ytdlp(args: list, timeout: int = 60) -> tuple[int, str, str]:
    cmd = ["yt-dlp"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "yt-dlp not found"

def validate_channel(channel: str, content_type: str = "all") -> dict | None:
    """
    content_type: "video" -> cuma cek tab /videos
                  "live"  -> cuma cek tab /streams
                  "all"   -> cek keduanya (default, kompatibel dgn caller lama)
    """
    ch = channel.lstrip("@").strip()
    if not ch:
        return None

    check_videos = content_type in ("all", "video")
    check_live   = content_type in ("all", "live")

    # Tab utama buat ambil nama channel: ikut pilihan content_type dulu,
    # baru fallback ke tab satunya kalau gagal (nama doang, harmless
    # dicoba dua-duanya walau content_type spesifik).
    primary_tab   = "streams" if content_type == "live" else "videos"
    fallback_tab  = "videos" if primary_tab == "streams" else "streams"

    def _fetch_name(tab: str):
        rc, out, _ = run_ytdlp([
            "--print", "channel",
            "--playlist-items", "1",
            "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/{tab}"
        ], timeout=15)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0].strip()
        return None

    name = _fetch_name(primary_tab) or _fetch_name(fallback_tab)

    video_count = 0
    if check_videos:
        rc2, out2, _ = run_ytdlp([
            "--flat-playlist", "--print", "playlist_count",
            "--playlist-items", "0", "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/videos"
        ], timeout=15)
        video_count = int(out2.strip()) if rc2 == 0 and out2.strip().isdigit() else 0

    live_count = 0
    if check_live:
        rc3, out3, _ = run_ytdlp([
            "--flat-playlist", "--print", "playlist_count",
            "--playlist-items", "0", "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/streams"
        ], timeout=15)
        live_count = int(out3.strip()) if rc3 == 0 and out3.strip().isdigit() else 0

    return {
        "username": ch,
        "name": name or ch,
        "video_count": video_count,
        "live_count": live_count,
        "valid": name is not None or video_count > 0 or live_count > 0
    }

def get_video_ids(channel: str, content_type: str, limit: int = 0) -> list[str]:
    ch = channel.lstrip("@").strip()
    ids = []

    if content_type in ("all", "video"):
        rc, out, _ = run_ytdlp([
            "--flat-playlist", "--print", "id",
            "--match-filter", "duration>60",
            *(["--playlist-end", str(limit)] if limit > 0 else []),
            "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/videos"
        ], timeout=60)
        if rc == 0:
            ids.extend([l.strip() for l in out.splitlines() if l.strip()])

    if content_type in ("all", "live"):
        rc, out, _ = run_ytdlp([
            "--flat-playlist", "--print", "id",
            *(["--playlist-end", str(limit)] if limit > 0 else []),
            "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/streams"
        ], timeout=60)
        if rc == 0:
            ids.extend([l.strip() for l in out.splitlines() if l.strip()])

    seen, result = set(), []
    for v in ids:
        if v not in seen and len(v) == 11:
            seen.add(v)
            result.append(v)

    return result[:limit] if limit > 0 else result

def get_video_title(video_id: str) -> str:
    rc, out, _ = run_ytdlp([
        "--print", "title", "--no-warnings", "--quiet",
        f"https://youtu.be/{video_id}"
    ], timeout=15)
    return out.strip() if rc == 0 else video_id

# ==============================================================================
# ENGINE SUBPROCESS RUNNER (shared oleh Index Mode & Age Bypass)
# ==============================================================================

def render_result_card(result: dict):
    """Render satu card temuan, konsisten dengan style page_search()."""
    kasta = result.get("kasta", "ZONK")
    card_class = {
        "GOD_MODE": "cs-card-god",
        "VALID_HIGH": "cs-card-high",
        "VALID": "cs-card-valid",
        "SILENT": "cs-card-silent",
    }.get(kasta, "cs-card-low")

    tc = result.get("tier_counts", {}) or {}
    badge_map = {"CORE": "badge-core", "TYPO": "badge-typo",
                 "SILENT": "badge-silent", "CONTEXT": "badge-ctx"}
    badges_html = "".join(
        f'<span class="badge {badge_map.get(t,"badge-ctx")}">{t} ×{c}</span>'
        for t, c in tc.items() if c
    )

    st.markdown(f"""
    <div class='cs-card {card_class}'>
        <h4>🎬 {result.get('title', result.get('video_id',''))}</h4>
        <p><code>@{result.get('channel','')}</code> ·
           <a href='https://youtu.be/{result.get('video_id','')}' target='_blank'>{result.get('video_id','')}</a></p>
        <p>Score: {result.get('persentase', 0)}% &nbsp; {badges_html}</p>
    </div>
    """, unsafe_allow_html=True)

    hits = result.get("hits", [])
    if hits:
        st.caption("🔗 Timestamp links:")
        for h in hits[:10]:
            st.markdown(
                f"<a class='ts-link' href='{h['url']}' target='_blank'>⏰ [{h['time']}] {h['text'][:80]}</a>",
                unsafe_allow_html=True
            )
        if len(hits) > 10:
            st.caption(f"... dan {len(hits)-10} momen lainnya")


def run_engine_live(cmd: list, results_key: str, progress_label_prefix: str = ""):
    """
    Jalankan engine (index/age) sebagai subprocess dengan --json-events,
    stream event JSON per baris ke UI secara live.
    Return list hasil match yang ditemukan (juga disimpan ke session_state[results_key]).
    """
    st.session_state[results_key] = []

    progress_bar  = st.progress(0)
    status_text   = st.empty()
    report_status = st.empty()
    results_area  = st.container()

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
    )

    raw_log_lines = []

    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")
        if not line.startswith("CS20JSON:"):
            raw_log_lines.append(line)
            continue

        try:
            ev = json.loads(line[len("CS20JSON:"):])
        except json.JSONDecodeError:
            continue

        etype = ev.get("type")

        if etype == "progress":
            done, total = ev.get("done", 0), ev.get("total", 1) or 1
            pct = min(done / total, 1.0)
            phase = ev.get("phase", "")
            batch = ev.get("batch")
            batch_str = f" batch {batch}" if batch else ""
            progress_bar.progress(pct)
            status_text.text(
                f"{progress_label_prefix}{ev.get('channel','')}{batch_str} "
                f"[{phase}] — {done}/{total}"
            )

        elif etype in ("batch_start",):
            status_text.info(f"📦 Mulai batch {ev.get('batch')} / {ev.get('total_batches')} "
                              f"untuk @{ev.get('channel','')}")

        elif etype == "batch_done":
            status_text.success(f"✅ Batch {ev.get('batch')} selesai ({ev.get('status')}).")

        elif etype == "match":
            with results_area:
                render_result_card(ev)
            st.session_state[results_key].append(ev)

        elif etype == "report_sent":
            report_status.success(f"📨 Laporan terkirim ke Discord ({ev.get('html_path','')}).")

        elif etype == "phase":
            status_text.info(f"➡️ {ev.get('phase')}")

        elif etype in ("all_done",):
            status_text.success("🏁 Selesai.")

    proc.wait()
    progress_bar.empty()

    if proc.returncode != 0 and raw_log_lines:
        with st.expander("⚠️ Log mentah (ada kemungkinan error, klik untuk lihat)"):
            st.code("\n".join(raw_log_lines[-60:]), language="text")

    return st.session_state[results_key]

# ==============================================================================
# UI COMPONENTS
# ==============================================================================

def render_header():
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("""
        <h1 style='color:#4fc3f7;margin:0;text-shadow:0 0 12px #4fc3f755;'>
        👑 CEGUKAN SEEKER <span style='color:#fff'>V20</span>
        </h1>
        <p style='color:#8b949e;margin:4px 0 0 0;font-size:13px;'>
        Fuzzy Regex Engine · Streamlit UI · 7 Bahasa
        </p>
        """, unsafe_allow_html=True)
    with col2:
        cfg = check_config()
        if cfg:
            st.success("✅ Config aktif", icon="🔑")
        else:
            st.error("❌ Config belum di-set", icon="⚠️")

def render_sidebar():
    with st.sidebar:
        st.markdown("<h3 style='color:#4fc3f7;'>🧭 NAVIGASI</h3>", unsafe_allow_html=True)

        pages = {
            "home": "🏠 Beranda",
            "search": "🔍 Search Instant",
            "index": "📦 Index Mode",
            "age": "🔞 Age Bypass",
            "chat": "💬 ChatSeeker",
            "settings": "⚙️ Settings",
        }

        for key, label in pages.items():
            if key in ("search", "index", "age", "chat") and not check_config():
                continue
            btn_type = "primary" if st.session_state.page == key else "secondary"
            if st.button(label, key=f"nav_{key}", use_container_width=True, type=btn_type):
                st.session_state.page = key
                st.rerun()

        st.divider()
        mobile = st.toggle("📱 Mode HP (Compact)", value=st.session_state.mobile)
        if mobile != st.session_state.mobile:
            st.session_state.mobile = mobile
            st.rerun()

# ==============================================================================
# PAGE: SETUP
# ==============================================================================

def page_setup():
    st.markdown("""
    <div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:8px 0;'>
    <h2 style='color:#ff7b72;'>🔐 SETUP WAJIB</h2>
    <p>Config webhook Discord belum di-set. Script tidak bisa jalan tanpa ini.</p>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🌐 Setup via Web", "💻 Setup via File"])

    with tab1:
        st.subheader("Masukkan Webhook Discord")
        st.info("""
        Cara dapat webhook:
        1. Buka Discord → Server kamu → Channel Settings
        2. Integrations → Webhooks → New Webhook
        3. Copy URL webhook, paste di bawah
        """)

        col1, col2 = st.columns(2)
        with col1:
            webhook_id = st.text_input("🇮🇩 Indonesia (ID)", placeholder="https://discord.com/api/webhooks/...")
            webhook_en = st.text_input("🇬🇧 English (EN)", placeholder="https://discord.com/api/webhooks/...")
            webhook_jp = st.text_input("🇯🇵 Japanese (JP)", placeholder="https://discord.com/api/webhooks/...")
        with col2:
            webhook_kr = st.text_input("🇰🇷 Korean (KR)", placeholder="https://discord.com/api/webhooks/...")
            webhook_in = st.text_input("🇮🇳 India/Hindi/Telugu (IN)", placeholder="https://discord.com/api/webhooks/...")

        if st.button("💾 Simpan Config", type="primary", use_container_width=True):
            webhooks = {}
            for name, url in [("id", webhook_id), ("en", webhook_en), ("jp", webhook_jp),
                              ("kr", webhook_kr), ("in", webhook_in)]:
                if url.strip() and validate_webhook(url.strip()):
                    webhooks[name] = url.strip()

            if not webhooks:
                st.error("❌ Minimal 1 webhook valid harus diisi!")
            else:
                save_config({"webhooks": webhooks})
                st.success("✅ Config tersimpan! Refresh halaman...")
                time.sleep(1)
                st.rerun()

    with tab2:
        st.subheader("Setup Manual via File")
        st.code("""
{
  "webhooks": {
    "id": "https://discord.com/api/webhooks/XXXX/YYYY",
    "en": "https://discord.com/api/webhooks/XXXX/YYYY",
    "jp": "https://discord.com/api/webhooks/XXXX/YYYY",
    "kr": "https://discord.com/api/webhooks/XXXX/YYYY",
    "in": "https://discord.com/api/webhooks/XXXX/YYYY"
  }
}
        """, language="json")

        if st.button("🔄 Cek Ulang Config", use_container_width=True):
            if check_config():
                st.success("✅ Config ditemukan!")
                st.rerun()
            else:
                st.error("❌ Config belum ditemukan atau tidak valid.")

# ==============================================================================
# PAGE: HOME
# ==============================================================================

def page_home():
    cfg = check_config()
    webhooks = cfg.get("webhooks", {}) if cfg else {}

    cols = st.columns(4 if not st.session_state.mobile else 2)
    with cols[0]:
        st.metric("🌐 Bahasa", "7", delta="ID EN JP KR IN TE TH")
    with cols[1]:
        st.metric("🔗 Webhooks", len(webhooks))
    with cols[2]:
        st.metric("⚡ Mode", "4 Aktif")
    with cols[3]:
        st.metric("📱 Device", "HP" if st.session_state.mobile else "PC")

    st.divider()
    st.subheader("⚡ Quick Actions")

    qcols = st.columns(2 if st.session_state.mobile else 4)
    with qcols[0]:
        if st.button("🔍 Search Instant", use_container_width=True, type="primary"):
            st.session_state.page = "search"
            st.rerun()
    with qcols[1]:
        if st.button("📦 Index Mode", use_container_width=True):
            st.session_state.page = "index"
            st.rerun()
    with qcols[2]:
        if st.button("🔞 Age Bypass", use_container_width=True):
            st.session_state.page = "age"
            st.rerun()
    with qcols[3]:
        if st.button("💬 ChatSeeker", use_container_width=True):
            st.session_state.page = "chat"
            st.rerun()

    st.divider()
    st.subheader("📋 Laporan Terbaru")

    report_files = sorted(glob.glob(".cs20/*.html"), reverse=True)[:5]
    if report_files:
        for rf in report_files:
            fname = os.path.basename(rf)
            size_kb = os.path.getsize(rf) / 1024
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.markdown(f"📄 `{fname}`")
            with col2:
                st.caption(f"{size_kb:.1f} KB")
            with col3:
                with open(rf, "r", encoding="utf-8") as f:
                    st.download_button("⬇️", f.read(), fname, mime="text/html", key=f"dl_{fname}")
    else:
        st.info("Belum ada laporan. Mulai scan untuk membuat laporan!")

# ==============================================================================
# PAGE: SEARCH INSTANT
# ==============================================================================

def page_search():
    st.markdown("""
    <h2 style='color:#4fc3f7;'>🔍 SEARCH INSTANT</h2>
    <p style='color:#8b949e;'>Scan cepat transcript video untuk deteksi cegukan.</p>
    """, unsafe_allow_html=True)

    cfg = check_config()
    if not cfg:
        st.error("❌ Config belum di-set!")
        return

    with st.container():
        st.subheader("📺 Target Channel")

        channels_input = st.text_area(
            "Username channel (pisahkan dengan koma atau newline):",
            placeholder="windahbasudara, jessnolimit, ...",
            help="Bisa banyak channel sekaligus! Warning muncul kalau >5."
        )

        channels = [c.strip().lstrip("@") for c in re.split(r'[,\n]', channels_input) if c.strip()]

        if len(channels) > 5:
            st.warning("⚠️ Lebih dari 5 channel! Risiko rate limit YouTube tinggi.")

        lang_cols = st.columns(2)
        with lang_cols[0]:
            lang_opts = [("id", "🇮🇩 Indonesia"), ("en", "🇬🇧 English"),
                        ("jp", "🇯🇵 Japanese"), ("kr", "🇰🇷 Korean"),
                        ("in", "🇮🇳 India (Hindi+Telugu)"), ("th", "🇹🇭 Thai")]
            lang = st.selectbox("🌐 Bahasa Engine:", lang_opts, format_func=lambda x: x[1])
            lang_code = lang[0]

        with lang_cols[1]:
            ct_opts = [("all", "🌐 Semua (kecuali Shorts)"),
                      ("video", "🎬 Video biasa"),
                      ("live", "🔴 Arsip Live Stream")]
            content_type = st.selectbox("🎬 Tipe Konten:", ct_opts, format_func=lambda x: x[1])
            content_type_code = content_type[0]

        if channels and st.button("✅ Validasi Channel", type="secondary"):
            with st.spinner("Validasi channel..."):
                for ch in channels:
                    info = validate_channel(ch, content_type_code)
                    st.session_state.channels_validated[ch] = info
                    if info and info["valid"]:
                        vid_display = f"{info['video_count']:,}" if content_type_code in ("all", "video") else "–"
                        live_display = f"{info['live_count']:,}" if content_type_code in ("all", "live") else "–"
                        st.success(f"✅ @{ch} → **{info['name']}** | Video: {vid_display} | Live: {live_display}")
                    else:
                        st.error(f"❌ @{ch} tidak ditemukan atau tidak valid!")

        validated = {k: v for k, v in st.session_state.channels_validated.items() if v and v.get("valid")}

        if validated:
            st.subheader("📊 Video Tersedia")
            for ch, info in validated.items():
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    st.write(f"**@{ch}** ({info['name']})")
                with col2:
                    available = info['video_count'] + info['live_count'] if content_type_code == "all" else \
                               info['live_count'] if content_type_code == "live" else info['video_count']
                    st.caption(f"{available:,} video tersedia")
                with col3:
                    limit = st.number_input(f"Limit", min_value=0, max_value=available or 1000,
                                           value=min(50, available or 50), key=f"limit_{ch}",
                                           help="0 = semua video")
                    # Catatan: JANGAN tulis ulang st.session_state[f"limit_{ch}"] di sini —
                    # widget dengan `key` yang sama sudah otomatis nyimpen nilainya ke
                    # session_state sendiri. Nulis ulang manual setelah widget dirender
                    # bikin StreamlitAPIException ("cannot be modified after the widget
                    # ... is instantiated"). `limit` (variabel lokal) sudah cukup dipakai
                    # langsung di bagian scan di bawah.

        jobs = st.slider("⚡ Workers (thread):", 1, 8, 4,
                        help="Lebih banyak = lebih cepat tapi lebih boros RAM. HP kentang pakai 2.")

        start_disabled = not validated or st.session_state.scan_running
        if st.button("🚀 MULAI SCAN", type="primary", use_container_width=True, disabled=start_disabled):
            st.session_state.scan_running = True
            st.session_state.scan_results = []
            st.session_state.scan_logs = []
            st.session_state.scan_stop = False
            st.rerun()

    if st.session_state.scan_running:
        st.divider()
        st.subheader("⏳ Progress Scan")

        progress_bar = st.progress(0)
        status_text = st.empty()

        for ch in validated.keys():
            if st.session_state.scan_stop:
                break

            limit = st.session_state.get(f"limit_{ch}", 50)
            if limit == 0:
                limit = 99999

            webhook_url = cfg.get("webhooks", {}).get(lang_code, "")
            status_text.text(f"🔍 Scanning @{ch}...")

            video_ids = get_video_ids(ch, content_type_code, limit)

            if not video_ids:
                st.warning(f"⚠️ Tidak ada video ditemukan untuk @{ch}")
                continue

            import sys
            sys.path.insert(0, os.path.dirname(__file__))
            from cs20_engine import _init_lang, analyze_video
            _init_lang(lang_code)

            for i, vid in enumerate(video_ids):
                if st.session_state.scan_stop:
                    break

                progress = (i + 1) / len(video_ids)
                progress_bar.progress(min(progress, 0.99))
                status_text.text(f"🔍 @{ch}: {vid} ({i+1}/{len(video_ids)})")

                result = analyze_video(vid, ch)
                title = get_video_title(vid)

                if result.get("is_valid") or result.get("status") == "analyzed":
                    st.session_state.scan_results.append({
                        "channel": ch,
                        "video_id": vid,
                        "title": title,
                        "kasta": result.get("kasta", "ZONK"),
                        "kasta_label": result.get("kasta_label", ""),
                        "persentase": result.get("persentase", 0),
                        "hits": result.get("hits", []),
                        "score": result.get("score", 0),
                    })

                time.sleep(0.3)

            progress_bar.empty()
            status_text.success(f"✅ @{ch} selesai! {len(video_ids)} video diperiksa.")

        st.session_state.scan_running = False

        if st.session_state.scan_results:
            st.divider()
            st.subheader(f"🎯 {len(st.session_state.scan_results)} TEMUAN!")

            sorted_results = sorted(st.session_state.scan_results,
                                   key=lambda x: x.get("score", 0), reverse=True)

            for r in sorted_results:
                kasta = r["kasta"]
                card_class = {
                    "GOD_MODE": "cs-card-god",
                    "VALID_HIGH": "cs-card-high",
                    "VALID": "cs-card-valid",
                    "SILENT": "cs-card-silent",
                }.get(kasta, "cs-card-low")

                st.markdown(f"""
                <div class='cs-card {card_class}'>
                    <h4>🎬 {r['title']}</h4>
                    <p><code>@{r['channel']}</code> · <a href='https://youtu.be/{r['video_id']}' target='_blank'>{r['video_id']}</a></p>
                    <p><b>{r['kasta_label']}</b> · Score: {r['persentase']}%</p>
                </div>
                """, unsafe_allow_html=True)

                if r.get("hits"):
                    st.caption("🔗 Timestamp links:")
                    for h in r["hits"][:10]:
                        st.markdown(f"<a class='ts-link' href='{h['url']}' target='_blank'>⏰ [{h['time']}] {h['text'][:80]}</a>",
                                   unsafe_allow_html=True)
                    if len(r["hits"]) > 10:
                        st.caption(f"... dan {len(r['hits'])-10} momen lainnya")
        else:
            st.info("Tidak ada temuan cegukan di channel ini.")

    if st.session_state.scan_running:
        if st.button("⏹️ STOP SCAN", type="secondary", use_container_width=True):
            st.session_state.scan_stop = True
            st.rerun()

# ==============================================================================
# PAGE: INDEX MODE
# ==============================================================================

def page_index():
    st.markdown("""
    <h2 style='color:#4fc3f7;'>📦 INDEX MODE</h2>
    <p style='color:#8b949e;'>Untuk channel besar (1000+ video). Download batch + search manual.</p>
    """, unsafe_allow_html=True)

    cfg = check_config()
    if not cfg:
        st.error("❌ Config belum di-set!")
        return

    st.info("""
    📦 **Cara kerja Index Mode:**
    1. Video dibagi per batch (misal: 100 video/batch)
    2. Download subtitle semua video di batch
    3. Analisis otomatis + search manual dengan keyword bebas
    4. Hasil dikirim ke Discord
    """)

    channel = st.text_input("📺 Channel target:", placeholder="nama_channel").strip().lstrip("@")

    if channel:
        if st.button("✅ Validasi"):
            with st.spinner("Validasi..."):
                info = validate_channel(channel)
                if info and info["valid"]:
                    st.success(f"✅ **{info['name']}** | Video: {info['video_count']:,} | Live: {info['live_count']:,}")
                else:
                    st.error("❌ Channel tidak valid!")

        st.subheader("⚙️ Konfigurasi Batch")
        col1, col2 = st.columns(2)
        with col1:
            total_videos = st.number_input("Total video estimasi:", min_value=10, value=1000)
            batch_size = st.number_input("Video per batch:", min_value=10, max_value=500, value=100)
        with col2:
            workers = st.slider("Workers:", 1, 3, 2)
            ct_opts = [("all", "Semua"), ("video", "Video"), ("live", "Live")]
            content_type = st.selectbox("Tipe:", ct_opts, format_func=lambda x: x[1])[0]

        total_batches = (total_videos + batch_size - 1) // batch_size
        st.caption(f"📦 {total_batches} batch total (~{batch_size} video/batch)")

        batches_per_run = st.number_input(
            "Batch diproses per klik run:", min_value=1, max_value=total_batches, value=min(2, total_batches),
            help="Index Mode berat — kirim beberapa batch saja per klik, sisanya lanjut nanti (resume otomatis)."
        )
        start_batch = st.number_input("Mulai dari batch ke-:", min_value=1, max_value=total_batches, value=1)

        run_disabled = st.session_state.get("index_running", False)
        if st.button("🚀 Mulai Index Mode", type="primary", use_container_width=True, disabled=run_disabled):
            script_dir  = os.path.dirname(os.path.abspath(__file__))
            engine_path = os.path.join(script_dir, "cs20_index_engine.py")

            if not os.path.exists(engine_path):
                st.error(f"❌ cs20_index_engine.py tidak ditemukan di {engine_path}")
                return

            webhook_url = cfg.get("webhooks", {}).get(st.session_state.get("current_lang", "id"), "")

            cmd = [
                "python3", engine_path,
                "--channel", channel,
                "--content-type", content_type,
                "--executor", cfg.get("executor", "Operator"),
                "--lang", st.session_state.get("current_lang", "id"),
                "--webhook-url", webhook_url,
                "--config-dir", ".cs20",
                "--jobs", str(workers),
                "--total-videos", str(total_videos),
                "--total-batches", str(total_batches),
                "--batches-per-run", str(batches_per_run),
                "--start-batch", str(start_batch),
                "--json-events",
            ]

            st.session_state.index_running = True
            st.divider()
            st.subheader("⏳ Progress Index Mode")
            run_engine_live(cmd, "index_results", progress_label_prefix="📦 ")
            st.session_state.index_running = False

            results = st.session_state.get("index_results", [])
            if not results:
                st.info("Tidak ada temuan cegukan di batch yang diproses.")

            st.caption(
                "ℹ️ Search manual per-keyword bebas belum tersedia dari UI ini "
                "(otomatis di-skip). Batch otomatis lanjut & cache dibersihkan. "
                "Kalau butuh search manual, jalankan `cs20_index_engine.py` "
                "langsung dari terminal (tanpa `--json-events`)."
            )

# ==============================================================================
# PAGE: AGE BYPASS
# ==============================================================================

def page_age():
    st.markdown("""
    <h2 style='color:#ff7b72;'>🔞 AGE BYPASS</h2>
    <p style='color:#8b949e;'>Bypass video age-restricted via cookies.</p>
    """, unsafe_allow_html=True)

    cfg = check_config()
    if not cfg:
        st.error("❌ Config belum di-set!")
        return

    st.subheader("🍪 Cookies Setup")

    cookies_file = st.file_uploader("Upload cookies.txt (Netscape format):", type=["txt"])

    if cookies_file:
        os.makedirs(".cs20", exist_ok=True)
        with open(".cs20/cookies.txt", "wb") as f:
            f.write(cookies_file.getvalue())
        st.success("✅ Cookies tersimpan!")

    st.caption("Atau jalankan di Termux: `bash setup_cookies.sh`")

    channel = st.text_input("📺 Channel target (wajib diisi):", placeholder="nama_channel").strip().lstrip("@")

    col1, col2 = st.columns(2)
    with col1:
        ct_opts = [("all", "🌐 Semua (kecuali Shorts)"), ("video", "🎬 Video biasa"), ("live", "🔴 Live")]
        content_type = st.selectbox("Tipe konten:", ct_opts, format_func=lambda x: x[1])[0]
        limit = st.number_input("Jumlah video terakhir:", min_value=1, value=50)
    with col2:
        jobs = st.select_slider(
            "Kecepatan (worker):", options=[1, 2, 3, 4], value=2,
            help="1=paling aman utk cookies, 4=paling cepat tapi rawan rate limit"
        )

    st.info(
        "ℹ️ Mode UI ini selalu mulai **sesi baru** dari channel di atas — pilihan "
        "resume dari log lama/pending hanya tersedia lewat CLI (`cs20_age_engine.py` "
        "tanpa `--json-events`)."
    )

    run_disabled = not channel or st.session_state.get("age_running", False)
    if st.button("🔞 Mulai Age Bypass", type="primary", use_container_width=True, disabled=run_disabled):
        script_dir  = os.path.dirname(os.path.abspath(__file__))
        engine_path = os.path.join(script_dir, "cs20_age_engine.py")

        if not os.path.exists(engine_path):
            st.error(f"❌ cs20_age_engine.py tidak ditemukan di {engine_path}")
            return

        webhook_url = cfg.get("webhooks", {}).get(st.session_state.get("current_lang", "id"), "")

        cmd = [
            "python3", engine_path,
            "--channel", channel,
            "--content-type", content_type,
            "--limit", str(limit),
            "--executor", cfg.get("executor", "Operator"),
            "--lang", st.session_state.get("current_lang", "id"),
            "--webhook-url", webhook_url,
            "--config-dir", ".cs20",
            "--jobs", str(jobs),
            "--json-events",
        ]

        st.session_state.age_running = True
        st.divider()
        st.subheader("⏳ Progress Age Bypass")
        run_engine_live(cmd, "age_results", progress_label_prefix="🔞 ")
        st.session_state.age_running = False

        if not st.session_state.get("age_results"):
            st.info("Tidak ada temuan cegukan di video yang di-bypass.")

# ==============================================================================
# PAGE: CHATSEEKER
# ==============================================================================

def page_chat():
    st.markdown("""
    <h2 style='color:#58a6ff;'>💬 CHATSEEKER</h2>
    <p style='color:#8b949e;'>Miner live chat replay untuk deteksi momen cegukan.</p>
    """, unsafe_allow_html=True)

    cfg = check_config()
    if not cfg:
        st.error("❌ Config belum di-set!")
        return

    channel = st.text_input("📺 Channel target:", placeholder="nama_channel").strip().lstrip("@")

    if channel:
        if st.button("✅ Validasi"):
            with st.spinner("Validasi..."):
                info = validate_channel(channel, "live")
                if info and info["valid"]:
                    st.success(f"✅ **{info['name']}** | Live: {info['live_count']:,}")
                else:
                    st.error("❌ Channel tidak valid!")

        filter_opts = [("ALL", "Semua arsip live"), ("LIMIT", "Limit N video"),
                      ("CHECKPOINT", "Lanjut dari checkpoint")]
        filter_mode = st.selectbox("Filter:", filter_opts, format_func=lambda x: x[1])[0]

        if filter_mode == "LIMIT":
            max_vid = st.number_input("Jumlah video:", min_value=1, value=50)
        else:
            max_vid = 0

        if st.button("💬 Mulai ChatSeeker", type="primary", use_container_width=True):
            st.info("⏳ ChatSeeker berjalan...")

            import sys
            script_dir = os.path.dirname(__file__)
            engine_path = os.path.join(script_dir, "chatseeker.py")

            if not os.path.exists(engine_path):
                st.error(f"❌ chatseeker.py tidak ditemukan di {engine_path}")
                return

            st.info("💬 ChatSeeker dijalankan sebagai standalone. Output akan muncul di terminal Termux.")
            st.code(f"python3 {engine_path}", language="bash")

# ==============================================================================
# PAGE: SETTINGS
# ==============================================================================

def page_settings():
    st.markdown("""
    <h2 style='color:#4fc3f7;'>⚙️ SETTINGS</h2>
    """, unsafe_allow_html=True)

    cfg = load_config() or {"webhooks": {}}

    tab1, tab2, tab3 = st.tabs(["🔑 Webhooks", "👤 Profile", "🍪 Cookies"])

    with tab1:
        st.subheader("Discord Webhooks")
        webhooks = cfg.get("webhooks", {})

        cols = st.columns(2)
        with cols[0]:
            id_url = st.text_input("🇮🇩 Indonesia (ID):", webhooks.get("id", ""))
            en_url = st.text_input("🇬🇧 English (EN):", webhooks.get("en", ""))
            jp_url = st.text_input("🇯🇵 Japanese (JP):", webhooks.get("jp", ""))
        with cols[1]:
            kr_url = st.text_input("🇰🇷 Korean (KR):", webhooks.get("kr", ""))
            in_url = st.text_input("🇮🇳 India (IN):", webhooks.get("in", ""))

        if st.button("💾 Simpan Webhooks", type="primary"):
            new_hooks = {}
            for name, url in [("id", id_url), ("en", en_url), ("jp", jp_url),
                              ("kr", kr_url), ("in", in_url)]:
                if url.strip() and validate_webhook(url.strip()):
                    new_hooks[name] = url.strip()

            cfg["webhooks"] = new_hooks
            save_config(cfg)
            st.success("✅ Webhooks tersimpan!")

    with tab2:
        st.subheader("👤 Profile")
        executor = st.text_input("Nama Operator:", cfg.get("executor", "Operator"))
        if st.button("💾 Simpan Profile"):
            cfg["executor"] = executor
            save_config(cfg)
            st.success("✅ Profile tersimpan!")

    with tab3:
        st.subheader("🍪 YouTube Cookies")
        st.info("""
        Cookies digunakan untuk:
        - Menghindari rate limit YouTube
        - Bypass age-restricted videos

        Cara setup:
        1. Install ekstensi 'Get cookies.txt LOCALLY' di Chrome
        2. Login ke YouTube
        3. Export cookies.txt (Netscape format)
        4. Upload di sini atau taruh di `.cs20/cookies.txt`
        """)

        cookies = st.file_uploader("Upload cookies.txt:", type=["txt"])
        if cookies:
            os.makedirs(".cs20", exist_ok=True)
            with open(".cs20/cookies.txt", "wb") as f:
                f.write(cookies.getvalue())
            st.success("✅ Cookies tersimpan!")

        if os.path.exists(".cs20/cookies.txt"):
            st.caption("✅ cookies.txt ditemukan di .cs20/cookies.txt")
        else:
            st.caption("❌ cookies.txt belum ada")

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    render_header()

    if not check_config():
        page_setup()
        return

    render_sidebar()

    page = st.session_state.get("page", "home")

    if page == "home":
        page_home()
    elif page == "search":
        page_search()
    elif page == "index":
        page_index()
    elif page == "age":
        page_age()
    elif page == "chat":
        page_chat()
    elif page == "settings":
        page_settings()
    else:
        page_home()

if __name__ == "__main__":
    main()
