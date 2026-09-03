#!/usr/bin/env bash
# Create/refresh the virtualenv this project runs from, on the Raspberry Pi.
#
# --system-site-packages is required, not optional: picamera2 and its
# libcamera bindings come from apt (`sudo apt install -y python3-picamera2`),
# built against this Pi OS image's system Python - they are not normal PyPI
# packages and pip-installing them into an isolated venv will not work (or
# will pull an unrelated/broken build). This flag lets the venv still see
# whatever apt already installed system-wide, while opencv-python-headless
# and numpy (the two dependencies that *do* come from PyPI cleanly) are
# installed into the venv itself as usual.
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import picamera2" 2>/dev/null; then
    echo "picamera2 not found in the system Python - install it first:" >&2
    echo "  sudo apt update && sudo apt install -y python3-picamera2" >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    uv venv --system-site-packages venv
    uv pip install --python venv opencv-python-headless numpy
else
    python3 -m venv --system-site-packages venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install opencv-python-headless numpy
fi

echo "Done. Run the server with:"
echo "  venv/bin/python cmos_stream.py"
