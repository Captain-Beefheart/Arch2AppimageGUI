"""Turn a tree of extracted packages into a valid AppDir.

The extracted packages are merged under one root (which becomes the AppDir), so
it already contains ``usr/bin``, ``usr/lib``, ``usr/share`` etc. This module then:
  * prunes host-provided libraries (the excludelist),
  * locates the app's main binary, icon, and .desktop entry,
  * writes the top-level ``.desktop``, icon, ``.DirIcon`` and ``AppRun``.
"""

from __future__ import annotations

import os
import shutil
import stat

from . import excludelist


LIB_DIRS = ("usr/lib", "usr/lib32", "lib", "lib64", "usr/lib64")
BIN_DIRS = ("usr/bin", "bin", "usr/sbin", "sbin")


class AppDirError(RuntimeError):
    pass


# ---------------------------------------------------------------- lib pruning
def prune_excluded_libs(appdir: str, excludes: set[str]) -> list[str]:
    """Remove host-provided shared libraries. Returns basenames removed."""
    removed: list[str] = []
    for rel in LIB_DIRS:
        libdir = os.path.join(appdir, rel)
        if not os.path.isdir(libdir):
            continue
        for dirpath, _dirs, files in os.walk(libdir):
            for fn in files:
                if ".so" not in fn:
                    continue
                if excludelist.is_excluded(fn, excludes):
                    try:
                        os.remove(os.path.join(dirpath, fn))
                        removed.append(fn)
                    except OSError:
                        pass
    return removed


