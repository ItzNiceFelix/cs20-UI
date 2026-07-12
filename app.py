"""
Cegukan Seeker V20 — Web UI (Streamlit)
Menjalankan cs20_engine.py sebagai subprocess per channel (sequential FIFO),
video di dalam channel tetap diproses paralel (ThreadPoolExecutor di engine).
Live dashboard baca event JSON dari stdout engine.
"""

import json
import os
import subprocess
import sys
import time

import streamlit as st

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
ENGINE_PATH  = os.path.join(SCRIPT_DIR, "cs20_engine.py")
CONFIG_DIR   = os.path.join(SCRIPT_DIR, ".cs20")
CHECKPOINT_DIR = os.path.join(CONFIG_DIR, "checkpoints")
CONFIG_JSON  = os.path.join(SCRIPT_DIR, "config.json")

os.makedirs(CHECKPOINT_DIR, exist_ok=True)

LANG_OPTIONS = {
    "id": "🇮🇩 Indonesia",
    "en": "🇬🇧 Inggris",
    "jp": "🇯🇵 Jepang",
    "kr": "🇰🇷 Korea",
    "in": "🇮🇳 India (Hindi + Telugu, auto-detect)",
}

TIER_COLORS = {
    "CORE":    "#e74c3c",  # merah
    "TYPO":    "#f39c12",  # oranye/kuning
    "SILENT":  "#9b59b6",  # ungu
    "CONTEXT": "#3498db",  # biru/cyan
    "FP":      "#7f8c8d",  # abu-abu
}
TIER_ORDER = ["CORE", "TYPO", "SILENT", "CONTEXT", "FP"]


# ──────────────────────────────────────────────────────────────────────────
# CONFIG / WEBHOOK
# ──────────────────────────────────────────────────────────────────────────
def load_webhooks():
    if os.path.exists(CONFIG_JSON):
        try:
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                return json.load(f).get("webhooks", {})
        except Exception:
            return {}
    return {}


