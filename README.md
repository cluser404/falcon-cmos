# falcon-cmos

Raspberry Pi-side streaming server for the CMOS microscope camera used by
FALCON (`../falcon`). Replaces the earlier single-stream full-res-PNG-only
script: the live preview was too slow (a synchronous full-resolution
lossless PNG encode every frame), so this version streams small, fast JPEG
preview frames continuously and only produces a full-resolution PNG frame
when the client explicitly asks for one.

## Running

```
pip install picamera2 opencv-python
python3 cmos_stream.py
```

Listens on `0.0.0.0:5000`, one client at a time.

## Protocol

See the docstring at the top of `cmos_stream.py` for the exact wire
format. In short: every streamed frame is a 1-byte type tag (`P` = JPEG
preview, `F` = full-res PNG) + 4-byte big-endian length + payload, and the
client requests a full-res frame by sending the ASCII line `CAPTURE\n`.

The falcon app's client side lives in
`../falcon/src/falcon/devices/cmos.py` (`CMOS.request_hires()`).
