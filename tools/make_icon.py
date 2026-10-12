"""앱 아이콘(assets/app.png, app.ico)을 그린다: python -m tools.make_icon"""
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def draw(size=256):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size
    # 둥근 배경 (남보라 → 짙은 청)
    bg = QLinearGradient(0, 0, s, s)
    bg.setColorAt(0, QColor("#3a4a9a"))
    bg.setColorAt(1, QColor("#14161c"))
    p.setBrush(QBrush(bg))
    p.setPen(QPen(QColor("#6c8cff"), s * 0.025))
    p.drawRoundedRect(QRectF(s * .04, s * .04, s * .92, s * .92), s * .2, s * .2)
    # 도깨비 뿔 두 개 (오른쪽 뿔을 그리고 좌우 반전해서 왼쪽 뿔)
    for sign in (1, -1):
        p.save()
        if sign == -1:
            p.translate(s, 0)
            p.scale(-1, 1)
        path = QPainterPath()
        path.moveTo(s * .63, s * .43)
        path.quadTo(s * .66, s * .22, s * .80, s * .09)
        path.quadTo(s * .78, s * .30, s * .77, s * .43)
        path.closeSubpath()
        g = QLinearGradient(0, s * .09, 0, s * .43)
        g.setColorAt(0, QColor("#ffe27a"))
        g.setColorAt(1, QColor("#f5a623"))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawPath(path)
        p.restore()
    # 얼굴 (붉은 도깨비 얼굴)
    rg = QRadialGradient(QPointF(s * .5, s * .6), s * .32)
    rg.setColorAt(0, QColor("#ff6b5e"))
    rg.setColorAt(1, QColor("#c2332b"))
    p.setBrush(QBrush(rg))
    p.setPen(QPen(QColor("#7a1d18"), s * .012))
    p.drawEllipse(QRectF(s * .22, s * .36, s * .56, s * .5))
    # 눈, 엄니
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#fff"))
    for sign in (-1, 1):
        p.drawEllipse(QRectF(s * (.5 + sign * .14) - s * .05, s * .5, s * .1, s * .1))
        p.setBrush(QColor("#14161c"))
        p.drawEllipse(QRectF(s * (.5 + sign * .14) - s * .022, s * .527, s * .045, s * .05))
        p.setBrush(QColor("#fff"))
    mouth = QPainterPath()
    mouth.moveTo(s * .34, s * .7)
    mouth.quadTo(s * .5, s * .8, s * .66, s * .7)
    mouth.quadTo(s * .5, s * .76, s * .34, s * .7)
    p.setBrush(QColor("#3b0d0a"))
    p.drawPath(mouth)
    p.setBrush(QColor("#fff"))
    for sign in (-1, 1):
        t = QPainterPath()
        x = s * (.5 + sign * .1)
        t.moveTo(x - s * .025, s * .74)
        t.lineTo(x + s * .025, s * .74)
        t.lineTo(x, s * .82)
        t.closeSubpath()
        p.drawPath(t)
    p.end()
    return img


def main():
    QGuiApplication(sys.argv)
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    img = draw(256)
    img.save(os.path.join(ROOT, "assets", "app.png"))
    ok = img.save(os.path.join(ROOT, "assets", "app.ico"))
    print("저장:", ok)


if __name__ == "__main__":
    main()
