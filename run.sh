#!/bin/sh
# Launch Arch2AppimageGUI. Passes any arguments through to the CLI; with none,
# opens the GUI.
here="$(dirname "$(readlink -f "$0")")"
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        exec "$py" "$here/Arch2AppimageGUI.py" "$@"
    fi
done
echo "Arch2AppimageGUI: no python3 found on PATH" >&2
exit 1
