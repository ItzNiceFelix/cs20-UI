#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# INSTALL DEPS — Cegukan Seeker V20 Streamlit
#
# Strategi: --no-deps untuk Streamlit (skip pip dependency resolver TOTAL,
# termasuk pyarrow/pydeck yang berat compile). Streamlit ternyata tidak
# hard-import pyarrow/pydeck saat start — keduanya cuma lazy-import kalau
# fitur st.dataframe/st.pydeck_chart dipakai, yang mana app CS20 UI ini
# TIDAK pernah pakai. Sudah divalidasi jalan di device asli tanpa keduanya.
#
# Dependency runtime yang beneran dipakai app ini sudah dicek langsung dari
# semua import di cs20_ui.py + semua engine + chatseeker.py (bukan tebakan).
# ==============================================================================

set -e

echo "================================================================"
echo "  🚀 CS20 STREAMLIT — INSTALLER"
echo "================================================================"
echo ""

# ── 1. System deps ──────────────────────────────────────────────
echo "📦 [1/5] Installing system packages..."
pkg install -y python rust binutils || {
    echo "❌ Gagal install system packages"
    exit 1
}

# ── 1b. Health-check pip ──────────────────────────────────────────
echo "🔍 [1b/5] Cek kesehatan pip..."
if ! pip --version >/dev/null 2>&1; then
    echo "⚠️  pip rusak (biasanya shebang nunjuk ke Python versi lama)."
    python3 -m ensurepip --upgrade || {
        echo "❌ Gagal perbaiki pip otomatis. Coba manual:"
        echo "     pkg uninstall python -y && pkg install python -y"
        exit 1
    }
    python3 -m pip install --upgrade pip --force-reinstall --break-system-packages
fi
echo "  ✓ pip OK: $(pip --version)"

# ── 2. Streamlit — no-deps, skip resolver total ────────────────────
echo "📦 [2/5] Installing Streamlit (--no-deps, skip pyarrow/pydeck)..."
pip install streamlit --no-deps --break-system-packages || {
    echo "❌ Gagal install Streamlit"
    exit 1
}

# ── 3. Dependency runtime Streamlit — SATU-SATU (bukan grup) ──────
# PENTING: pip install grup itu ATOMIC — kalau 1 paket gagal, SEMUA
# paket di command yang sama batal terinstall walau di-wrap "|| echo
# warning" (ini penyebab blinker sempat raib tanpa error jelas
# sebelumnya). Install satu-satu supaya kegagalan per-paket kelihatan
# jelas paket mana yang bermasalah.
echo "📦 [3/5] Installing dependency Streamlit (satu per satu)..."
PKGS_CORE=(
    click blinker cachetools protobuf tornado
    gitpython pillow tenacity toml watchdog validators
    typing-extensions packaging
    anyio itsdangerous python-multipart starlette
    uvicorn websockets httptools
)
FAILED_PKGS=()
for pkg in "${PKGS_CORE[@]}"; do
    echo "   → $pkg"
    if ! pip install "$pkg" --break-system-packages -q; then
        echo "   ⚠️  GAGAL: $pkg"
        FAILED_PKGS+=("$pkg")
    fi
done

if [ ${#FAILED_PKGS[@]} -gt 0 ]; then
    echo ""
    echo "⚠️  Paket berikut gagal ke-install: ${FAILED_PKGS[*]}"
    echo "    Install manual satu-satu untuk lihat error detailnya:"
    for p in "${FAILED_PKGS[@]}"; do
        echo "      pip install $p --break-system-packages"
    done
fi

# ── 4. Engine deps ─────────────────────────────────────────────────
echo "📦 [4/5] Installing engine dependencies (yt-dlp, transcript API, rich, requests)..."
pip install youtube-transcript-api yt-dlp rich requests \
    --break-system-packages || {
    echo "❌ Gagal install engine deps"
    exit 1
}

# ── 5. VERIFIKASI — import beneran, bukan cuma "pip bilang sukses" ──
# List ini sudah dicek langsung dari import statement di semua file
# .py project ini (cs20_ui.py, cs20_engine.py, cs20_index_engine.py,
# cs20_index_parser.py, cs20_age_engine.py, chatseeker.py) — bukan
# tebakan dari memori.
echo "🔍 [5/5] Verifikasi modul (import test)..."
VERIFY_MODULES=(
    streamlit click blinker cachetools google.protobuf tornado
    git PIL tenacity toml watchdog validators
    anyio itsdangerous multipart starlette uvicorn websockets httptools
    rich requests yt_dlp youtube_transcript_api
)
MISSING=()
for mod in "${VERIFY_MODULES[@]}"; do
    if ! python3 -c "import $mod" >/dev/null 2>&1; then
        MISSING+=("$mod")
    fi
done

echo ""
echo "================================================================"
if [ ${#MISSING[@]} -eq 0 ]; then
    echo "  ✅ INSTALL SELESAI — semua modul terverifikasi bisa di-import!"
else
    echo "  ⚠️  Modul berikut BELUM bisa di-import: ${MISSING[*]}"
    echo "  streamlit run kemungkinan masih ModuleNotFoundError."
    echo "  Coba: pip install <nama_modul> --break-system-packages"
    echo "  (nama modul kadang beda dari nama paket pip — misal"
    echo "   'google.protobuf' dari paket 'protobuf', 'PIL' dari 'pillow',"
    echo "   'git' dari paket 'gitpython', 'multipart' dari 'python-multipart')"
fi
echo "================================================================"
echo ""
echo "  Jalankan dengan:"
echo "    streamlit run cs20_ui.py --server.address 0.0.0.0"
echo ""
echo "  Atau setup config dulu:"
echo "    cp config_sample.json config.json"
echo "    nano config.json  # Edit webhook URLs"
echo ""
echo "  Setup cookies (opsional tapi direkomendasikan):"
echo "    bash setup_cookies.sh"
echo ""
echo "================================================================"
