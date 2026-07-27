"""Download and parse pacman sync databases into a searchable package index.

A pacman ``.db`` is a gzip-compressed tar. Inside, each package is a directory
``<name>-<version>/`` holding a ``desc`` text file with ``%KEY%``-delimited
fields (%NAME%, %FILENAME%, %VERSION%, %DESC%, %DEPENDS%, %PROVIDES%, ...).
We only need the handful of fields relevant to fetching and bundling.
"""

from __future__ import annotations

import io
import os
import tarfile
import time
from dataclasses import dataclass, field

from . import config
from .config import Config, Repo
from .util import Progress, download, null_progress


@dataclass
class Package:
    repo: str
    name: str
    version: str = ""
    filename: str = ""
    desc: str = ""
    csize: int = 0
    isize: int = 0
    depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    base_url: str = ""
    priority: int = 100

    def url(self) -> str:
        return f"{self.base_url}/{self.filename}"


def strip_constraint(dep: str) -> str:
    """`bar>=2.0` / `libfoo.so=1-64` -> `bar` / `libfoo.so`."""
    for i, ch in enumerate(dep):
        if ch in "<>=":
            return dep[:i]
    return dep


def _parse_desc(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    key: str | None = None
    for line in text.splitlines():
        if line.startswith("%") and line.endswith("%"):
            key = line[1:-1]
            fields[key] = []
        elif line.strip() == "":
            key = None
        elif key is not None:
            fields[key].append(line)
    return fields


def parse_db(data: bytes, repo: Repo) -> list[Package]:
    """Parse raw .db bytes into a list of Package for the given repo."""
    packages: list[Package] = []
    base = repo.base_url()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith("/desc"):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            fields = _parse_desc(fh.read().decode("utf-8", "replace"))
            name = _first(fields, "NAME")
            if not name:
                continue
            packages.append(
                Package(
                    repo=repo.name,
                    name=name,
                    version=_first(fields, "VERSION"),
                    filename=_first(fields, "FILENAME"),
                    desc=_first(fields, "DESC"),
                    csize=_int(_first(fields, "CSIZE")),
                    isize=_int(_first(fields, "ISIZE")),
                    depends=fields.get("DEPENDS", []),
                    provides=fields.get("PROVIDES", []),
                    base_url=base,
                    priority=repo.priority,
                )
            )
    return packages


def _first(fields: dict[str, list[str]], key: str) -> str:
    vals = fields.get(key)
    return vals[0] if vals else ""


def _int(s: str) -> int:
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def _db_is_fresh(path: str, max_age_hours: int) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def sync_repo(repo: Repo, cfg: Config, progress: Progress = null_progress,
              force: bool = False) -> str | None:
    """Ensure the repo's .db is cached and reasonably fresh. Returns its path,
    or None if it could not be fetched and no cached copy exists."""
    dest = os.path.join(config.db_cache_dir(), f"{repo.name}.db")
    if not force and _db_is_fresh(dest, cfg.db_max_age_hours):
        return dest
    try:
        download(repo.db_url, dest, progress=progress, label=f"{repo.name}.db")
        return dest
    except Exception as exc:  # noqa: BLE001 — a dead mirror shouldn't kill a sync
        progress(f"{repo.name}: {exc}", None)
        return dest if os.path.exists(dest) else None


class Index:
    """Merged, searchable view over every enabled repo."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[Package]] = {}
        self.provides: dict[str, list[Package]] = {}
        self._all: list[Package] = []

    def add(self, packages: list[Package]) -> None:
        for pkg in packages:
            self._all.append(pkg)
            self.by_name.setdefault(pkg.name, []).append(pkg)
            for prov in pkg.provides:
                self.provides.setdefault(strip_constraint(prov), []).append(pkg)
        # Keep best (lowest-priority number) repo first for each name.
        for lst in self.by_name.values():
            lst.sort(key=lambda p: p.priority)

    def best(self, name: str) -> Package | None:
        """Resolve a package name (or a provided/virtual name) to one Package."""
        direct = self.by_name.get(name)
        if direct:
            return direct[0]
        prov = self.provides.get(name)
        if prov:
            return sorted(prov, key=lambda p: p.priority)[0]
        return None

    def count(self) -> int:
        return len(self._all)

    def search(self, query: str, limit: int = 500) -> list[Package]:
        """Rank packages by how well they match a free-text query.

        Only the best (highest-priority repo) package per name is returned so
        the results list isn't cluttered with the same app from several repos.
        """
        q = query.strip().lower()
        if not q:
            return []
        terms = q.split()
        seen: set[str] = set()
        scored: list[tuple[int, Package]] = []
        for name, pkgs in self.by_name.items():
            pkg = pkgs[0]
            if name in seen:
                continue
            score = _score(name.lower(), pkg.desc.lower(), q, terms)
            if score > 0:
                scored.append((score, pkg))
                seen.add(name)
        scored.sort(key=lambda t: (-t[0], t[1].name))
        return [p for _, p in scored[:limit]]


def _score(name: str, desc: str, q: str, terms: list[str]) -> int:
    if name == q:
        return 1000
    score = 0
    if name.startswith(q):
        score = 500
    elif q in name:
        score = 300
    elif all(t in name for t in terms):
        score = 200
    elif all(t in name or t in desc for t in terms):
        score = 80
    if score == 0:
        return 0
    # Shorter names that contain the query are usually the thing you meant.
    score -= min(len(name), 60)
    return max(score, 1)


def build_index(cfg: Config, progress: Progress = null_progress,
                force: bool = False) -> Index:
    """Sync every enabled repo and parse them into one Index."""
    index = Index()
    repos = cfg.enabled_repos()
    for i, repo in enumerate(repos):
        progress(f"Syncing {repo.name} ({i+1}/{len(repos)})", i / max(len(repos), 1))
        path = sync_repo(repo, cfg, progress=progress, force=force)
        if not path:
            progress(f"{repo.name}: unavailable, skipping", None)
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            index.add(parse_db(data, repo))
        except (OSError, tarfile.TarError) as exc:
            progress(f"{repo.name}: parse failed ({exc})", None)
    progress(f"Index ready: {index.count()} packages", 1.0)
    return index
