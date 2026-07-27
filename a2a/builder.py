"""The one-click build pipeline: package name in, .AppImage out.

    search index -> resolve deps -> download root+deps -> extract into AppDir
    -> prune host libs -> write .desktop/icon/AppRun -> appimagetool -> .AppImage
"""

from __future__ import annotations

import os
import re
import tempfile

from . import appdir as appdir_mod
from . import appimage, config, deps, excludelist, extract
from .config import Config
from .repos import Index, Package
from .util import Progress, download, ensure_dir, null_progress, rmtree


class BuildError(RuntimeError):
    pass


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", name)


def _fetch_pkg(pkg: Package, progress: Progress) -> str:
    """Download a package to the cache (skipping if already present)."""
    dest = os.path.join(config.pkg_cache_dir(), pkg.filename)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        progress(f"cached {pkg.filename}", None)
        return dest
    download(pkg.url(), dest, progress=progress, label=pkg.filename)
    return dest


def build_appimage(
    pkg: Package,
    index: Index,
    cfg: Config,
    progress: Progress = null_progress,
    output_dir: str | None = None,
    keep_appdir: bool = False,
) -> str:
    """Build an AppImage for ``pkg``. Returns the output path."""
    output_dir = output_dir or cfg.output_dir
    ensure_dir(output_dir)

    # 1. Resolve dependencies (unless disabled).
    if cfg.bundle_deps:
        progress("Resolving dependencies", 0.05)
        dep_result = deps.resolve(pkg, index)
        dep_pkgs = dep_result.packages
        if dep_result.unresolved:
            progress(
                f"note: {len(dep_result.unresolved)} deps unresolved "
                f"(assumed on host): {', '.join(dep_result.unresolved[:6])}"
                + ("..." if len(dep_result.unresolved) > 6 else ""),
                None,
            )
    else:
        dep_pkgs = []

    all_pkgs = [pkg] + dep_pkgs
    progress(f"{len(all_pkgs)} package(s) to fetch", 0.1)

    # 2. Download everything.
    paths: list[str] = []
    for i, p in enumerate(all_pkgs):
        progress(f"Downloading {p.name} ({i+1}/{len(all_pkgs)})",
                 0.1 + 0.4 * (i / len(all_pkgs)))
        paths.append(_fetch_pkg(p, progress))

    # 3. Assemble AppDir in a temp workspace.
    work = tempfile.mkdtemp(prefix="a2a-")
    appdir = ensure_dir(os.path.join(work, f"{_safe_name(pkg.name)}.AppDir"))
    try:
        for i, path in enumerate(paths):
            progress(f"Extracting {os.path.basename(path)}",
                     0.5 + 0.25 * (i / len(paths)))
            extract.extract(path, appdir)
        _strip_pacman_metadata(appdir)

        # 4. Prune host-provided libraries.
        progress("Pruning host libraries", 0.78)
        excludes = excludelist.load(update=True)
        removed = appdir_mod.prune_excluded_libs(appdir, excludes)
        progress(f"pruned {len(removed)} host lib(s)", None)

        # 5. Desktop / icon / AppRun.
        progress("Writing AppDir metadata", 0.82)
        info = appdir_mod.finalize(appdir, pkg.name, display_name=pkg.name)
        progress(f"entrypoint: {info['binary']}", None)

        # 6. Package.
        out_name = f"{_safe_name(pkg.name)}-{_safe_name(pkg.version)}-{config.ARCH}.AppImage"
        out_path = os.path.join(output_dir, out_name)
        appimage.build(appdir, out_path, progress=progress)

        if keep_appdir:
            progress(f"AppDir kept at {appdir}", None)
        return out_path
    finally:
        if not keep_appdir:
            rmtree(work)


def _strip_pacman_metadata(appdir: str) -> None:
    """Remove the per-package bookkeeping files pacman ships in every archive."""
    for meta in (".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL", ".CHANGELOG"):
        p = os.path.join(appdir, meta)
        try:
            os.remove(p)
        except OSError:
            pass
