"""
Cegukan Seeker V20 — Web UI (Streamlit) — Multi-Page
Basis: app.py lama (Search Instant / Pantau), diperluas dengan Index Mode,
Age Bypass, dan Settings, mengadaptasi bagian terbaik dari cs20_ui.py (Kimi).

Semua mode dijalankan sebagai subprocess engine dengan --json-events, dan
diparsing lewat run_engine_live() generik (pola konsisten, bukan import
langsung seperti versi Kimi yang lama).

CHANGELOG (audit fix):
- Fix: cfg_cache tidak pernah di-invalidate setelah save_config() -> user
  kelihatan "ke-reset" ke gate setup padahal file sudah tersimpan benar.
- Fix: antrian channel & hasil scan sekarang dipersist ke disk (queue.json)
  supaya tidak hilang total kalau browser reload di tengah throttle/cooldown.
- Fix: escape semua field dari data eksternal (judul video, teks transcript,
  channel, url) sebelum di-render sebagai HTML mentah -> cegah broken layout
  / HTML-injection dari data YouTube.
- Fix: tidak ada opsi "sesi baru" setelah scan selesai -> sekarang ada tombol
  "Mulai Sesi Baru" yang wajib ditekan sebelum bisa scan ulang.
- Fix: hapus form edit config manual sepenuhnya -> config HANYA masuk lewat
  upload config.json (satu sumber kebenaran, tidak ada webhook manual yang
  tidak divalidasi ulang).
- Fix: navigasi utama pindah dari sidebar-button ke st.tabs() di area utama
  -> di Android, sidebar collapsed sering bikin menu "hilang". Tabs selalu
  penuh terlihat baik di desktop maupun mobile.
"""

import html
import json
import os
import re
import subprocess
import sys
import time

import streamlit as st

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
ENGINE_PANTAU  = os.path.join(SCRIPT_DIR, "cs20_engine.py")
ENGINE_INDEX   = os.path.join(SCRIPT_DIR, "cs20_index_engine.py")
ENGINE_AGE     = os.path.join(SCRIPT_DIR, "cs20_age_engine.py")
ENGINE_CHAT    = os.path.join(SCRIPT_DIR, "chatseeker.py")
CONFIG_DIR     = os.path.join(SCRIPT_DIR, ".cs20")
CHECKPOINT_DIR = os.path.join(CONFIG_DIR, "checkpoints")
CONFIG_JSON    = os.path.join(SCRIPT_DIR, "config.json")
COOKIES_PATH   = os.path.join(CONFIG_DIR, "cookies.txt")
QUEUE_JSON     = os.path.join(CONFIG_DIR, "queue.json")

os.makedirs(CHECKPOINT_DIR, exist_ok=True)

LANG_OPTIONS = {
    "id": "🇮🇩 Indonesia",
    "en": "🇬🇧 Inggris",
    "jp": "🇯🇵 Jepang",
    "kr": "🇰🇷 Korea",
    "in": "🇮🇳 India (Hindi + Telugu, auto-detect)",
}

CONTENT_TYPE_OPTIONS = {
    "all":   "🌐 Semua (kecuali Shorts)",
    "video": "🎬 Video biasa",
    "live":  "🔴 Arsip Live Stream",
}

TIER_COLORS = {
    "CORE":    "#e74c3c",
    "TYPO":    "#f39c12",
    "SILENT":  "#9b59b6",
    "CONTEXT": "#3498db",
    "FP":      "#7f8c8d",
}
TIER_ORDER = ["CORE", "TYPO", "SILENT", "CONTEXT", "FP"]

KASTA_BORDER = {
    "GOD_MODE":   "#ffd700",
    "VALID_HIGH": "#ff3c3c",
    "VALID":      "#00ff88",
    "SILENT":     "#a020f0",
}
KASTA_DEFAULT_BORDER = "#445566"

