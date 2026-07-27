"""Command-line interface. Handy for headless builds and scripting.

    Arch2AppimageGUI search firefox
    Arch2AppimageGUI build firefox
    Arch2AppimageGUI build firefox --repo chaotic-aur -o ~/AppImages
    Arch2AppimageGUI repos
    Arch2AppimageGUI sync --force
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, config
from .builder import build_appimage
from .repos import build_index


def _progress(msg: str, frac: float | None) -> None:
    pct = f"[{int(frac*100):3d}%] " if frac is not None else "        "
    print(f"{pct}{msg}", flush=True)


def _load_index(cfg, force=False):
    print("Syncing repositories...", flush=True)
    return build_index(cfg, progress=_progress, force=force)


def cmd_repos(args, cfg) -> int:
    for r in cfg.repos:
        flag = "on " if r.enabled else "off"
        print(f"  [{flag}] {r.name:<14} prio={r.priority:<4} {r.db_url}")
    return 0


def cmd_sync(args, cfg) -> int:
    index = _load_index(cfg, force=args.force)
    print(f"\n{index.count()} packages across {len(cfg.enabled_repos())} repos.")
    return 0


def cmd_search(args, cfg) -> int:
    index = _load_index(cfg)
    results = index.search(args.query, limit=args.limit)
    if not results:
        print("No matches.")
        return 1
    print(f"\n{len(results)} result(s):\n")
    for p in results:
        print(f"  {p.name:<28} {p.version:<20} [{p.repo}]")
        if p.desc:
            print(f"      {p.desc[:90]}")
    return 0


def cmd_selftest(args, cfg) -> int:
    """Verify the runtime stack: a2a imports cleanly and Python/Tk works.

    Used by CI (under xvfb) to fail a broken AppImage before it ships.
    """
    from . import (  # noqa: F401 — importing is the test
        appdir, appimage, builder, deps, excludelist, extract, repos, util,
    )
    print("a2a modules import: ok")
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.update()
        root.destroy()
        print(f"tkinter: ok (Tcl/Tk {tkinter.TkVersion})")
    except Exception as exc:  # noqa: BLE001
        print(f"tkinter: FAILED ({exc})", file=sys.stderr)
        return 1
    backend = extract.zst_backend()
    if backend:
        print(f"zst extraction backend: {backend}")
    else:
        print("zst extraction backend: NONE — cannot unpack .pkg.tar.zst",
              file=sys.stderr)
        return 1
    print("selftest ok")
    return 0


def cmd_build(args, cfg) -> int:
    index = _load_index(cfg)
    candidates = index.by_name.get(args.package)
    if not candidates:
        # try a search to be helpful
        near = index.search(args.package, limit=8)
        print(f"Package '{args.package}' not found in enabled repos.")
        if near:
            print("Did you mean:")
            for p in near:
                print(f"  {p.name} [{p.repo}]")
        return 1
    pkg = candidates[0]
    if args.repo:
        match = [p for p in candidates if p.repo == args.repo]
        if not match:
            print(f"'{args.package}' is not in repo '{args.repo}'. "
                  f"Available: {', '.join(p.repo for p in candidates)}")
            return 1
        pkg = match[0]

    if args.no_deps:
        cfg.bundle_deps = False
    print(f"\nBuilding {pkg.name} {pkg.version} from [{pkg.repo}]\n")
    try:
        out = build_appimage(pkg, index, cfg, progress=_progress,
                             output_dir=args.output, keep_appdir=args.keep_appdir)
    except Exception as exc:  # noqa: BLE001
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        return 1
    print(f"\n✔ {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="Arch2AppimageGUI",
                                description="Search prebuilt Arch repos and build AppImages.")
    p.add_argument("--version", action="version", version=f"Arch2AppimageGUI {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("repos", help="list configured repositories")
    sub.add_parser("selftest", help="verify the bundled Python/Tk stack (used by CI)")

    ps = sub.add_parser("sync", help="download/refresh repo databases")
    ps.add_argument("--force", action="store_true", help="ignore cache freshness")

    pf = sub.add_parser("search", help="search for a package")
    pf.add_argument("query")
    pf.add_argument("--limit", type=int, default=40)

    pb = sub.add_parser("build", help="build an AppImage for a package")
    pb.add_argument("package")
    pb.add_argument("--repo", help="force a specific repo")
    pb.add_argument("-o", "--output", help="output directory")
    pb.add_argument("--no-deps", action="store_true", help="don't bundle dependencies")
    pb.add_argument("--keep-appdir", action="store_true", help="leave the AppDir for inspection")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.Config.load()
    handlers = {
        "repos": cmd_repos,
        "sync": cmd_sync,
        "search": cmd_search,
        "build": cmd_build,
        "selftest": cmd_selftest,
    }
    return handlers[args.cmd](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
