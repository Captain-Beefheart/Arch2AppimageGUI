"""Static entry point for PyInstaller.

The normal entry (Arch2AppimageGUI.py) imports the GUI lazily inside a function,
which PyInstaller's static analysis can't follow. This module imports everything
at top level so the whole app is bundled, then launches the GUI.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pull in every module so PyInstaller bundles them.
import a2a  # noqa: F401
import a2a.appdir  # noqa: F401
import a2a.appimage  # noqa: F401
import a2a.builder  # noqa: F401
import a2a.cli  # noqa: F401
import a2a.config  # noqa: F401
import a2a.deps  # noqa: F401
import a2a.excludelist  # noqa: F401
import a2a.extract  # noqa: F401
import a2a.repos  # noqa: F401
import a2a.util  # noqa: F401
from gui.arch2appimage_gui import main

if __name__ == "__main__":
    raise SystemExit(main())
