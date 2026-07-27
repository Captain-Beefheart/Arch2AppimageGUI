"""Extract ``.pkg.tar.zst`` (and .xz/.gz) packages onto disk.

Package files are zstd-compressed tarballs. Extraction path, in order:
  1. Python's tarfile — natively handles zst on 3.14+, xz/gz everywhere.
  2. The ``zstandard`` PyPI module, streaming into tarfile.
  3. A ``bsdtar`` / ``zstd`` command-line fallback.

Members are extracted with a filter that permits the symlinks packages rely on
(e.g. ``libfoo.so -> libfoo.so.1``) but blocks absolute paths and traversal.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile


class ExtractError(RuntimeError):
    pass


def zst_backend() -> str | None:
    """Name of the available .pkg.tar.zst extraction backend, or None.

    Mirrors the fallback order used by extract(). Used by the CLI selftest to
    confirm a packaged build can actually unpack packages.
    """
    if "zst" in getattr(tarfile.TarFile, "OPEN_METH", {}):
        return "tarfile-native"
    try:
        import zstandard  # noqa: F401
        return "zstandard"
    except ImportError:
        pass
    if shutil.which("bsdtar"):
        return "bsdtar"
    if shutil.which("zstd") and shutil.which("tar"):
        return "zstd-cli"
    return None


def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
    # 'tar' filter (py3.12+) keeps symlinks/hardlinks but sanitizes paths.
    try:
        tar.extractall(dest, filter="tar")
    except TypeError:
        # Older Python without the filter kwarg: manual traversal guard.
        dest_abs = os.path.abspath(dest)
        for m in tar.getmembers():
            target = os.path.abspath(os.path.join(dest, m.name))
            if not target.startswith(dest_abs + os.sep):
                raise ExtractError(f"unsafe path in archive: {m.name}")
        tar.extractall(dest)


def extract(pkg_path: str, dest: str) -> None:
    """Extract a package archive into ``dest`` (created if needed)."""
    os.makedirs(dest, exist_ok=True)

    # 1. Native tarfile (transparent compression via 'r:*').
    try:
        with tarfile.open(pkg_path, mode="r:*") as tar:
            _safe_extract(tar, dest)
        return
    except (tarfile.TarError, OSError) as native_exc:
        if not pkg_path.endswith(".zst"):
            raise ExtractError(f"cannot extract {pkg_path}: {native_exc}") from native_exc

    # 2. zstandard module -> tarfile stream.
    try:
        import zstandard  # type: ignore

        with open(pkg_path, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader:
                data = reader.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
            _safe_extract(tar, dest)
        return
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"zstandard extract of {pkg_path} failed: {exc}") from exc

    # 3. CLI fallback.
    _extract_cli(pkg_path, dest)


def _extract_cli(pkg_path: str, dest: str) -> None:
    if shutil.which("bsdtar"):
        cmd = ["bsdtar", "-xpf", pkg_path, "-C", dest]
    elif shutil.which("tar") and shutil.which("zstd"):
        cmd = ["tar", "--use-compress-program=zstd", "-xpf", pkg_path, "-C", dest]
    else:
        raise ExtractError(
            "no zst extractor available: need Python 3.14+, the 'zstandard' "
            "module, or 'bsdtar'/'zstd' on PATH"
        )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExtractError(f"{cmd[0]} failed: {proc.stderr.strip()}")