# ------------------------------------------------------------- desktop lookup
def _parse_desktop(path: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    in_entry = False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if line.strip() == "[Desktop Entry]":
                    in_entry = True
                    continue
                if line.startswith("[") and line.strip() != "[Desktop Entry]":
                    in_entry = False
                if in_entry and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    entries.setdefault(k.strip(), v.strip())
    except OSError:
        pass
    return entries


def _exec_binary(exec_line: str) -> str:
    """First real token of an Exec= line, minus %-field codes and env prefixes."""
    tokens = exec_line.split()
    for tok in tokens:
        if tok.startswith("%") or "=" in tok or tok in ("env",):
            continue
        return os.path.basename(tok)
    return ""


def find_desktop(appdir: str, pkg_name: str) -> str | None:
    appsdir = os.path.join(appdir, "usr/share/applications")
    if not os.path.isdir(appsdir):
        return None
    candidates = [
        os.path.join(appsdir, f)
        for f in os.listdir(appsdir)
        if f.endswith(".desktop")
    ]
    if not candidates:
        return None
    # Prefer a .desktop whose file name or Exec matches the package.
    def rank(path: str) -> int:
        base = os.path.basename(path).removesuffix(".desktop").lower()
        if base == pkg_name.lower():
            return 0
        d = _parse_desktop(path)
        if _exec_binary(d.get("Exec", "")).lower() == pkg_name.lower():
            return 1
        if d.get("NoDisplay", "false").lower() == "true":
            return 5
        return 3
    return sorted(candidates, key=rank)[0]


def find_binary(appdir: str, pkg_name: str, prefer: str = "") -> str | None:
    """Return the AppDir-relative path of the main executable."""
    names = [n for n in (prefer, pkg_name) if n]
    for rel in BIN_DIRS:
        bindir = os.path.join(appdir, rel)
        if not os.path.isdir(bindir):
            continue
        for want in names:
            cand = os.path.join(bindir, want)
            if os.path.isfile(cand):
                return os.path.relpath(cand, appdir).replace(os.sep, "/")
    # Fall back to the first executable file we can find in a bin dir.
    for rel in BIN_DIRS:
        bindir = os.path.join(appdir, rel)
        if not os.path.isdir(bindir):
            continue
        for fn in sorted(os.listdir(bindir)):
            p = os.path.join(bindir, fn)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return os.path.relpath(p, appdir).replace(os.sep, "/")
    return None


# ------------------------------------------------------------------ icon copy
def find_icon(appdir: str, icon_name: str) -> str | None:
    """Locate the best (largest) icon file matching ``icon_name``."""
    if not icon_name:
        return None
    icon_base = os.path.basename(icon_name)
    best: tuple[int, str] | None = None
    search_roots = [
        os.path.join(appdir, "usr/share/icons"),
        os.path.join(appdir, "usr/share/pixmaps"),
    ]
    exts = (".png", ".svg", ".xpm")
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                stem, ext = os.path.splitext(fn)
                if ext.lower() not in exts:
                    continue
                if stem != icon_base and stem != icon_name:
                    continue
                size = _icon_size(dirpath, ext)
                if best is None or size > best[0]:
                    best = (size, os.path.join(dirpath, fn))
    return best[1] if best else None


def _icon_size(dirpath: str, ext: str) -> int:
    # SVG is scalable -> rank highest; otherwise read the NxN from the theme path.
    if ext.lower() == ".svg":
        return 100_000
    for part in dirpath.replace(os.sep, "/").split("/"):
        if "x" in part:
            a, _, b = part.partition("x")
            if a.isdigit() and b.isdigit():
                return int(a)
    return 0


# -------------------------------------------------------------- AppRun script
_APPRUN = """#!/bin/sh
# Generated by Arch2AppimageGUI.
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$HERE/bin:$PATH"
export LD_LIBRARY_PATH="$HERE/usr/lib:$HERE/usr/lib32:$HERE/lib:$HERE/lib64:$HERE/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas${GSETTINGS_SCHEMA_DIR:+:$GSETTINGS_SCHEMA_DIR}"
exec "$HERE/__BINARY__" "$@"
"""


def _write_exec(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _placeholder_png() -> bytes:
    # Minimal 1x1 opaque PNG so appimagetool always has a .DirIcon.
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+"
        "M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


def finalize(appdir: str, pkg_name: str, display_name: str = "") -> dict:
    """Write .desktop / icon / .DirIcon / AppRun. Returns a summary dict."""
    display_name = display_name or pkg_name
    desktop_src = find_desktop(appdir, pkg_name)
    entries = _parse_desktop(desktop_src) if desktop_src else {}

    prefer_bin = _exec_binary(entries.get("Exec", ""))
    binary = find_binary(appdir, pkg_name, prefer=prefer_bin)
    if not binary:
        raise AppDirError(
            f"could not find an executable for '{pkg_name}' in the package"
        )

    icon_name = entries.get("Icon", pkg_name)
    icon_src = find_icon(appdir, icon_name)

    # --- top-level icon + .DirIcon ---
    icon_dest_stem = pkg_name
    if icon_src:
        ext = os.path.splitext(icon_src)[1].lower()
        top_icon = os.path.join(appdir, icon_dest_stem + ext)
        shutil.copyfile(icon_src, top_icon)
        _link_or_copy(top_icon, os.path.join(appdir, ".DirIcon"))
    else:
        top_icon = os.path.join(appdir, icon_dest_stem + ".png")
        with open(top_icon, "wb") as fh:
            fh.write(_placeholder_png())
        _link_or_copy(top_icon, os.path.join(appdir, ".DirIcon"))

    # --- top-level .desktop ---
    desktop_dest = os.path.join(appdir, f"{pkg_name}.desktop")
    _write_desktop(desktop_dest, entries, display_name, pkg_name, icon_dest_stem)

    # --- AppRun ---
    _write_exec(os.path.join(appdir, "AppRun"), _APPRUN.replace("__BINARY__", binary))

    return {
        "binary": binary,
        "desktop": os.path.basename(desktop_src) if desktop_src else "(generated)",
        "icon": os.path.basename(icon_src) if icon_src else "(placeholder)",
    }


def _write_desktop(dest, entries, display_name, pkg_name, icon_stem) -> None:
    exec_bin = _exec_binary(entries.get("Exec", "")) or pkg_name
    categories = entries.get("Categories", "Utility;")
    if not categories.endswith(";"):
        categories += ";"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={entries.get('Name', display_name)}",
        f"Exec={exec_bin}",
        f"Icon={icon_stem}",
        f"Comment={entries.get('Comment', display_name)}",
        f"Categories={categories}",
        f"Terminal={entries.get('Terminal', 'false')}",
        "",
    ]
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def _link_or_copy(src: str, dest: str) -> None:
    try:
        if os.path.lexists(dest):
            os.remove(dest)
        os.symlink(os.path.basename(src), dest)
    except OSError:
        shutil.copyfile(src, dest)
