"""
Draws the plugin icon, stacked_terrain_builder/icon.png (256 x 256): five
nested slabs in Spectral colors, lifted up the page, with walls.

Run it from the QGIS Python console, which already has a QGuiApplication:

    import sys; sys.argv = ["", "/path/to/stacked_terrain_builder/icon.png"]
    exec(open("/path/to/dev/make_icon.py").read())
"""
import math
import sys

from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
S = 512
img = QImage(S, S, QImage.Format.Format_ARGB32)
img.fill(Qt.GlobalColor.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.RenderHint.Antialiasing)

# Spectral-like ramp, low to high
colors = ["#3288bd", "#66c2a5", "#e6f598", "#fdae61", "#d53e4f"]


def blob(cx, cy, r, seed):
    path = QPainterPath()
    n = 64
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 1 + 0.10 * math.sin(3 * a + seed) + 0.06 * math.sin(5 * a + 2 * seed)
        pts.append(QPointF(cx + r * k * math.cos(a), cy + 0.78 * r * k * math.sin(a)))
    path.moveTo(pts[0])
    for q in pts[1:]:
        path.lineTo(q)
    path.closeSubpath()
    return path


lift = 26
for i, c in enumerate(colors):
    r = 206 - i * 38
    cx = S / 2 - 12 + i * 8
    cy = S / 2 + 62 - i * lift - i * 8
    shape = blob(cx, cy, r, i * 0.9)
    face = QColor(c)
    for k in range(lift, 0, -2):  # wall: darker copies under the face
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(face.darker(170))
        p.drawPath(shape.translated(0, k))
    p.setBrush(face)
    p.setPen(QPen(QColor(255, 255, 255, 200), 5))
    p.drawPath(shape)
p.end()
img.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
           Qt.TransformationMode.SmoothTransformation).save(sys.argv[1])
print("saved", sys.argv[1])
