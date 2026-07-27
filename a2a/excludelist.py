"""Libraries that must NOT be bundled into an AppImage.

These come from the host at runtime — bundling them causes crashes because they
are tightly coupled to the kernel, the GPU driver, or the host's glibc/loader.
The list mirrors the AppImage project's ``excludelist``; a fresh copy is fetched
and cached opportunistically, but this embedded default keeps the tool working
fully offline.
"""

from __future__ import annotations

import os

from . import config
from .util import http_get

# Curated from AppImage/pkg2appimage excludelist. Basenames, matched by prefix
# (e.g. "libc.so" also matches "libc.so.6").
_EMBEDDED = """
ld-linux.so
ld-linux-x86-64.so
libanl.so
libBrokenLocale.so
libcidn.so
libc.so
libdl.so
libm.so
libmvec.so
libnss_compat.so
libnss_dns.so
libnss_files.so
libpthread.so
libresolv.so
librt.so
libthread_db.so
libutil.so
libstdc++.so
libGL.so
libGLX.so
libGLdispatch.so
libEGL.so
libGLESv2.so
libOpenGL.so
libdrm.so
libglapi.so
libgbm.so
libxcb.so
libX11.so
libX11-xcb.so
libgio-2.0.so
libglib-2.0.so
libgobject-2.0.so
libgmodule-2.0.so
libgpg-error.so
libgcrypt.so
libz.so
libharfbuzz.so
libpango-1.0.so
libpangocairo-1.0.so
libpangoft2-1.0.so
libfontconfig.so
libfreetype.so
libfribidi.so
libselinux.so
libpcre.so
libpcre2-8.so
libp11-kit.so
libgtk-3.so
libgtk-x11-2.0.so
libgdk-3.so
libgdk-x11-2.0.so
libcairo.so
libgdk_pixbuf-2.0.so
libwayland-client.so
libwayland-cursor.so
libwayland-egl.so
libwayland-server.so
libasound.so
libdbus-1.so
libudev.so
libsystemd.so
libexpat.so
libuuid.so
libblkid.so
libmount.so
libcom_err.so
libgssapi_krb5.so
libkrb5.so
libk5crypto.so
libkrb5support.so
libutil.so
libnsl.so
libcrypt.so
libncursesw.so
libtinfo.so
libfuse.so
"""


def _parse(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def _cache_path() -> str:
    return os.path.join(config.cache_dir(), "excludelist.txt")


def load(update: bool = False) -> set[str]:
    """Return the set of excluded library basenames.

    If ``update`` is set, try to refresh the cached copy from upstream first;
    any failure silently falls back to cache, then to the embedded list.
    """
    if update:
        try:
            data = http_get(config.EXCLUDELIST_URL)
            os.makedirs(config.cache_dir(), exist_ok=True)
            with open(_cache_path(), "wb") as fh:
                fh.write(data)
        except Exception:  # noqa: BLE001 — offline is fine
            pass
    cache = _cache_path()
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as fh:
                names = _parse(fh.read())
            if names:
                return names
        except OSError:
            pass
    return _parse(_EMBEDDED)


def is_excluded(lib_basename: str, excludes: set[str]) -> bool:
    """True if ``lib.so.1.2`` matches an excluded prefix like ``lib.so``."""
    for ex in excludes:
        if lib_basename == ex or lib_basename.startswith(ex):
            return True
    return False
