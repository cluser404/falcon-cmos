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

## Tuning capture speed

The full-res `CAPTURE` reply is meant to be fast, not just correct: the
"main" stream runs continuously alongside "lores" so grabbing its current
frame has nothing to reconfigure/re-trigger, and `FULL_PNG_COMPRESS_LEVEL`
in `cmos_stream.py` (0-9, default `1`) trades PNG file size for encode
speed - it's purely a zlib compression-level knob, so the frame stays
exactly as lossless at `1` as at the default `6`. If a `CAPTURE` round trip
still feels slow on real hardware, lowering this further (or raising it if
you'd rather trade speed back for smaller files) is the first thing to try
before anything else - PREVIEW_JPEG_QUALITY and the preview/full sizes are
the other two levers, but the preview path (JPEG, small frame) was never
the bottleneck.
