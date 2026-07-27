#!/bin/sh
# Build a self-contained AppImage of Arch2AppimageGUI.
#
# Bundles a full CPython + Tk taken from the host, plus the `zstandard` module,
# so the AppImage runs on any Linux *desktop* with no Python installed. The X11 /
# OpenGL / fontconfig / freetype stack is intentionally NOT bundled — those come
# from the host desktop (same policy the app itself uses when bundling packages).
#
# Runs on a clean Ubuntu CI runner (needs: python3 python3-tk python3-pip tcl tk
# wget file; and rsvg-convert or imagemagick for the icon). Also works on a dev
# box that has those.
set -eu

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APPDIR="$ROOT/build/AppDir"
OUT="${OUT:-Arch2AppimageGUI-x86_64.AppImage}"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$APPDIR/usr/src" "$APPDIR/usr/share/tcltk"

PYBIN="$(command -v python3)"
PYVER="$("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
echo ">> bundling CPython $PYVER from $PYBIN"

# --- 1. interpreter + stdlib (incl. lib-dynload: _tkinter, _ssl, _lzma, ...) --
STDLIB="$("$PYBIN" -c 'import sysconfig;print(sysconfig.get_paths()["stdlib"])')"
cp -L "$(readlink -f "$PYBIN")" "$APPDIR/usr/bin/python3"
cp -a "$STDLIB/." "$APPDIR/usr/lib/python$PYVER/"
mkdir -p "$APPDIR/usr/lib/python$PYVER/site-packages"

# trim weight we never use
( cd "$APPDIR/usr/lib/python$PYVER" && rm -rf test tests idlelib turtledemo \
    lib2to3/tests config-* ensurepip/_bundled 2>/dev/null || true )
find "$APPDIR/usr/lib/python$PYVER" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# --- 2. shared libraries the interpreter + C extensions link against ----------
# Bundle everything EXCEPT the glibc/loader and the host desktop graphics stack.
gather() { ldd "$1" 2>/dev/null | awk '/=>/{print $3} !/=>/{print $1}' | grep '^/' || true; }

copy_lib() {
    lib="$1"
    [ -e "$lib" ] || return 0
    base="$(basename "$lib")"
    stem="${base%%.so*}"
    case "$stem" in
        ld-linux*|libc|libm|libdl|libpthread|librt|libresolv|libutil|libnsl|\
        libcrypt|libanl|libBrokenLocale|libgcc_s|libstdc++|\
        libX11|libX11-xcb|libXext|libXft|libXrender|libXss|libXi|libXfixes|\
        libXcursor|libXinerama|libXrandr|libXcomposite|libXdamage|libxcb*|\
        libXau|libXdmcp|libGL*|libEGL*|libGLdispatch|libOpenGL|libGLX*|\
        libdrm|libgbm|libglapi|libfontconfig|libfreetype|libexpat|libpng*|\
        libharfbuzz*|libgraphite2|libglib-2.0|libgobject-2.0|libgio-2.0|\
        libgmodule-2.0|libselinux|libpcre*|libbsd|libmd|libthai|libdatrie|\
        libwayland*|libxkbcommon*|libcairo*|libpango*|libgdk*|libgtk*|\
        libatk*|libpixman*)
            return 0 ;;
    esac
    [ -e "$APPDIR/usr/lib/$base" ] && return 0
    cp -L "$lib" "$APPDIR/usr/lib/$base"
}

# Seed from the interpreter and every compiled extension, then close over deps.
for dep in $(gather "$APPDIR/usr/bin/python3"); do copy_lib "$dep"; done
DYNLOAD="$APPDIR/usr/lib/python$PYVER/lib-dynload"
if [ -d "$DYNLOAD" ]; then
    for so in "$DYNLOAD"/*.so; do
        [ -e "$so" ] || continue
        for dep in $(gather "$so"); do copy_lib "$dep"; done
    done
fi
# transitive closure (a few passes over what we've copied)
for _pass in 1 2 3 4; do
    for lib in "$APPDIR/usr/lib"/*.so*; do
        [ -e "$lib" ] || continue
        for dep in $(gather "$lib"); do copy_lib "$dep"; done
    done
done

# --- 3. Tcl/Tk script libraries (needed at runtime by Tk) ---------------------
for d in /usr/share/tcltk/tcl8.* /usr/share/tcltk/tk8.* \
         /usr/lib/tcl8.* /usr/lib/tk8.* /usr/share/tcl8.* /usr/share/tk8.*; do
    [ -d "$d" ] && cp -a "$d" "$APPDIR/usr/share/tcltk/" 2>/dev/null || true
done

# --- 4. zstandard, so .pkg.tar.zst extraction works on Python < 3.14 ----------
"$PYBIN" -m pip install --no-compile --no-input \
    --target "$APPDIR/usr/lib/python$PYVER/site-packages" "zstandard>=0.21" \
    || echo "!! warning: could not bundle zstandard (extraction will fall back to bsdtar/zstd)"

# --- 5. the application ------------------------------------------------------
cp -a "$ROOT/a2a" "$ROOT/gui" "$ROOT/Arch2AppimageGUI.py" "$APPDIR/usr/src/"
find "$APPDIR/usr/src" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# --- 6. icon + desktop entry -------------------------------------------------
ICON_PNG="$APPDIR/arch2appimage.png"
if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 256 -h 256 "$ROOT/assets/arch2appimage.svg" -o "$ICON_PNG"
elif command -v magick >/dev/null 2>&1; then
    magick -background none "$ROOT/assets/arch2appimage.svg" -resize 256x256 "$ICON_PNG"
elif command -v convert >/dev/null 2>&1; then
    convert -background none "$ROOT/assets/arch2appimage.svg" -resize 256x256 "$ICON_PNG"
else
    cp "$ROOT/assets/arch2appimage.svg" "$APPDIR/arch2appimage.svg"
    ICON_PNG="$APPDIR/arch2appimage.svg"
fi
cp -f "$ICON_PNG" "$APPDIR/.DirIcon"

cat > "$APPDIR/Arch2AppimageGUI.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Arch2AppimageGUI
Comment=Search prebuilt Arch repos and build AppImages
Exec=Arch2AppimageGUI
Icon=arch2appimage
Categories=Utility;System;PackageManager;
Terminal=false
EOF

# --- 7. AppRun ---------------------------------------------------------------
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONHOME="$HERE/usr"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$HERE/usr/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/lib:${LD_LIBRARY_PATH:-}"
# site-packages (zstandard) — Debian's site.py may not add it, so be explicit
for sp in "$HERE"/usr/lib/python3.*/site-packages; do
    [ -d "$sp" ] && export PYTHONPATH="$sp${PYTHONPATH:+:$PYTHONPATH}"
done
for d in "$HERE"/usr/share/tcltk/tcl8.*; do [ -d "$d" ] && export TCL_LIBRARY="$d"; done
for d in "$HERE"/usr/share/tcltk/tk8.*;  do [ -d "$d" ] && export TK_LIBRARY="$d";  done
exec "$HERE/usr/bin/python3" "$HERE/usr/src/Arch2AppimageGUI.py" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# --- 8. package --------------------------------------------------------------
APPIMAGETOOL="$(command -v appimagetool || true)"
if [ -z "$APPIMAGETOOL" ]; then
    echo ">> downloading appimagetool"
    wget -qO /tmp/appimagetool \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x /tmp/appimagetool
    APPIMAGETOOL=/tmp/appimagetool
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" "$APPDIR" "$OUT"
echo ">> built $OUT"
ls -lh "$OUT"
