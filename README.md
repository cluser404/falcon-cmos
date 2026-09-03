# falcon-cmos

Raspberry Pi-side streaming server for the CMOS microscope camera used by
FALCON (`../falcon`). Replaces the earlier single-stream full-res-PNG-only
script: the live preview was too slow (a synchronous full-resolution
lossless PNG encode every frame), so this version streams small, fast JPEG
preview frames continuously and only produces a full-resolution PNG frame
when the client explicitly asks for one.

## Setup (venv)

`picamera2` comes from apt, not PyPI - it's built against the Pi OS
image's system libcamera bindings, so it has to be installed system-wide
first, then the venv created with `--system-site-packages` so it can still
see it:

```
sudo apt update && sudo apt install -y python3-picamera2
./setup_venv.sh
```

`setup_venv.sh` creates `.venv` (via `uv venv --system-site-packages` if
`uv` is available, else `python3 -m venv --system-site-packages`) and
installs the two PyPI dependencies (`opencv-python-headless`, `numpy`)
into it - safe to re-run any time to refresh the venv.

## Running

```
.venv/bin/python cmos_stream.py
```

Listens on `0.0.0.0:5000`, one client at a time.

## Protocol

See the docstring at the top of `cmos_stream.py` for the exact wire
format. In short: every streamed frame is a 1-byte type tag (`P` = JPEG
preview, `F` = full-res PNG) + 4-byte big-endian length + payload, and the
client requests a full-res frame by sending the ASCII line `CAPTURE\n`.

The falcon app's client side lives in
`../falcon/src/falcon/devices/cmos.py` (`CMOS.request_hires()`).
