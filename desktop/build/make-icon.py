"""Render the placeholder app icon (icon.png, 1024x1024 RGBA) with the stdlib only, then build icon.icns
with macOS sips + iconutil. Run: python3 make-icon.py   (icon.svg documents the same design)."""
import math, os, struct, subprocess, sys, zlib

N = 1024


def rrect(px, py, x0, y0, x1, y1, r):  # signed distance to a rounded rectangle (negative inside)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    qx, qy = abs(px - cx) - ((x1 - x0) / 2 - r), abs(py - cy) - ((y1 - y0) / 2 - r)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def seg(px, py, ax, ay, bx, by):  # distance to a segment
    dx, dy = bx - ax, by - ay
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def cov(d):  # antialiased coverage from a signed distance in pixels
    return max(0.0, min(1.0, 0.5 - d))


def pixel(x, y):
    px, py = x + 0.5, y + 0.5
    a_bg = cov(rrect(px, py, 100, 100, 924, 924, 184))
    if a_bg == 0:
        return (0, 0, 0, 0)
    t = (px + py - 200) / 1648
    bg = (99 + (67 - 99) * t, 102 + (56 - 102) * t, 241 + (202 - 241) * t)  # #6366f1 -> #4338ca
    c, a_env = bg, cov(rrect(px, py, 262, 332, 762, 692, 44))
    c = tuple(v * (1 - a_env) + 255 * a_env for v in c)
    d = min(seg(px, py, 286, 366, 512, 548), seg(px, py, 512, 548, 738, 366))
    a_fl = cov(d - 20) * a_env  # flap stroke, clipped to the envelope
    c = tuple(v * (1 - a_fl) + f * a_fl for v, f in zip(c, (67, 56, 202)))
    return (round(c[0]), round(c[1]), round(c[2]), round(255 * a_bg))


def png(path):
    rows = bytearray()
    for y in range(N):
        rows.append(0)
        for x in range(N):
            rows += bytes(pixel(x, y))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


here = os.path.dirname(os.path.abspath(__file__))
png(os.path.join(here, "icon.png"))
iconset = os.path.join(here, "icon.iconset")
os.makedirs(iconset, exist_ok=True)
for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        subprocess.run(["sips", "-z", str(size * scale), str(size * scale), os.path.join(here, "icon.png"),
                        "--out", os.path.join(iconset, name)], check=True, capture_output=True)
subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(here, "icon.icns")], check=True)
subprocess.run(["rm", "-rf", iconset])
print("wrote icon.icns", file=sys.stderr)
