#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# INSTALL DEPS — Cegukan Seeker V20 Streamlit
# Ringan, tanpa compile berat
# ==============================================================================

set -e

echo "================================================================"
echo "  🚀 CS20 STREAMLIT — LIGHTWEIGHT INSTALLER"
echo "================================================================"
echo ""

# ── 1. System deps ──────────────────────────────────────────────
echo "📦 [1/5] Installing system packages..."
pkg install -y python rust binutils || {
    echo "❌ Gagal install system packages"
    exit 1
}

# ── 2. Streamlit (no heavy deps) ──────────────────────────────
echo "📦 [2/5] Installing Streamlit (no deps)..."
pip install streamlit --no-deps --break-system-packages || {
    echo "❌ Gagal install Streamlit"
    exit 1
}

# ── 3. Pure-Python deps ───────────────────────────────────────
echo "📦 [3/5] Installing pure-Python dependencies..."
pip install click blinker cachetools protobuf tornado \
    altair gitpython pillow requests rich tenacity \
    toml watchdog validators \
    --break-system-packages || {
    echo "⚠️  Beberapa package mungkin gagal, mencoba lanjut..."
}

pip install anyio itsdangerous python-multipart \
    starlette uvicorn websockets httptools \
    --break-system-packages || {
    echo "⚠️  Beberapa package mungkin gagal, mencoba lanjut..."
}

# ── 4. numpy/pandas via pkg (prebuilt!) ────────────────────────
echo "📦 [4/5] Installing numpy/pandas (prebuilt)..."
pkg install -y python-numpy python-pandas || {
    echo "⚠️  Gagal install numpy/pandas via pkg"
}

# ── 5. Engine deps ─────────────────────────────────────────────
echo "📦 [5/5] Installing engine dependencies..."
pip install youtube-transcript-api yt-dlp \
    --break-system-packages || {
    echo "❌ Gagal install engine deps"
    exit 1
}

# ── Verify ──────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  ✅ INSTALL SELESAI!"
echo "================================================================"
echo ""
echo "  Jalankan dengan:"
echo "    streamlit run cs20_ui.py"
echo ""
echo "  Atau setup config dulu:"
echo "    cp config_sample.json config.json"
echo "    nano config.json  # Edit webhook URLs"
echo ""
echo "  Setup cookies (opsional tapi direkomendasikan):"
echo "    bash setup_cookies.sh"
echo ""
echo "================================================================"