LEVEL_COLORS = {4: "#ff3c3c", 3: "#ff7b72", 2: "#f0c419", 1: "#8b949e"}
LEVEL_LABELS = {4: "🔥 LVL4-HYPE", 3: "🔴 LVL3-TINGGI", 2: "🟡 LVL2-SEDANG", 1: "⚪ LVL1-RENDAH"}
CHECKPOINT_DIR_CHAT = os.path.join(CONFIG_DIR, "cs_checkpoints")
os.makedirs(CHECKPOINT_DIR_CHAT, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════
# CONFIG SYSTEM (webhook.json) — HANYA via upload, tidak ada edit manual
# ══════════════════════════════════════════════════════════════════════════
def load_config() -> dict | None:
    if not os.path.exists(CONFIG_JSON):
        return None
    try:
        with open(CONFIG_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(cfg: dict):
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    with open(CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def validate_webhook(url: str) -> bool:
    # Terima discord.com & legacy discordapp.com, boleh ada query string (?wait=true dll).
    return bool(re.match(
        r'^https://(discord|discordapp)\.com/api/webhooks/\d+/[\w-]+(\?.*)?$',
        url.strip()
    ))


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


def get_cfg(force_recheck: bool = False) -> dict | None:
    """Config di-cache di session_state sekali per sesi, BUKAN di-baca ulang
    dari disk di setiap rerun. Ini mencegah interaksi widget yang sama sekali
    tidak berhubungan dengan config (mis. hapus channel di antrian) tiba-tiba
    melempar balik ke gate setup gara-gara flakiness baca file sesaat.

    PENTING: dipanggil ulang secara eksplisit (force_recheck=True) SETIAP kali
    setelah save_config() dipanggil (upload config baru), supaya cache tidak
    "nyangkut" ke nilai lama (bug lama: user isi config, ke-save benar ke
    disk, tapi UI tetap balik ke gate setup karena cache tidak di-refresh).
    """
    if force_recheck or "cfg_cache" not in st.session_state:
        st.session_state.cfg_cache = check_config()
    return st.session_state.cfg_cache


# ══════════════════════════════════════════════════════════════════════════
# PERSISTENSI ANTRIAN CHANNEL (queue.json) — supaya tidak hilang saat reload
# ══════════════════════════════════════════════════════════════════════════
def load_queue() -> list:
    if not os.path.exists(QUEUE_JSON):
        return []
    try:
        with open(QUEUE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_queue(channels: list):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(QUEUE_JSON, "w", encoding="utf-8") as f:
            json.dump(channels, f, indent=2, ensure_ascii=False)
    except Exception:
        pass  # gagal simpan antrian bukan fatal, cukup diamkan


def clear_queue():
    st.session_state.channels = []
    save_queue([])


# ══════════════════════════════════════════════════════════════════════════
# CHANNEL VALIDATION (diadaptasi dari cs20_ui.py, poin 10 catatan lanjutan)
# ══════════════════════════════════════════════════════════════════════════
def _run_ytdlp(args: list, timeout: int = 30) -> tuple[int, str, str]:
    cmd = ["yt-dlp"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "yt-dlp not found"


def validate_channel(channel: str, content_type: str = "all") -> dict:
    """Respect content_type — jangan selalu cek /videos DAN /streams.
    Jangan pakai playlist_count (tidak reliable) — hitung ID beneran, cap 200.
    """
    ch = channel.strip().lstrip("@")
    if not ch:
        return {"valid": False, "name": "", "error": "Kosong"}

    check_videos = content_type in ("all", "video")
    check_live   = content_type in ("all", "live")

    primary_tab  = "streams" if content_type == "live" else "videos"
    fallback_tab = "videos" if primary_tab == "streams" else "streams"

    def _fetch_name(tab: str):
        rc, out, _ = _run_ytdlp([
            "--print", "channel", "--playlist-items", "1",
            "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/{tab}"
        ], timeout=15)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0].strip()
        return None

    name = _fetch_name(primary_tab) or _fetch_name(fallback_tab)

    def _approx_count(tab: str, cap: int = 200) -> tuple[int, bool]:
        rc, out, _ = _run_ytdlp([
            "--flat-playlist", "--print", "id",
            "--playlist-end", str(cap),
            "--no-warnings", "--quiet",
            f"https://www.youtube.com/@{ch}/{tab}"
        ], timeout=30)
        if rc != 0:
            return 0, False
        ids = [l for l in out.splitlines() if l.strip()]
        return len(ids), len(ids) >= cap

    video_count, video_capped = _approx_count("videos") if check_videos else (0, False)
    live_count, live_capped   = _approx_count("streams") if check_live else (0, False)

    valid = name is not None or video_count > 0 or live_count > 0
    return {
        "valid": valid,
        "name": name or ch,
        "video_count": video_count, "video_capped": video_capped,
        "live_count": live_count, "live_capped": live_capped,
        "error": "" if valid else "Channel tidak ditemukan",
    }


# ══════════════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "page": "search",
    "theme": "dark",
    "channels": None,          # diisi dari queue.json di awal main()
    "running": False,
    "session_done": False,     # True setelah 1 sesi scan selesai -> gate "Sesi Baru"
    "index_running": False,
    "age_running": False,
    "chat_running": False,
    "search_results_tmp": [],
    "index_results": [],
    "age_results": [],
    "chat_results": [],
    "executor": "",
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Load antrian channel dari disk hanya sekali di awal sesi (bukan tiap rerun,
# supaya perubahan di memory sesi berjalan tidak ketimpa balik oleh file lama).
if st.session_state.channels is None:
    st.session_state.channels = load_queue()


# ══════════════════════════════════════════════════════════════════════════
# THEME
# ══════════════════════════════════════════════════════════════════════════
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
            border-left: 4px solid {border};
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
        /* Mobile-friendly tab nav: wrap & perkecil font supaya tidak
           terpotong/overflow di layar sempit (Android). */
        .stTabs [data-baseweb="tab-list"] {{
            flex-wrap: wrap;
            gap: 4px;
        }}
        .stTabs [data-baseweb="tab"] {{
            font-size: 0.85rem;
            padding: 6px 10px;
            white-space: nowrap;
        }}
    </style>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# RESULT CARD (badge tier + border kasta digabung) — semua field eksternal
# di-escape dulu sebelum masuk HTML, cegah broken layout / injection.
# ══════════════════════════════════════════════════════════════════════════
def _safe(val, default="") -> str:
    """Escape apa pun yang berasal dari data eksternal (judul video, channel,
    teks transcript/chat) sebelum di-render sebagai HTML mentah."""
    return html.escape(str(val if val not in (None, "") else default))


def _safe_url(url: str) -> str:
    """Hanya izinkan URL yang benar-benar mengarah ke YouTube. Selain itu,
    kembalikan '#' supaya tidak ada href yang bisa disuntik dari data luar."""
    url = str(url or "")
    if url.startswith("https://www.youtube.com/") or url.startswith("https://youtu.be/"):
        return html.escape(url, quote=True)
    return "#"


def render_match_card(container, ev: dict):
    kasta = ev.get("kasta", "")
    border = KASTA_BORDER.get(kasta, KASTA_DEFAULT_BORDER)

    level = ev.get("level")
    if level:
        border = LEVEL_COLORS.get(level, border)

    tc = ev.get("tier_counts", {}) or {}
    badges_html = ""
    for tier in TIER_ORDER:
        cnt = tc.get(tier, 0)
        if cnt:
            color = TIER_COLORS.get(tier, "#888")
            badges_html += f'<span class="cs20-badge" style="background:{color}">{_safe(tier)} ×{int(cnt)}</span>'

    score_line = f"skor: {_safe(ev.get('persentase', 0))}%"
    if level:
        score_line = f"{_safe(LEVEL_LABELS.get(level, f'LVL{level}'))} · skor chat: {_safe(ev.get('score', 0))}"

    lang_tag = ev.get("transcript_lang", "") or "?"
    hits_html = ""
    for h in ev.get("hits", []):
        top_tier = next((t for t in TIER_ORDER if h.get("tiers", {}).get(t, 0)), None)
        color = TIER_COLORS.get(top_tier, LEVEL_COLORS.get(level, "#555")) if not level else LEVEL_COLORS.get(level, "#555")
        hits_html += (
            f'<div class="cs20-hit" style="border-left-color:{color}">'
            f'⏱ <b>{_safe(h.get("time"))}</b> — {_safe(h.get("text"))} '
            f'<a href="{_safe_url(h.get("url"))}" target="_blank" rel="noopener noreferrer">↗ buka</a></div>'
        )

    lang_badge = f'<span class="cs20-lang-badge">{_safe(lang_tag).upper()}</span>&nbsp; ' if not level else ""

    html_out = f"""
    <div class="cs20-card" style="border-left-color:{border}">
      <div class="cs20-title">📹 {_safe(ev.get('title'), '(tanpa judul)')}</div>
      <div class="cs20-sub">
        {lang_badge}@{_safe(ev.get('channel'))} &nbsp;|&nbsp; {score_line}
        &nbsp; {badges_html}
      </div>
      {hits_html}
    </div>
    """
    container.markdown(html_out, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# GENERIC ENGINE RUNNER — subprocess + --json-events, live streaming
# ══════════════════════════════════════════════════════════════════════════
def run_engine_live(cmd: list, results_key: str, label_prefix: str = ""):
    """Jalankan engine manapun (pantau/index/age) sebagai subprocess dengan
    --json-events, stream event JSON ke UI secara live. Dipakai oleh semua
    halaman mode. Return list hasil 'match' yang ditemukan.
    """
    st.session_state[results_key] = []

    status_placeholder   = st.empty()
    progress_placeholder = st.empty()
    report_placeholder   = st.empty()
    results_container     = st.container()

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=SCRIPT_DIR,
        )
    except FileNotFoundError as e:
        st.error(f"❌ Gagal menjalankan engine: `{e}`. Cek apakah file engine ada di direktori script.")
        return st.session_state[results_key]
    except Exception as e:
        st.error(f"❌ Gagal menjalankan engine: {e}")
        return st.session_state[results_key]

    raw_log_lines = []

    try:
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
                pct = int(min(done / total, 1.0) * 100)
                phase = ev.get("phase", "")
                batch = ev.get("batch")
                batch_str = f" batch {batch}" if batch else ""
                progress_placeholder.progress(
                    pct, text=f"{label_prefix}@{ev.get('channel','')}{batch_str} "
                              f"[{phase}] — {done}/{total} ({ev.get('hits_total', ev.get('an_hits', 0))} hit)"
                )

            elif etype == "batch_start":
                status_placeholder.info(f"📦 Mulai batch {ev.get('batch')} / {ev.get('total_batches')} "
                                         f"untuk @{ev.get('channel','')}")

            elif etype == "batch_done":
                status_placeholder.success(f"✅ Batch {ev.get('batch')} selesai ({ev.get('status')}).")

            elif etype == "match":
                slot = results_container.empty()
                render_match_card(slot, ev)
                st.session_state[results_key].append(ev)

            elif etype == "report_sent":
                report_placeholder.success(f"📨 Laporan @{ev.get('channel','')} terkirim ke Discord.")

            elif etype == "rate_limit_stop":
                status_placeholder.warning(f"⏸️ Rate limit — @{ev.get('channel','')} dihentikan darurat.")

            elif etype == "cooldown":
                status_placeholder.warning(f"⏸️ Rate limit beruntun — cooldown {ev.get('seconds',0)}s...")

            elif etype == "all_done":
                status_placeholder.success(f"🏁 @{ev.get('channel','')} selesai.")
    except Exception as e:
        st.error(f"❌ Terjadi error saat membaca output engine: {e}")
        try:
            proc.kill()
        except Exception:
            pass

    proc.wait()
    progress_placeholder.empty()

    # Log mentah tetap ditampilkan meski returncode == 0, supaya warning
    # non-fatal (mis. dari yt-dlp) tidak hilang begitu saja dari pandangan user.
    if raw_log_lines:
        label = "⚠️ Log mentah (ada kemungkinan error, klik untuk lihat)" if proc.returncode != 0 \
            else "📄 Log mentah (klik untuk lihat detail proses)"
        with st.expander(label):
            st.code("\n".join(raw_log_lines[-60:]), language="text")

    return st.session_state[results_key]


# ══════════════════════════════════════════════════════════════════════════
# PAGE: SETUP / SETTINGS (config HANYA via upload config.json)
# ══════════════════════════════════════════════════════════════════════════
def page_setup(gate: bool = False):
    if gate:
        st.markdown("## 🔐 Setup Wajib")
        st.caption("Config webhook Discord belum ditemukan/valid. Upload `config.json` dulu di bawah.")
    else:
        st.markdown("## ⚙️ Settings")

    tab_upload, tab_cookies = st.tabs(["📤 Upload config.json", "🍪 Cookies"])

    with tab_upload:
        st.subheader("Upload file config.json")
        st.caption(
            "Config webhook **HANYA** bisa diisi lewat upload file — tidak ada form edit "
            "manual di web ini (mencegah webhook salah ketik/kepencet tanpa sadar). "
            "Upload `config.json` yang sudah pernah kamu isi (mis. dari sesi Termux lain), "
            "atau buat baru dengan format:"
        )
        st.code("""{
  "webhooks": {
    "id": "https://discord.com/api/webhooks/XXXX/YYYY",
    "en": "https://discord.com/api/webhooks/XXXX/YYYY"
  },
  "executor": "NamaKamu"
}""", language="json")

        uploaded = st.file_uploader("Pilih config.json", type=["json"], key="config_uploader")
        if uploaded is not None:
            try:
                parsed = json.loads(uploaded.getvalue().decode("utf-8"))
                webhooks = parsed.get("webhooks", {})
                valid_hooks = {k: v for k, v in webhooks.items()
                               if v and validate_webhook(v)}
                if not valid_hooks:
                    st.error(
                        "❌ File terbaca tapi tidak ada webhook valid di dalamnya. "
                        "Pastikan URL berformat `https://discord.com/api/webhooks/<id>/<token>`."
                    )
                else:
                    parsed["webhooks"] = valid_hooks
                    save_config(parsed)
                    get_cfg(force_recheck=True)  # <-- fix: invalidate cache supaya tidak "balik ke gate"
                    st.success(f"✅ Config tersimpan! ({len(valid_hooks)} webhook valid terdeteksi)")
                    time.sleep(1)
                    st.rerun()
            except json.JSONDecodeError:
                st.error("❌ File bukan JSON valid.")
            except Exception as e:
                st.error(f"❌ Gagal baca file: {e}")

        if os.path.exists(CONFIG_JSON):
            with open(CONFIG_JSON, "rb") as f:
                st.download_button("⬇️ Download config.json saat ini", f.read(),
                                    "config.json", mime="application/json")
        else:
            st.caption("Belum ada config.json tersimpan.")

    with tab_cookies:
        st.subheader("🍪 YouTube Cookies")
        st.info(
            "Dipakai buat mengurangi rate limit dan buat Age Bypass. "
            "Export via ekstensi 'Get cookies.txt LOCALLY' di Chrome (format Netscape)."
        )
        cookies_file = st.file_uploader("Upload cookies.txt", type=["txt"], key="cookies_uploader")
        if cookies_file:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(COOKIES_PATH, "wb") as f:
                f.write(cookies_file.getvalue())
            st.success("✅ Cookies tersimpan!")

        if os.path.exists(COOKIES_PATH):
            st.caption("✅ cookies.txt ditemukan.")
        else:
            st.caption("❌ cookies.txt belum ada — rentan rate limit / tidak bisa Age Bypass.")


# ══════════════════════════════════════════════════════════════════════════
# PAGE: SEARCH INSTANT (Pantau)
# ══════════════════════════════════════════════════════════════════════════
def page_search(cfg: dict):
    st.title("🔍 Search Instant")
    st.caption("Mode Pantau: video paralel per channel, channel diproses sequential FIFO.")

    with st.sidebar:
        st.markdown("### ⚙️ Opsi Scan")
        executor = st.text_input("Nama alias", value=st.session_state.get("executor", ""))
        st.session_state.executor = executor

        lang_choice = st.selectbox("Bahasa engine", options=list(LANG_OPTIONS.keys()),
                                    format_func=lambda k: LANG_OPTIONS[k])
        content_type = st.selectbox("Tipe konten", options=list(CONTENT_TYPE_OPTIONS.keys()),
                                     format_func=lambda k: CONTENT_TYPE_OPTIONS[k])
        video_limit = st.number_input(
            "Maks video per channel", min_value=1, max_value=5000, value=50, step=10,
            help="Channel dengan video >1000 sebaiknya pakai Index Mode, bukan Search Instant."
        )
        jobs = st.slider("Workers (thread)", 1, 8, 4,
                          help="Lebih banyak = lebih cepat tapi lebih boros RAM. HP kentang pakai 2.")

        st.divider()
        st.markdown("### 📺 Tambah Channel")
        disabled_add = st.session_state.running or st.session_state.session_done
        new_handle = st.text_input("Username channel (tanpa @)", key="new_channel_input",
                                    disabled=disabled_add)
        if st.button("➕ Tambah ke antrian", use_container_width=True, disabled=disabled_add):
            handle_clean = new_handle.strip().lstrip("@")
            if not handle_clean:
                st.warning("⚠️ Isi username channel dulu.")
            elif any(c["handle"].lower() == handle_clean.lower() for c in st.session_state.channels):
                st.warning(f"⚠️ @{handle_clean} sudah ada di antrian.")
            else:
                with st.spinner(f"Memeriksa @{handle_clean}..."):
                    res = validate_channel(handle_clean, content_type)
                st.session_state.channels.append({
                    "handle": handle_clean,
                    "name": res["name"],
                    "valid": res["valid"],
                    "error": res["error"],
                    "status": "queued",
                })
                save_queue(st.session_state.channels)
                st.rerun()

        if st.session_state.channels:
            st.markdown("#### Antrian channel")
            for i, ch in enumerate(st.session_state.channels):
                badge = "✅" if ch["valid"] else "❌"
                status = ch.get("status", "queued")
                status_icon = {"queued": "⏳", "done": "🏁", "running": "▶️"}.get(status, status)
                label = ch["name"] if ch["valid"] else (ch["error"] or "invalid")
                colc1, colc2 = st.columns([5, 1])
                with colc1:
                    st.write(f"{badge} **@{ch['handle']}** — {label}  `{status_icon} {status}`")
                with colc2:
                    if st.button("🗑", key=f"del_{i}", disabled=st.session_state.running):
                        st.session_state.channels.pop(i)
                        save_queue(st.session_state.channels)
                        st.rerun()

    st.session_state.theme = st.radio("Tema", ["dark", "light"], horizontal=True,
                                       index=0 if st.session_state.theme == "dark" else 1,
                                       key="theme_radio_search")
    inject_theme_css(st.session_state.theme)

    # ── Gate: kalau sesi sebelumnya sudah selesai, wajib "Mulai Sesi Baru" dulu ──
    if st.session_state.session_done:
        st.success("🏁 Sesi scan sebelumnya sudah selesai.")
        st.caption("Antrian channel & hasil scan sesi lalu masih ditampilkan di bawah untuk referensi.")
        if st.button("🔄 Mulai Sesi Baru", type="primary", use_container_width=True):
            clear_queue()
            st.session_state.search_results_tmp = []
            st.session_state.session_done = False
            st.rerun()
        st.divider()

    valid_channels = [c for c in st.session_state.channels if c["valid"]]
    executor = st.session_state.get("executor", "")
    start_disabled = (st.session_state.running or st.session_state.session_done
                       or not valid_channels or not executor.strip())

    start_col, info_col = st.columns([1, 4])
    with start_col:
        start_clicked = st.button("▶️ Mulai Scan", type="primary", use_container_width=True,
                                   disabled=start_disabled)
    with info_col:
        if st.session_state.session_done:
            st.caption("ℹ️ Tekan 'Mulai Sesi Baru' dulu di atas sebelum bisa scan lagi.")
        elif not executor.strip():
            st.caption("⚠️ Isi nama alias dulu di sidebar.")
        elif not valid_channels:
            st.caption("⚠️ Tambahkan minimal 1 channel valid ke antrian (sidebar).")

    if start_clicked and not start_disabled:
        st.session_state.running = True
        webhook_url = cfg.get("webhooks", {}).get(lang_choice, "")
        total = len(valid_channels)
        for idx, ch in enumerate(valid_channels, start=1):
            ch["status"] = "running"
            save_queue(st.session_state.channels)
            st.markdown(f"#### 🔴 Channel {idx}/{total}: @{ch['handle']}")
            cmd = [
                sys.executable, ENGINE_PANTAU,
                "--channel", ch["handle"],
                "--executor", executor.strip(),
                "--lang", lang_choice,
                "--content-type", content_type,
                "--limit", str(video_limit),
                "--jobs", str(jobs),
                "--mode", "pantau",
                "--config-dir", CONFIG_DIR,
                "--checkpoint-dir", CHECKPOINT_DIR,
                "--webhook-url", webhook_url,
                "--json-events",
            ]
            run_engine_live(cmd, "search_results_tmp", label_prefix="")
            ch["status"] = "done"
            save_queue(st.session_state.channels)
        st.session_state.running = False
        st.session_state.session_done = True
        st.success("🏁 Semua channel di antrian selesai diproses.")
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE: INDEX MODE (manual per-klik)
# ══════════════════════════════════════════════════════════════════════════
def page_index(cfg: dict):
    st.title("📦 Index Mode")
    st.caption("Untuk channel besar (1000+ video). Download+analisis per batch, manual per-klik.")

    st.info(
        "📦 Cara kerja: video dibagi per batch → download subtitle → analisis "
        "otomatis → laporan Discord. Batch diproses **manual per klik** (bukan "
        "otomatis sampai habis) supaya kamu tetap kontrol beban & bisa jeda kapan saja."
    )

    channel = st.text_input("📺 Channel target", placeholder="nama_channel").strip().lstrip("@")

    st.subheader("⚙️ Konfigurasi Batch")
    col1, col2 = st.columns(2)
    with col1:
        total_videos = st.number_input("Total video estimasi", min_value=10, value=1000)
        batch_size = st.number_input("Video per batch", min_value=10, max_value=500, value=100)
    with col2:
        jobs = st.slider("Workers", 1, 3, 2)
        content_type = st.selectbox("Tipe konten", options=list(CONTENT_TYPE_OPTIONS.keys()),
                                     format_func=lambda k: CONTENT_TYPE_OPTIONS[k], key="idx_ct")
    lang_choice = st.selectbox("Bahasa engine", options=list(LANG_OPTIONS.keys()),
                                format_func=lambda k: LANG_OPTIONS[k], key="idx_lang")

    if channel and st.button("✅ Validasi Channel"):
        with st.spinner("Validasi..."):
            info = validate_channel(channel, content_type)  # fix: pakai content_type yg sama dgn run
        if info["valid"]:
            st.success(f"✅ **{info['name']}** | Video: {info['video_count']}{'+' if info['video_capped'] else ''} "
                       f"| Live: {info['live_count']}{'+' if info['live_capped'] else ''}")
        else:
            st.error(f"❌ {info['error']}")

    total_batches = (total_videos + batch_size - 1) // batch_size
    st.caption(f"📦 {total_batches} batch total (~{batch_size} video/batch)")

    batches_per_run = st.number_input(
        "Batch diproses per klik run", min_value=1, max_value=int(total_batches), value=min(2, int(total_batches)),
        help="Setiap klik run cuma proses sekian batch — sisanya lanjut di klik run berikutnya (resume otomatis)."
    )
    start_batch = st.number_input("Mulai dari batch ke-", min_value=1, max_value=int(total_batches), value=1)

    executor = st.text_input("👤 Nama alias", value=cfg.get("executor", ""), key="idx_executor")

    run_disabled = not channel or st.session_state.get("index_running", False)
    if st.button("🚀 Proses Batch Ini", type="primary", use_container_width=True, disabled=run_disabled):
        webhook_url = cfg.get("webhooks", {}).get(lang_choice, "")
        cmd = [
            sys.executable, ENGINE_INDEX,
            "--channel", channel,
            "--content-type", content_type,
            "--executor", executor.strip() or "Unknown",
            "--lang", lang_choice,
            "--webhook-url", webhook_url,
            "--config-dir", CONFIG_DIR,
            "--jobs", str(jobs),
            "--total-videos", str(total_videos),
            "--total-batches", str(total_batches),
            "--batches-per-run", str(batches_per_run),
            "--start-batch", str(start_batch),
            "--json-events",
        ]
        st.session_state.index_running = True
        st.divider()
        st.subheader("⏳ Progress Index Mode")
        run_engine_live(cmd, "index_results", label_prefix="📦 ")
        st.session_state.index_running = False
        if not st.session_state.get("index_results"):
            st.info("Tidak ada temuan cegukan di batch yang diproses. Klik lagi buat lanjut batch berikutnya.")
        else:
            st.caption("Klik '🚀 Proses Batch Ini' lagi buat lanjut ke batch berikutnya.")


# ══════════════════════════════════════════════════════════════════════════
# PAGE: AGE BYPASS
# ══════════════════════════════════════════════════════════════════════════
def page_age(cfg: dict):
    st.title("🔞 Age Bypass")
    st.caption("Scan video age-restricted via cookies YouTube.")

    if os.path.exists(COOKIES_PATH):
        st.success("✅ cookies.txt ditemukan.")
    else:
        st.error("❌ cookies.txt belum di-upload. Buka Settings → tab Cookies dulu.")

    channel = st.text_input("📺 Channel target (wajib)", placeholder="nama_channel").strip().lstrip("@")

    col1, col2 = st.columns(2)
    with col1:
        content_type = st.selectbox("Tipe konten", options=list(CONTENT_TYPE_OPTIONS.keys()),
                                     format_func=lambda k: CONTENT_TYPE_OPTIONS[k], key="age_ct")
        limit = st.number_input("Jumlah video terakhir", min_value=1, value=50)
    with col2:
        jobs = st.select_slider("Kecepatan (worker)", options=[1, 2, 3, 4], value=2,
                                 help="1=paling aman utk cookies, 4=paling cepat tapi rawan rate limit")
        lang_choice = st.selectbox("Bahasa engine", options=list(LANG_OPTIONS.keys()),
                                    format_func=lambda k: LANG_OPTIONS[k], key="age_lang")

    executor = st.text_input("👤 Nama alias", value=cfg.get("executor", ""), key="age_executor")

    st.info(
        "ℹ️ Mode UI ini selalu mulai **sesi baru** — resume dari log lama hanya "
        "tersedia lewat CLI (`cs20_age_engine.py` tanpa `--json-events`)."
    )

    run_disabled = (not channel or not os.path.exists(COOKIES_PATH)
                     or st.session_state.get("age_running", False))
    if st.button("🔞 Mulai Age Bypass", type="primary", use_container_width=True, disabled=run_disabled):
        webhook_url = cfg.get("webhooks", {}).get(lang_choice, "")
        cmd = [
            sys.executable, ENGINE_AGE,
            "--channel", channel,
            "--content-type", content_type,
            "--limit", str(limit),
            "--executor", executor.strip() or "Unknown",
            "--lang", lang_choice,
            "--webhook-url", webhook_url,
            "--config-dir", CONFIG_DIR,
            "--jobs", str(jobs),
            "--json-events",
        ]
        st.session_state.age_running = True
        st.divider()
        st.subheader("⏳ Progress Age Bypass")
        run_engine_live(cmd, "age_results", label_prefix="🔞 ")
        st.session_state.age_running = False
        if not st.session_state.get("age_results"):
            st.info("Tidak ada temuan cegukan di video yang di-bypass.")


# ══════════════════════════════════════════════════════════════════════════
# PAGE: CHATSEEKER
# ══════════════════════════════════════════════════════════════════════════
FILTER_OPTIONS = {
    "ALL":              "🌐 Semua arsip live",
    "LIMIT":            "🔢 Batasi jumlah video terbaru",
    "CHECKPOINT":       "💾 Lanjut dari checkpoint (skip yang sudah diproses)",
    "LIMIT_CHECKPOINT": "🔢💾 Batasi jumlah + lanjut dari checkpoint",
}


def page_chat(cfg: dict):
    st.title("💬 ChatSeeker")
    st.caption("Mining live chat replay — cari keyword cegukan di chat arsip livestream.")

    channel = st.text_input("📺 Channel target", placeholder="nama_channel").strip().lstrip("@")

    col1, col2 = st.columns(2)
    with col1:
        filter_mode = st.selectbox("Mode filter", options=list(FILTER_OPTIONS.keys()),
                                    format_func=lambda k: FILTER_OPTIONS[k])
        max_vid_input = "ALL"
        if filter_mode in ("LIMIT", "LIMIT_CHECKPOINT"):
            max_vid_input = str(st.number_input("Jumlah video terbaru", min_value=1, value=50))
    with col2:
        webhook_lang = st.selectbox("Kirim laporan ke webhook", options=list(LANG_OPTIONS.keys()),
                                     format_func=lambda k: LANG_OPTIONS[k], key="chat_webhook_lang")

    executor = st.text_input("👤 Nama alias", value=cfg.get("executor", ""), key="chat_executor")

    st.info(
        "ℹ️ ChatSeeker cuma scan tab **Live/Streams** (bukan video biasa). "
        "Mode `CHECKPOINT`/`LIMIT_CHECKPOINT` dari UI ini **tidak akan pernah minta konfirmasi reset** — "
        "kalau semua video di checkpoint sudah pernah diproses, prosesnya cuma berhenti "
        "(jalankan CLI langsung tanpa `--json-events` kalau mau reset interaktif)."
    )

    run_disabled = not channel or st.session_state.get("chat_running", False)
    if st.button("💬 Mulai ChatSeeker", type="primary", use_container_width=True, disabled=run_disabled):
        webhook_url = cfg.get("webhooks", {}).get(webhook_lang, "")
        cmd = [
            sys.executable, ENGINE_CHAT,
            "--channel", channel,
            "--executor", executor.strip() or "Unknown",
            "--filter", filter_mode,
            "--max-vid", max_vid_input,
            "--webhook-url", webhook_url,
            "--checkpoint-dir", CHECKPOINT_DIR_CHAT,
            "--json-events",
        ]
        st.session_state["chat_running"] = True
        st.divider()
        st.subheader("⏳ Progress ChatSeeker")
        run_engine_live(cmd, "chat_results", label_prefix="💬 ")
        st.session_state["chat_running"] = False
        if not st.session_state.get("chat_results"):
            st.info("Tidak ada temuan cegukan di chat arsip yang diproses.")


# ══════════════════════════════════════════════════════════════════════════
# MAIN — navigasi pakai st.tabs() di area utama, BUKAN sidebar button.
# Alasan: di Android, sidebar Streamlit default collapsed dan ikon buka (>>)
# gampang ke-cover / meleset di layar sempit -> menu "hilang". st.tabs()
# selalu terlihat penuh baik di desktop maupun mobile.
# ══════════════════════════════════════════════════════════════════════════
def main():
    st.set_page_config(
        page_title="Cegukan Seeker V20 — Web UI",
        page_icon="👑",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    cfg = get_cfg()

    if not cfg:
        inject_theme_css(st.session_state.theme)
        st.markdown("## 👑 Cegukan Seeker V20")
        page_setup(gate=True)
        return

    st.markdown("## 👑 Cegukan Seeker V20")

    tab_search, tab_index, tab_age, tab_chat, tab_settings = st.tabs([
        "🔍 Search Instant", "📦 Index Mode", "🔞 Age Bypass", "💬 ChatSeeker", "⚙️ Settings"
    ])

    with tab_search:
        page_search(cfg)
    with tab_index:
        inject_theme_css(st.session_state.theme)
        page_index(cfg)
    with tab_age:
        inject_theme_css(st.session_state.theme)
        page_age(cfg)
    with tab_chat:
        inject_theme_css(st.session_state.theme)
        page_chat(cfg)
    with tab_settings:
        inject_theme_css(st.session_state.theme)
        page_setup(gate=False)


if __name__ == "__main__":
    main()
