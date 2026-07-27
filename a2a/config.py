"""Repository definitions, paths, and persisted user config.

A repo is just a pacman sync database plus a base URL its package files live
under. In a pacman repo the ``.db`` and every ``*.pkg.tar.*`` sit in the same
directory, so ``pkg_base_url`` defaults to the directory of ``db_url``.

The default set is "everything on": the official repos plus every large
prebuilt-binary community repo. Distro repos (Manjaro/Garuda/EndeavourOS) and
optimized rebuilds (ALHP/CachyOS) are deliberately excluded — the former drift
from Arch versions, the latter add no new packages.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

ARCH = "x86_64"

# Where the AppImage runtime + appimagetool come from.
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
# Canonical AppImage library excludelist (bundled copy in excludelist.py is the
# fallback; this is fetched + cached opportunistically to stay current).
EXCLUDELIST_URL = (
    "https://raw.githubusercontent.com/AppImage/pkg2appimage/master/excludelist"
)


@dataclass
class Repo:
    """One pacman binary repository."""

    name: str
    db_url: str
    # Optional explicit override; otherwise derived from db_url's directory.
    pkg_base_url: str | None = None
    enabled: bool = True
    # Lower sorts first when the same package name appears in several repos.
    priority: int = 100

    def base_url(self) -> str:
        if self.pkg_base_url:
            return self.pkg_base_url.rstrip("/")
        return self.db_url.rsplit("/", 1)[0]


def _official(name: str, priority: int) -> Repo:
    return Repo(
        name=name,
        db_url=f"https://geo.mirror.pkgbuild.com/{name}/os/{ARCH}/{name}.db",
        priority=priority,
    )


# Order == default priority. Official first so its versions win ties.
DEFAULT_REPOS: list[Repo] = [
    _official("core", 10),
    _official("extra", 20),
    _official("multilib", 30),
    Repo(
        name="chaotic-aur",
        db_url=f"https://cdn-mirror.chaotic.cx/chaotic-aur/{ARCH}/chaotic-aur.db",
        priority=40,
    ),
    Repo(
        name="archlinuxcn",
        db_url=f"https://repo.archlinuxcn.org/{ARCH}/archlinuxcn.db",
        priority=50,
    ),
    Repo(
        name="arch4edu",
        db_url=f"https://mirrors.tuna.tsinghua.edu.cn/arch4edu/{ARCH}/arch4edu.db",
        priority=60,
    ),
    Repo(
        name="blackarch",
        db_url=f"https://ftp.halifax.rwth-aachen.de/blackarch/blackarch/os/{ARCH}/blackarch.db",
        priority=70,
    ),
    Repo(
        name="archstrike",
        db_url=f"https://mirror.archstrike.org/{ARCH}/archstrike/archstrike.db",
        priority=80,
    ),
]


def cache_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "arch2appimage")


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "arch2appimage")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def db_cache_dir() -> str:
    return os.path.join(cache_dir(), "db")


def pkg_cache_dir() -> str:
    return os.path.join(cache_dir(), "pkg")


def tool_cache_dir() -> str:
    return os.path.join(cache_dir(), "tool")


@dataclass
class Config:
    """User-persisted settings."""

    repos: list[Repo] = field(default_factory=lambda: [Repo(**asdict(r)) for r in DEFAULT_REPOS])
    output_dir: str = field(default_factory=lambda: os.path.expanduser("~"))
    bundle_deps: bool = True
    # Re-download a repo db if the cached copy is older than this many hours.
    db_max_age_hours: int = 12

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not os.path.exists(path):
            cfg = cls()
            cfg.save()
            return cfg
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return cls()
        repos = [Repo(**r) for r in raw.get("repos", [])] or None
        cfg = cls(
            repos=repos or [Repo(**asdict(r)) for r in DEFAULT_REPOS],
            output_dir=raw.get("output_dir", os.path.expanduser("~")),
            bundle_deps=raw.get("bundle_deps", True),
            db_max_age_hours=raw.get("db_max_age_hours", 12),
        )
        # Merge in any repos added to DEFAULT_REPOS since the config was written.
        known = {r.name for r in cfg.repos}
        for r in DEFAULT_REPOS:
            if r.name not in known:
                cfg.repos.append(Repo(**asdict(r)))
        return cfg

    def save(self) -> None:
        os.makedirs(config_dir(), exist_ok=True)
        data = {
            "repos": [asdict(r) for r in self.repos],
            "output_dir": self.output_dir,
            "bundle_deps": self.bundle_deps,
            "db_max_age_hours": self.db_max_age_hours,
        }
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, config_path())

    def enabled_repos(self) -> list[Repo]:
        return sorted((r for r in self.repos if r.enabled), key=lambda r: r.priority)
