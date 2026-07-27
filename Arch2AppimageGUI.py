#!/usr/bin/env python3
"""Arch2AppimageGUI entry point.

No arguments -> launch the GUI. Any arguments -> run the CLI.

    ./Arch2AppimageGUI.py                 # GUI
    ./Arch2AppimageGUI.py search firefox  # CLI
    ./Arch2AppimageGUI.py build firefox
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    if len(sys.argv) > 1:
        from a2a.cli import main as cli_main
        return cli_main()
    # GUI
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui"))
    from gui.arch2appimage_gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
