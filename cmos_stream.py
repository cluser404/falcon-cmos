#!/usr/bin/env python3
"""CMOS microscope camera streaming server (Raspberry Pi side).

Continuously streams small, fast JPEG preview frames to the connected
client (falcon's CMOS device - see ../falcon/src/falcon/devices/cmos.py)
and, only when the client sends a "CAPTURE" command, captures and sends a
single full-resolution frame instead - so the live preview stays fast,
AND the on-demand full-res capture itself is controlled (happens only on
request) and fast (not just correct).

The full-res frame is sent as RAW, UNCOMPRESSED pixel bytes, not PNG -
this is deliberate, found by measuring on the actual target hardware
(a memory-constrained Pi: ~390MB RAM, already under enough memory
pressure to be swapping). Acquisition is fast either way (capture_array
on the continuously-running "main" stream, ~0.1s measured, nothing to
reconfigure/re-trigger) - the bottleneck was PNG-encoding the ~24MB frame
on this CPU, which measured 5-38s (erratic - consistent with swap
thrashing under memory pressure, not just raw compute cost) REGARDLESS of
PNG compression level (an earlier attempt at tuning that level down is
why you may see it referenced elsewhere in history; it did not fix this).
Sending raw bytes instead skips that entirely: the client (falcon,
running on a real desktop CPU with far more headroom) does the lossless
PNG encode itself when it actually writes a capture to disk - see
CMOS._read_frames in devices/cmos.py.

Wire protocol
-------------
Outgoing frames (Pi -> client), one per streamed image:
    1 byte    type: b"P" = JPEG preview frame, b"F" = full-res raw frame
    4 bytes   big-endian uint32 payload length
    N bytes   the payload (format depends on type, see below)

"P" payload: JPEG-encoded preview image bytes, decode directly.
"F" payload: 8 bytes (big-endian uint32 width, uint32 height) followed by
    width*height*3 raw interleaved BGR uint8 bytes (row-major, no padding)
    - picamera2's "RGB888" format is, despite the name, laid out as BGR in
    memory (a documented picamera2 quirk), which is also directly the byte
    order OpenCV/cv2 expects, so this needs no color conversion on either
    end; confirmed empirically against the known-correct PIL-based capture
    path before switching to this raw path (channel means tracked pairwise
    across both, ruling out a channel swap).

Incoming commands (client -> Pi), newline-terminated ASCII lines:
    CAPTURE   request exactly one full-resolution raw frame. The preview
              stream keeps flowing in between requests; the single "F"
              frame is interleaved into it as soon as the capture
              completes.

One client at a time (same as the original single-stream script this
replaces). Requires: picamera2, opencv-python, numpy.
"""
import socket
import struct
import threading
import time

import cv2
from picamera2 import Picamera2

HOST = "0.0.0.0"
PORT = 5000

FULL_SIZE = (3280, 2464)    # Pi Camera v2 full resolution - "main" stream
PREVIEW_SIZE = (640, 480)   # fast preview - "lores" stream
PREVIEW_JPEG_QUALITY = 75

TYPE_PREVIEW = b"P"
TYPE_FULL = b"F"
HEADER = struct.Struct(">cI")      # type byte + big-endian uint32 length
FULL_DIMS = struct.Struct(">II")   # "F" payload's own width/height sub-header


def build_camera():
    picam2 = Picamera2()
    # main + lores together, both running continuously off the same sensor
    # capture - this is what lets a CAPTURE command grab a full-res "main"
    # frame without ever stopping/reconfiguring the fast "lores" preview.
    #
    # buffer_count=2 (default is higher, e.g. 4-6) is required, not just an
    # optimization: create_video_configuration also provisions a full-
    # sensor-resolution "raw" stream feeding the ISP alongside main+lores,
    # and on a memory-constrained Pi (the target board here has ~390MB
    # total RAM, ~256MB of that CMA-reserved) the default buffer count
    # across all three streams was enough to exhaust the DMA heap outright
    # (OSError: Cannot allocate memory, before the camera could even start)
    # - not a slowness issue, a hard failure. 2 buffers per stream is the
    # minimum for continuous double-buffered capture and comfortably fits.
    config = picam2.create_video_configuration(
        main={"size": FULL_SIZE, "format": "RGB888"},
        lores={"size": PREVIEW_SIZE, "format": "YUV420"},
        buffer_count=2,
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)  # let AE/AWB settle
    return picam2


def encode_preview(picam2):
    yuv = picam2.capture_array("lores")
    bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
    return buf.tobytes() if ok else None


def build_full_payload(picam2):
    # No encoding step at all, deliberately - see the module docstring for
    # why (PNG-encoding this frame on the Pi's CPU was the actual
    # bottleneck, not acquisition). capture_array on the continuously-
    # running "main" stream just returns its current buffer.
    arr = picam2.capture_array("main")
    h, w = arr.shape[:2]
    return FULL_DIMS.pack(w, h) + arr.tobytes()


def command_reader(conn, capture_event, stop_event):
    """Reads newline-terminated commands from the client and sets
    capture_event on "CAPTURE". Runs in its own thread so it can block on
    recv() without holding up the frame-writer loop below."""
    buf = b""
    try:
        while not stop_event.is_set():
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip() == b"CAPTURE":
                    capture_event.set()
    except OSError:
        pass
    finally:
        stop_event.set()


def send_frame(conn, frame_type, payload):
    conn.sendall(HEADER.pack(frame_type, len(payload)) + payload)


def serve_client(conn, addr, picam2):
    print(f"Client connected: {addr}")
    capture_event = threading.Event()
    stop_event = threading.Event()

    reader = threading.Thread(
        target=command_reader, args=(conn, capture_event, stop_event), daemon=True,
    )
    reader.start()

    try:
        while not stop_event.is_set():
            if capture_event.is_set():
                capture_event.clear()
                payload = build_full_payload(picam2)
                send_frame(conn, TYPE_FULL, payload)
                continue

            payload = encode_preview(picam2)
            if payload is not None:
                send_frame(conn, TYPE_PREVIEW, payload)
    except (BrokenPipeError, ConnectionResetError, OSError):
        print("Client disconnected")
    finally:
        stop_event.set()
        conn.close()
        reader.join(timeout=1.0)


def main():
    picam2 = build_camera()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(1)

    print(
        f"Listening on {HOST}:{PORT} - preview {PREVIEW_SIZE[0]}x{PREVIEW_SIZE[1]} JPEG, "
        f"full-res {FULL_SIZE[0]}x{FULL_SIZE[1]} raw BGR on CAPTURE"
    )

    try:
        while True:
            conn, addr = srv.accept()
            serve_client(conn, addr, picam2)
    finally:
        picam2.stop()
        srv.close()


if __name__ == "__main__":
    main()
