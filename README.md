# falcon-cmos

Raspberry Pi-side streaming server for the CMOS microscope camera used by
FALCON (`../falcon`). Replaces the earlier single-stream full-res-PNG-only
script: the live preview was too slow (a synchronous full-resolution
lossless PNG encode every frame), so this version streams small, fast JPEG
preview frames continuously and only produces a full-resolution frame when
the client explicitly asks for one - and that on-demand frame is sent as
raw pixel bytes, not PNG, because PNG-encoding it on the Pi's CPU turned
out to be the real bottleneck (see "Full-res capture speed" below).

## Setup (venv)

`picamera2` comes from apt, not PyPI - it's built against the Pi OS
image's system libcamera bindings, so it has to be installed system-wide
first, then the venv created with `--system-site-packages` so it can still
see it:

```
sudo apt update && sudo apt install -y python3-picamera2
./setup_venv.sh
```

`setup_venv.sh` creates `venv` (via `uv venv --system-site-packages` if
`uv` is available, else `python3 -m venv --system-site-packages`) and
installs the two PyPI dependencies (`opencv-python-headless`, `numpy`)
into it - safe to re-run any time to refresh the venv. The name `venv`
(not `.venv`) matches what's actually deployed on the Pi and what
`systemd/camera-serve.service` points at - keep them in sync if you ever
rename it.

## Running

```
venv/bin/python cmos_stream.py
```

Listens on `0.0.0.0:5000`, one client at a time.

### As a systemd service

`systemd/camera-serve.service` is the unit actually running on the
deployed Pi - installed at `/etc/systemd/system/camera-serve.service`,
`ExecStart` pointing at `/root/falcon-cmos/venv/bin/python
/root/falcon-cmos/cmos_stream.py`, `Restart=always`. To (re)install it
after changing either the unit file or the venv/repo path:

```
cp systemd/camera-serve.service /etc/systemd/system/camera-serve.service
systemctl daemon-reload
systemctl restart camera-serve.service
systemctl status camera-serve.service --no-pager
```

If `ExecStart` ever points at `/usr/bin/python3` instead of
`venv/bin/python`, the service will crash-loop on `ModuleNotFoundError:
No module named 'cv2'` - `opencv-python-headless`/`numpy` only live in the
venv, not the system Python (see Setup above). `journalctl -u
camera-serve.service --no-pager -n 50` is the first place to look if it's
not starting.

## Protocol

See the docstring at the top of `cmos_stream.py` for the exact wire
format. In short: every streamed frame is a 1-byte type tag (`P` = JPEG
preview, `F` = full-res raw BGR) + 4-byte big-endian length + payload, and
the client requests a full-res frame by sending the ASCII line
`CAPTURE\n`. The "F" payload itself starts with an 8-byte width/height
sub-header before the raw pixel bytes - it is NOT a PNG or any other
codec, deliberately (see below).

The falcon app's client side lives in
`../falcon/src/falcon/devices/cmos.py` (`CMOS.request_hires()`).

## Full-res capture speed

The on-demand `CAPTURE` reply sends **raw, uncompressed pixel bytes**, not
PNG - found by measuring directly on the deployed hardware (a memory-
constrained Pi: ~390MB RAM, already under enough memory pressure to be
swapping). `capture_array` on the continuously-running "main" stream is
fast regardless (~0.1s measured, nothing to reconfigure/re-trigger for a
CAPTURE), but PNG-encoding the ~24MB frame measured **5-38s, and
erratically so** (consistent with swap thrashing, not just raw compute
cost) - REGARDLESS of PNG compression level; that avenue was tried first
and did not fix it. Sending the frame raw instead measures a consistent
**~1.4-1.6s** end to end (acquire + ~23MB transfer over the link-local
connection) - the falcon client does the lossless PNG encode itself when
it actually saves a capture to disk, on a machine with far more CPU
headroom.

If a `CAPTURE` round trip ever regresses on real hardware, check
`journalctl -u camera-serve.service` and `free -h`/`cat /proc/meminfo |
grep Cma` first - the earlier PNG-encode slowness looked like it might
have been a compression-level tuning problem but was actually a hardware
memory-pressure problem, so a fresh regression is more likely to be that
class of issue again than something to fix by re-adding encoding.
