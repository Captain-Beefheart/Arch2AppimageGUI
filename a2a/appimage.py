"""Fetch appimagetool and package an AppDir into a .AppImage.

appimagetool is itself an AppImage; we run it with ``--appimage-extract-and-run``
so no FUSE is required on the build host.
"""

from __future__ import annotations

import os
import stat
import subprocess

from . import config
from .util import Progress, download, null_progress


class AppImageError(RuntimeError):
    pass


def ensure_appimagetool(progress: Progress = null_progress) -> str:
    dest = os.path.join(config.tool_cache_dir(), "appimagetool-x86_64.AppImage")
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        progress("Fetching appimagetool", None)
        download(config.APPIMAGETOOL_URL, dest, progress=progress,
                 label="appimagetool")
    st = os.stat(dest)
    os.chmod(dest, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def build(appdir: str, output_path: str, progress: Progress = null_progress) -> str:
    """Run appimagetool on ``appdir`` producing ``output_path``."""
    tool = ensure_appimagetool(progress)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    env = dict(os.environ)
    env.setdefault("ARCH", config.ARCH)
    # No desktop integration prompt, no update information required.
    cmd = [
        tool, "--appimage-extract-and-run",
        "--no-appstream",
        appdir, output_path,
    ]
    progress(f"Packaging {os.path.basename(output_path)}", None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except OSError as exc:
        raise AppImageError(f"could not run appimagetool: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise AppImageError(
            "appimagetool failed:\n" + "\n".join(tail)
        )
    if not os.path.exists(output_path):
        raise AppImageError("appimagetool reported success but produced no file")
    st = os.stat(output_path)
    os.chmod(output_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    progress(f"Built {os.path.basename(output_path)}", 1.0)
    return output_path