# ──────────────────────────────────────────────────────────────────────────
# CHANNEL VALIDATION
# ──────────────────────────────────────────────────────────────────────────
def validate_channel(handle: str) -> dict:
    """Cek cepat apakah @handle valid via yt-dlp, ambil nama channel asli."""
    handle = handle.strip().lstrip("@")
    if not handle:
        return {"valid": False, "name": "", "error": "Kosong"}
    try:
        cmd = [
            "yt-dlp", "--flat-playlist", "--playlist-items", "1",
            "--print", "%(channel)s", "--quiet", "--no-warnings",
            f"https://www.youtube.com/@{handle}/videos",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        name = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if name:
            return {"valid": True, "name": name, "error": ""}
        return {"valid": False, "name": "", "error": "Channel tidak ditemukan"}
    except subprocess.TimeoutExpired:
        return {"valid": False, "name": "", "error": "Timeout — cek koneksi"}
    except Exception as e:
        return {"valid": False, "name": "", "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ──────────────────────────────────────────────────────────────────────────
if "channels" not in st.session_state:
    st.session_state.channels = []  # list of {"handle","name","valid","error"}
if "running" not in st.session_state:
    st.session_state.running = False
if "theme" not in st.session_state:
    st.session_state.theme = "dark"


# ──────────────────────────────────────────────────────────────────────────
# THEME (dark / light toggle)
# ──────────────────────────────────────────────────────────────────────────
def inject_theme_css(theme: str):
    if theme == "dark":
        bg, card_bg, text, sub, border = "#0e1117", "#1a1d24", "#f0f0f0", "#9aa0a6", "#2a2e37"
    else:
        bg, card_bg, text, sub, border = "#ffffff", "#f7f7f9", "#111111", "#5f6368", "#e2e2e6"

    st.markdown(f"""
    <style>
        .stApp {{ background-color: {bg}; color: {text}; }}
        .cs20-card {{
            background-color: {card_bg};
            border: 1px solid {border};
            border-radius: 10px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }}
        .cs20-title {{ font-weight: 700; font-size: 1.02rem; color: {text}; }}
        .cs20-sub {{ color: {sub}; font-size: 0.82rem; margin-bottom: 6px; }}
        .cs20-hit {{
            border-left: 3px solid #555;
            padding: 4px 10px;
            margin: 6px 0;
            font-size: 0.88rem;
            color: {text};
        }}
        .cs20-badge {{
            display: inline-block; padding: 1px 8px; border-radius: 6px;
            font-size: 0.72rem; font-weight: 700; color: white; margin-right: 4px;
        }}
        .cs20-lang-badge {{
            display: inline-block; padding: 1px 8px; border-radius: 6px;
            font-size: 0.72rem; font-weight: 700; background: #34495e; color: white;
        }}
    </style>
    """, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────
# UI — SIDEBAR
# ──────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Cegukan Seeker V20 — Web UI", page_icon="👑", layout="wide")

with st.sidebar:
    st.markdown("## 👑 Cegukan Seeker V20")
    st.session_state.theme = st.radio("Tema", ["dark", "light"], horizontal=True,
                                       index=0 if st.session_state.theme == "dark" else 1)
    st.divider()

    executor = st.text_input("Nama alias (buat laporan Discord)", value=st.session_state.get("executor", ""))
    st.session_state.executor = executor

    lang_choice = st.selectbox("Bahasa engine", options=list(LANG_OPTIONS.keys()),
                                format_func=lambda k: LANG_OPTIONS[k])

    st.divider()
    st.markdown("### 📺 Tambah Channel")
    new_handle = st.text_input("Username channel (tanpa @)", key="new_channel_input")
    if st.button("➕ Tambah ke antrian", use_container_width=True, disabled=st.session_state.running):
        if new_handle.strip():
            with st.spinner(f"Memeriksa @{new_handle.strip()}..."):
                res = validate_channel(new_handle)
            st.session_state.channels.append({
                "handle": new_handle.strip().lstrip("@"),
                "name": res["name"],
                "valid": res["valid"],
                "error": res["error"],
                "status": "queued",
            })

    if st.session_state.channels:
        st.markdown("#### Antrian channel")
        for i, ch in enumerate(st.session_state.channels):
            badge = "✅" if ch["valid"] else "❌"
            label = ch["name"] if ch["valid"] else (ch["error"] or "invalid")
            colc1, colc2 = st.columns([5, 1])
            with colc1:
                st.write(f"{badge} **@{ch['handle']}** — {label}  `{ch.get('status','queued')}`")
            with colc2:
                if st.button("🗑", key=f"del_{i}", disabled=st.session_state.running):
                    st.session_state.channels.pop(i)
                    st.rerun()

inject_theme_css(st.session_state.theme)

st.title("👑 Cegukan Seeker V20 — Web UI")
st.caption("Mode: Pantau (video paralel per channel, channel sequential FIFO)")

valid_channels = [c for c in st.session_state.channels if c["valid"]]
start_disabled = st.session_state.running or not valid_channels or not executor.strip()
start_col, info_col = st.columns([1, 4])
with start_col:
    start_clicked = st.button("▶️ Mulai Scan", type="primary", use_container_width=True,
                               disabled=start_disabled)
with info_col:
    if not executor.strip():
        st.caption("⚠️ Isi nama alias dulu di sidebar.")
    elif not valid_channels:
        st.caption("⚠️ Tambahkan minimal 1 channel valid ke antrian.")

status_placeholder   = st.empty()
progress_placeholder = st.empty()
results_container    = st.container()


# ──────────────────────────────────────────────────────────────────────────
# RENDER ONE MATCH CARD
# ──────────────────────────────────────────────────────────────────────────
def render_match_card(container, ev: dict):
    tc = ev.get("tier_counts", {}) or {}
    badges_html = ""
    for tier in TIER_ORDER:
        cnt = tc.get(tier, 0)
        if cnt:
            color = TIER_COLORS.get(tier, "#888")
            badges_html += f'<span class="cs20-badge" style="background:{color}">{tier} ×{cnt}</span>'

    lang_tag = ev.get("transcript_lang", "") or "?"
    hits_html = ""
    for h in ev.get("hits", []):
        top_tier = next((t for t in TIER_ORDER if h.get("tiers", {}).get(t, 0)), None)
        color = TIER_COLORS.get(top_tier, "#555")
        hits_html += (
            f'<div class="cs20-hit" style="border-left-color:{color}">'
            f'⏱ <b>{h["time"]}</b> — {h["text"]} '
            f'<a href="{h["url"]}" target="_blank">↗ buka</a></div>'
        )

    html = f"""
    <div class="cs20-card">
      <div class="cs20-title">📹 {ev.get('title','(tanpa judul)')}</div>
      <div class="cs20-sub">
        <span class="cs20-lang-badge">{lang_tag.upper()}</span>
        &nbsp; @{ev.get('channel','')} &nbsp;|&nbsp; skor: {ev.get('persentase', 0)}%
        &nbsp; {badges_html}
      </div>
      {hits_html}
    </div>
    """
    container.markdown(html, unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────
# RUN ENGINE (sequential FIFO across channels)
# ──────────────────────────────────────────────────────────────────────────
def run_all_channels():
    webhooks = load_webhooks()
    webhook_url = webhooks.get(lang_choice, "")

    total_channels = len(valid_channels)
    for idx, ch in enumerate(valid_channels, start=1):
        handle = ch["handle"]
        status_placeholder.info(f"🔴 Memproses channel {idx}/{total_channels}: **@{handle}**")

        cmd = [
            sys.executable, ENGINE_PATH,
            "--channel", handle,
            "--executor", executor.strip(),
            "--lang", lang_choice,
            "--mode", "pantau",
            "--config-dir", CONFIG_DIR,
            "--checkpoint-dir", CHECKPOINT_DIR,
            "--webhook-url", webhook_url,
            "--json-events",
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=SCRIPT_DIR,
        )

        card_slots = []
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line.startswith("CS20JSON:"):
                continue
            try:
                ev = json.loads(line[len("CS20JSON:"):])
            except json.JSONDecodeError:
                continue

            etype = ev.get("type")

            if etype == "progress":
                done, total = ev.get("done", 0), ev.get("total", 1) or 1
                pct = int(done / total * 100)
                progress_placeholder.progress(
                    min(pct, 100),
                    text=f"@{handle} — {done}/{total} video ({ev.get('hits_total',0)} hit)"
                )

            elif etype == "match":
                slot = results_container.empty()
                render_match_card(slot, ev)
                card_slots.append(slot)

            elif etype == "report_sent":
                status_placeholder.success(f"✅ Laporan @{handle} terkirim ke Discord.")

            elif etype == "rate_limit_stop":
                status_placeholder.warning(f"⏸️ Rate limit — @{handle} dihentikan darurat.")

        proc.wait()

    status_placeholder.success("🏁 Semua channel di antrian selesai diproses.")
    progress_placeholder.empty()
    st.session_state.running = False


if start_clicked and not start_disabled:
    st.session_state.running = True
    run_all_channels()
