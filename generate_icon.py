"""
Generate assets/icon.ico for ClipFlow.
Chạy 1 lần trước khi build: python generate_icon.py

Không cần Pillow — dùng PyQt6 render PNG rồi tự đóng gói thành .ico.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap, QPolygon
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

ICON_BG = "#2563eb"   # blue — khớp với theme primary
ICON_FG = "#ffffff"   # white mark
SIZES = [16, 24, 32, 48, 64, 128, 256]
OUTPUT = Path(__file__).parent / "assets" / "icon.ico"


def render_png(size: int) -> bytes:
    """Vẽ icon ClipFlow (play ▶ + thanh dọc) tại kích thước size×size, trả về PNG bytes."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = size
    margin = max(1, round(s * 0.06))
    radius = max(3, round(s * 0.22))

    # Nền bo góc màu xanh
    painter.setBrush(QColor(ICON_BG))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(margin, margin, s - 2 * margin, s - 2 * margin, radius, radius)

    # Tam giác play (▶) — filled
    painter.setBrush(QColor(ICON_FG))
    painter.setPen(Qt.PenStyle.NoPen)
    tx_left  = round(s * 0.27)
    tx_right = round(s * 0.63)
    ty_top   = round(s * 0.27)
    ty_bot   = round(s * 0.73)
    ty_mid   = s // 2
    painter.drawPolygon(QPolygon([
        QPoint(tx_left, ty_top),
        QPoint(tx_left, ty_bot),
        QPoint(tx_right, ty_mid),
    ]))

    # Thanh dọc bên phải (next-track bar)
    bar_x = round(s * 0.69)
    bar_w = max(2, round(s * 0.09))
    painter.drawRect(bar_x, ty_top, bar_w, ty_bot - ty_top)

    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(buf.data())


def build_ico(png_map: dict[int, bytes]) -> bytes:
    """Đóng gói dict {size: png_bytes} thành binary .ico (PNG-embedded, Vista+)."""
    sizes = sorted(png_map.keys(), reverse=True)
    n = len(sizes)
    offset = 6 + 16 * n  # header + directory

    parts: list[bytes] = []
    # ICONDIR header
    parts.append(struct.pack("<HHH", 0, 1, n))
    # ICONDIRENTRY cho mỗi kích thước
    for size in sizes:
        data = png_map[size]
        dim = 0 if size >= 256 else size  # 0 = 256 theo spec .ico
        parts.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    # Blob PNG
    for size in sizes:
        parts.append(png_map[size])

    return b"".join(parts)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    png_map: dict[int, bytes] = {}
    for size in SIZES:
        print(f"  Rendering {size}×{size}...", flush=True)
        png_map[size] = render_png(size)

    ico_data = build_ico(png_map)
    OUTPUT.write_bytes(ico_data)
    print(f"\n[OK] {OUTPUT}  ({OUTPUT.stat().st_size:,} bytes)", flush=True)
    print("[next] Chạy: python check.py  để build exe với icon mới.", flush=True)


if __name__ == "__main__":
    main()
