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
echo "📦 [1/6] Installing system packages..."
pkg install -y python rust binutils || {
    echo "❌ Gagal install system packages"
    exit 1
}

# ── 1b. Health-check pip (deteksi shebang python rusak) ──────────
echo "🔍 [1b/6] Cek kesehatan pip..."
if ! pip --version >/dev/null 2>&1; then
    echo "⚠️  pip rusak (biasanya shebang nunjuk ke Python versi lama)."
    echo "    Mencoba perbaikan otomatis via ensurepip..."
    python3 -m ensurepip --upgrade || {
        echo "❌ Gagal perbaiki pip otomatis. Coba manual:"
        echo "     pkg uninstall python -y && pkg install python -y"
        exit 1
    }
    python3 -m pip install --upgrade pip --force-reinstall --break-system-packages
fi
echo "  ✓ pip OK: $(pip --version)"

# ── 2. Streamlit (no heavy deps) ──────────────────────────────
echo "📦 [2/6] Installing Streamlit (no deps)..."
pip install streamlit --no-deps --break-system-packages || {
    echo "❌ Gagal install Streamlit"
    exit 1
}

# ── 3. Pure-Python deps — SATU-SATU (biar gagalnya spesifik) ──────
# PENTING: pip install grup itu ATOMIC — kalau 1 paket gagal, SEMUA
# paket di command yang sama batal terinstall, walau di-wrap "|| echo
# warning" (makanya sebelumnya blinker dkk raib tanpa kelihatan error
# jelas). Install satu-satu supaya kegagalan per-paket kelihatan.
echo "📦 [3/6] Installing pure-Python dependencies (satu per satu)..."
PKGS_CORE=(
    click blinker cachetools protobuf tornado altair
    gitpython pillow requests rich tenacity toml
    watchdog validators
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
    echo "    Coba install manual satu-satu untuk lihat error detailnya:"
    for p in "${FAILED_PKGS[@]}"; do
        echo "      pip install $p --break-system-packages"
    done
fi

# ── 4. numpy/pandas via pkg (prebuilt!) ────────────────────────
echo "📦 [4/6] Installing numpy/pandas (prebuilt)..."
pkg install -y python-numpy python-pandas || {
    echo "⚠️  Gagal install numpy/pandas via pkg"
}

# ── 5. Engine deps ─────────────────────────────────────────────
echo "📦 [5/6] Installing engine dependencies..."
pip install youtube-transcript-api yt-dlp \
    --break-system-packages || {
    echo "❌ Gagal install engine deps"
    exit 1
}

# ── 6. VERIFIKASI — cek beneran bisa di-import, bukan cuma "install selesai" ──
echo "🔍 [6/6] Verifikasi modul (import test)..."
VERIFY_MODULES=(
    streamlit click blinker cachetools google.protobuf tornado altair
    git PIL requests rich tenacity toml watchdog validators
    anyio itsdangerous multipart starlette uvicorn websockets httptools
    yt_dlp youtube_transcript_api
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
    echo "  ⚠️  INSTALL SELESAI DENGAN CATATAN"
    echo "  Modul berikut BELUM bisa di-import: ${MISSING[*]}"
    echo "  streamlit run kemungkinan masih error ModuleNotFoundError."
    echo "  Coba: pip install <nama_modul> --break-system-packages"
    echo "  (nama modul Python kadang beda dari nama paket pip, misal"
    echo "   'google.protobuf' itu dari paket 'protobuf', 'PIL' dari 'pillow')"
fi
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
