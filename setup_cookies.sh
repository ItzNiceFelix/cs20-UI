#!/bin/bash
# ==============================================================================
# SETUP COOKIES — Cegukan Seeker V20
# Mengexport cookies YouTube dari Chrome Android untuk hindari rate limit
# ==============================================================================

CONFIG_DIR=".cs20"
COOKIES_FILE="$CONFIG_DIR/cookies.txt"
mkdir -p "$CONFIG_DIR"

echo "================================================================"
echo "  🍪 SETUP COOKIES — CEGUKAN SEEKER V20"
echo "================================================================"
echo ""
echo "  Cookies YouTube diperlukan supaya tidak kena rate limit."
echo "  Skrip akan coba beberapa metode otomatis."
echo ""

# ── METODE 1: Chrome Android ─────────────────────────────────────
echo "  [1/3] Mencoba export dari Chrome Android..."
if yt-dlp --cookies-from-browser chrome \
          --cookies "$COOKIES_FILE" \
          --skip-download --quiet \
          "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null; then

    if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
        echo "  [✅] Berhasil dari Chrome Android!"
        echo ""
        echo "  Cookies tersimpan di: $COOKIES_FILE"
        echo "  Jalankan cs20.sh — rate limit protection sudah aktif."
        exit 0
    fi
fi

# ── METODE 2: Firefox Android ────────────────────────────────────
echo "  [2/3] Mencoba export dari Firefox Android..."
if yt-dlp --cookies-from-browser firefox \
          --cookies "$COOKIES_FILE" \
          --skip-download --quiet \
          "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null; then

    if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
        echo "  [✅] Berhasil dari Firefox Android!"
        echo ""
        echo "  Cookies tersimpan di: $COOKIES_FILE"
        exit 0
    fi
fi

# ── METODE 3: Chromium Termux ────────────────────────────────────
echo "  [3/3] Mencoba export dari Chromium Termux..."
if yt-dlp --cookies-from-browser chromium \
          --cookies "$COOKIES_FILE" \
          --skip-download --quiet \
          "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 2>/dev/null; then

    if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
        echo "  [✅] Berhasil dari Chromium Termux!"
        exit 0
    fi
fi

# ── SEMUA OTOMATIS GAGAL — panduan manual ────────────────────────
echo ""
echo "================================================================"
echo "  [⚠️] Export otomatis gagal. Gunakan cara manual:"
echo "================================================================"
echo ""
echo "  CARA MANUAL (pakai ekstensi browser):"
echo ""
echo "  1. Install ekstensi 'Get cookies.txt LOCALLY' di Chrome:"
echo "     https://chrome.google.com/webstore/detail/get-cookiestxt-locally"
echo ""
echo "  2. Buka YouTube di Chrome, pastikan sudah login"
echo ""
echo "  3. Klik ikon ekstensi → pilih 'Export' → simpan sebagai cookies.txt"
echo ""
echo "  4. Pindahkan file cookies.txt ke folder .cs20/ :"
echo "     cp /sdcard/Download/cookies.txt $COOKIES_FILE"
echo ""
echo "  5. Jalankan lagi: bash setup_cookies.sh"
echo "     (untuk verifikasi file sudah benar)"
echo ""

# Cek jika user sudah taruh manual
if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
    echo "  [✅] File cookies.txt ditemukan di $COOKIES_FILE!"
    echo "  Jalankan cs20.sh — rate limit protection aktif."
else
    echo "  [ℹ️]  Tanpa cookies, skrip tetap bisa berjalan"
    echo "        tapi akan lebih lambat karena jeda antar request lebih panjang."
fi

echo "================================================================"
