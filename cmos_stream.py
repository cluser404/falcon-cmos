#!/usr/bin/env python3
"""CMOS microscope camera streaming server (Raspberry Pi side).

Continuously streams small, fast JPEG preview frames to the connected
client (falcon's CMOS device - see ../falcon/src/falcon/devices/cmos.py)
and, only when the client sends a "CAPTURE" command, captures and sends a
single lossless full-resolution PNG frame instead - so the live preview
stays fast, AND the on-demand full-res capture itself is controlled
(happens only on request) and fast (not just correct): the "main" stream
is always running alongside "lores" (see build_camera), so grabbing its
current frame is effectively instant with nothing to reconfigure/re-
trigger, and encode_full's PNG compression level is tuned down from PIL's
slow default specifically so the encode - the actual bottleneck on the
Pi's CPU - doesn't turn a "fast on-demand capture" into a multi-second
wait. See FULL_PNG_COMPRESS_LEVEL.

Wire protocol
-------------
Outgoing frames (Pi -> client), one per streamed image:
    1 byte    type: b"P" = JPEG preview frame, b"F" = full-res PNG frame
    4 bytes   big-endian uint32 payload length
    N bytes   the JPEG- or PNG-encoded image

Incoming commands (client -> Pi), newline-terminated ASCII lines:
    CAPTURE   request exactly one full-resolution PNG frame. The preview
              stream keeps flowing in between requests; the single "F"
              frame is interleaved into it as soon as the capture and PNG
              encode complete.

One client at a time (same as the original single-stream script this
replaces). Requires: picamera2, opencv-python, numpy.
"""
import io
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

# PNG compression level (0-9, PIL/zlib's deflate level - the same knob
# Pillow's PNG writer exposes as `compress_level`). This ONLY trades encode
# CPU time for output size - it never touches image data, so the full-res
# capture stays exactly as lossless at level 1 as it would at the default
# level 6. Level 6 (PIL's default, used by the original single-stream
# script) is what made a CAPTURE reply take multiple seconds on the Pi's
# CPU; level 1 is dramatically faster to encode for a modest size increase,
# which is what actually makes "request full-res, get it back fast" true.
FULL_PNG_COMPRESS_LEVEL = 1

TYPE_PREVIEW = b"P"
TYPE_FULL = b"F"
HEADER = struct.Struct(">cI")  # type byte + big-endian uint32 length


def build_camera():
    picam2 = Picamera2()
    # main + lores together, both running continuously off the same sensor
    # capture - this is what lets a CAPTURE command grab a full-res "main"
    # frame without ever stopping/reconfiguring the fast "lores" preview.
    config = picam2.create_video_configuration(
        main={"size": FULL_SIZE, "format": "RGB888"},
        lores={"size": PREVIEW_SIZE, "format": "YUV420"},
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


def encode_full(picam2):
    # capture_file (rather than a manual capture_array + cv2 encode) matches
    # the original single-stream script's tested-working full-res PNG path:
    # it goes through PIL, which handles the "main" stream's RGB888 pixel
    # layout correctly without a hand-rolled, easy-to-get-wrong color-order
    # conversion. Grabbing "main"'s current buffer is itself effectively
    # instant - it's already continuously running alongside "lores" (see
    # build_camera), so there's no reconfigure/re-trigger to wait on here;
    # the PNG *encode* is the only real cost, which compress_level below
    # addresses.
    buf = io.BytesIO()
    try:
        picam2.capture_file(buf, format="png", name="main", compress_level=FULL_PNG_COMPRESS_LEVEL)
    except TypeError:
        # Some picamera2 versions may not forward PNG save kwargs through
        # capture_file - fall back to its default (slower) compression
        # rather than failing the capture outright.
        buf = io.BytesIO()
        picam2.capture_file(buf, format="png", name="main")
    return buf.getvalue()


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
                payload = encode_full(picam2)
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
        f"full-res {FULL_SIZE[0]}x{FULL_SIZE[1]} PNG on CAPTURE"
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
