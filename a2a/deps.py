"""Resolve the runtime dependency closure of a package.

We walk each package's ``%DEPENDS%`` transitively, mapping every dependency to a
concrete package (directly by name, or via another package's ``%PROVIDES%``).
Packages in BASE_SKIP — the parts of every Linux userland that are guaranteed
present and must come from the host — are not pulled in; the leftover
excludelisted libraries are pruned later at the ``.so`` level.
"""

from __future__ import annotations

from .repos import Index, Package, strip_constraint

# Always present on any glibc Linux host / part of Arch's base. Bundling these
# would bloat the AppImage and risk clashing with the host loader.
BASE_SKIP: set[str] = {
    "glibc", "gcc-libs", "filesystem", "bash", "coreutils", "sh",
    "tzdata", "iana-etc", "licenses", "ca-certificates", "ca-certificates-mozilla",
    "ca-certificates-utils", "pacman", "pacman-mirrorlist", "archlinux-keyring",
    "shadow", "util-linux", "util-linux-libs", "systemd", "systemd-libs",
    "systemd-sysvcompat", "linux-api-headers", "glib2", "glib2-docs",
    "gcc", "binutils", "libcap", "libcap-ng", "acl", "attr", "gmp", "libtool",
    "readline", "ncurses", "zlib", "xz", "bzip2", "zstd", "gdbm", "db5.3", "db",
    "pcre2", "libffi", "e2fsprogs", "krb5", "keyutils", "libxcrypt", "libnsl",
    "gpgme", "libgpg-error", "libgcrypt", "expat", "dbus",
}


class DepResult:
    def __init__(self) -> None:
        self.packages: list[Package] = []  # deps to bundle (excludes root)
        self.unresolved: list[str] = []    # dep names nothing provided
        self.skipped_base: set[str] = set()


def resolve(root: Package, index: Index, max_packages: int = 400) -> DepResult:
    """Return the transitive dependency packages to bundle alongside ``root``."""
    result = DepResult()
    seen: set[str] = {root.name}
    queue: list[str] = [strip_constraint(d) for d in root.depends]

    while queue and len(result.packages) < max_packages:
        dep = queue.pop(0)
        if not dep or dep in seen:
            continue
        seen.add(dep)

        if dep in BASE_SKIP:
            result.skipped_base.add(dep)
            continue

        pkg = index.best(dep)
        if pkg is None:
            result.unresolved.append(dep)
            continue
        if pkg.name in BASE_SKIP:
            result.skipped_base.add(pkg.name)
            continue
        if pkg.name == root.name:
            continue

        result.packages.append(pkg)
        seen.add(pkg.name)
        for sub in pkg.depends:
            name = strip_constraint(sub)
            if name and name not in seen:
                queue.append(name)

    # De-dup by package name, keep first (best-priority) occurrence.
    uniq: dict[str, Package] = {}
    for p in result.packages:
        uniq.setdefault(p.name, p)
    result.packages = list(uniq.values())
    return result
