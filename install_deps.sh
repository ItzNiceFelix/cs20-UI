#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# INSTALL DEPS — Cegukan Seeker V20 Streamlit
#
# Pendekatan: BUKAN nebak nama modul satu-satu (rawan ketinggalan —
# itu penyebab error blinker/typing_extensions dkk sebelumnya). Sebaliknya,
# biarkan pip resolve dependency Streamlit SECARA PENUH & OTOMATIS (dia yang
# paling tau versi persis yang dibutuhkan), dan kita cuma "akalin" 2 paket
# yang MEMANG berat/compile (pyarrow, pydeck) dengan stub kosong — karena
# app CS20 UI ini tidak pernah pakai st.dataframe / st.pydeck_chart sama
# sekali, jadi keduanya aman di-stub tanpa fungsi apapun.
# ==============================================================================

set -e

echo "================================================================"
echo "  🚀 CS20 STREAMLIT — INSTALLER"
echo "================================================================"
echo ""

# ── 1. System deps ──────────────────────────────────────────────
echo "📦 [1/6] Installing system packages..."
pkg install -y python rust binutils || {
    echo "❌ Gagal install system packages"
    exit 1
}

# ── 1b. Health-check pip ──────────────────────────────────────────
echo "🔍 [1b/6] Cek kesehatan pip..."
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

# ── 2. numpy & pandas via pkg — PREBUILT, harus duluan ────────────
# Streamlit butuh keduanya di runtime (bukan cuma dataframe display —
# beberapa modul internal Streamlit import numpy/pandas di top-level).
# Pasang via pkg (Termux repo, sudah dikompilasi) SEBELUM pip install
# streamlit, supaya nanti pip resolver lihat keduanya "sudah terpenuhi"
# dan tidak coba compile dari source sendiri.
echo "📦 [2/6] Installing numpy/pandas (prebuilt via pkg)..."
pkg install -y python-numpy python-pandas || {
    echo "⚠️  Gagal install numpy/pandas via pkg — lanjut, tapi kemungkinan"
    echo "    pip nanti akan coba compile dari source (lama)."
}

# ── 3. Stub pyarrow & pydeck — TIDAK dipakai app ini sama sekali ──
# Keduanya wajib menurut metadata Streamlit tapi butuh compile berat
# (pyarrow: C++/Arrow; pydeck: narik numpy versi tertentu + build JS).
# App CS20 UI tidak pernah panggil st.dataframe/st.table/st.pydeck_chart,
# jadi stub kosong (modul valid, tidak melakukan apa-apa) cukup untuk
# memenuhi pip resolver tanpa compile sama sekali.
echo "📦 [3/6] Membuat stub pyarrow & pydeck (tidak dipakai app ini)..."
STUB_DIR="$(mktemp -d)"

make_stub () {
    local name="$1" version="$2" dir="$STUB_DIR/$name"
    mkdir -p "$dir/$name"
    cat > "$dir/pyproject.toml" <<EOF
[project]
name = "$name"
version = "$version"
description = "Stub — CS20 UI tidak menggunakan modul ini"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
EOF
    cat > "$dir/$name/__init__.py" <<EOF
# Stub kosong — CS20 UI tidak menggunakan modul ini (tidak ada
# st.dataframe / st.pydeck_chart dsb). Dibuat hanya supaya pip
# dependency resolver Streamlit terpenuhi tanpa compile berat.
__version__ = "$version"
EOF
    pip install "$dir" --break-system-packages -q 2>&1 | tail -3
}

python3 -c "import pyarrow" >/dev/null 2>&1 || make_stub pyarrow 14.0.2
python3 -c "import pydeck"  >/dev/null 2>&1 || make_stub pydeck  0.9.1
rm -rf "$STUB_DIR"

# ── 4. Streamlit — biarkan pip resolve SEMUA dependency-nya sendiri ──
# Ini kuncinya: bukan kita tebak paket satu-satu (blinker, click,
# typing_extensions, packaging, dst — daftar ini gampang kurang),
# tapi pip yang tau persis versi & daftar lengkap yang benar.
echo "📦 [4/6] Installing Streamlit (resolve dependency otomatis)..."
pip install streamlit --break-system-packages || {
    echo "❌ Gagal install Streamlit. Kemungkinan masih ada paket berat"
    echo "   yang coba di-compile. Cek pesan error di atas — nama paket"
    echo "   yang gagal biasanya kelihatan jelas di baris 'Building wheel for ...'"
    exit 1
}

# ── 5. Engine deps ─────────────────────────────────────────────────
echo "📦 [5/6] Installing engine dependencies (yt-dlp, transcript API, rich)..."
pip install youtube-transcript-api yt-dlp rich requests \
    --break-system-packages || {
    echo "❌ Gagal install engine deps"
    exit 1
}

# ── 6. VERIFIKASI — import beneran, bukan cuma "pip bilang sukses" ──
echo "🔍 [6/6] Verifikasi modul (import test)..."
VERIFY_MODULES=(
    streamlit rich requests yt_dlp youtube_transcript_api
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
    echo "  ✅ INSTALL SELESAI — semua modul inti terverifikasi!"
else
    echo "  ⚠️  Modul berikut BELUM bisa di-import: ${MISSING[*]}"
    echo "  Coba: pip install <nama_modul> --break-system-packages"
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
