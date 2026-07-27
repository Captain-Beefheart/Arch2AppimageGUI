"""Small shared helpers: HTTP download with progress, logging callback type."""

from __future__ import annotations

import os
import shutil
import time
import urllib.error
import urllib.request
from typing import Callable

USER_AGENT = "Arch2AppimageGUI/0.1 (+https://github.com/Captain-Beefheart/Arch2AppimageGUI)"

# A progress reporter: (message, fraction) where fraction is 0..1 or None for
# indeterminate. GUIs and the CLI both plug into this.
Progress = Callable[[str, "float | None"], None]


def null_progress(message: str, fraction: float | None = None) -> None:
    pass


class DownloadError(RuntimeError):
    pass


def http_get(url: str, timeout: int = 30) -> bytes:
    """Fetch a URL fully into memory."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise DownloadError(f"GET {url} failed: {exc}") from exc


def download(
    url: str,
    dest: str,
    progress: Progress = null_progress,
    label: str | None = None,
    timeout: int = 60,
    chunk: int = 65536,
) -> str:
    """Stream ``url`` to ``dest`` atomically, reporting progress.

    Returns the destination path. Raises DownloadError on failure.
    """
    label = label or os.path.basename(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            last = 0.0
            with open(tmp, "wb") as fh:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    got += len(buf)
                    now = time.monotonic()
                    if now - last > 0.1:
                        frac = (got / total) if total else None
                        progress(f"{label}  {_human(got)}"
                                 + (f" / {_human(total)}" if total else ""), frac)
                        last = now
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        _quiet_remove(tmp)
        raise DownloadError(f"download {url} failed: {exc}") from exc
    os.replace(tmp, dest)
    progress(f"{label}  done", 1.0)
    return dest


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _human(n: float) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
