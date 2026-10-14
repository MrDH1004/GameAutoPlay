"""상단 버튼용 벡터 아이콘. 이모지/기호 글자(▶ ⏸ ■ 🧪 …) 대신 직접 그려서 글꼴·해상도와 상관없이 또렷하게 보인다.

make(name, color, disabled) → QIcon (사용 가능 / 비활성 두 가지 색). 20x20 칸 기준 도형을 4배로 그려 축소한다.
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 20
SCALE = 4


def _draw(name, p, color):
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    if name == "play":
        path = QPainterPath()
        path.moveTo(6, 4)
        path.lineTo(16.5, 10)
        path.lineTo(6, 16)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "pause":
        p.drawRoundedRect(QRectF(5, 4, 3.6, 12), 1, 1)
        p.drawRoundedRect(QRectF(11.4, 4, 3.6, 12), 1, 1)
    elif name == "stop":
        p.drawRoundedRect(QRectF(4.5, 4.5, 11, 11), 2, 2)
    elif name == "potion":                       # 플라스크: 목 + 둥근 몸통 + 액체 줄
        pen = QPen(color, 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(8, 3.5)
        path.lineTo(8, 8)
        path.lineTo(4.5, 15)
        path.quadTo(4, 17, 6, 17)
        path.lineTo(14, 17)
        path.quadTo(16, 17, 15.5, 15)
        path.lineTo(12, 8)
        path.lineTo(12, 3.5)
        p.drawPath(path)
        p.drawLine(QPointF(7, 3.5), QPointF(13, 3.5))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(6, 12.2, 8, 2.2), 1, 1)
    elif name == "swords":                       # 교차한 칼 두 자루
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(4.5, 15.5), QPointF(15, 5))
        p.drawLine(QPointF(15.5, 15.5), QPointF(5, 5))
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(3.5, 12.5), QPointF(7, 16))        # 손잡이 가드
        p.drawLine(QPointF(16.5, 12.5), QPointF(13, 16))
    elif name == "evade":                        # 빠르게 빠져나가는 화살표 (>>)
        pen = QPen(color, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for dx in (0, 6):
            path = QPainterPath()
            path.moveTo(4 + dx, 5)
            path.lineTo(9 + dx, 10)
            path.lineTo(4 + dx, 15)
            p.drawPath(path)
    elif name == "mouse":                        # 마우스: 몸통 + 가운데 선
        pen = QPen(color, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(5.5, 2.8, 9, 14.4), 4.5, 4.5)
        p.drawLine(QPointF(10, 3), QPointF(10, 9))
        p.drawLine(QPointF(5.5, 9), QPointF(14.5, 9))
    else:
        pen = QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if name == "dashboard":
            for x, y, h in ((3, 3, 6), (11, 3, 9), (3, 11, 6), (11, 14, 3)):
                p.drawRoundedRect(QRectF(x, y, 6, h), 1.3, 1.3)
        elif name == "settings":
            for y, x in ((5, 7), (10, 13), (15, 8)):
                p.drawLine(QPointF(3, y), QPointF(17, y))
                p.setBrush(QColor("#182130"))
                p.drawEllipse(QPointF(x, y), 2, 2)
        elif name == "stats":
            p.drawLine(QPointF(3, 3), QPointF(3, 17))
            p.drawLine(QPointF(3, 17), QPointF(17, 17))
            path = QPainterPath(QPointF(6, 13))
            for x, y in ((10, 9), (13, 11), (17, 5)):
                path.lineTo(x, y)
            p.drawPath(path)
        elif name == "preview":
            p.drawRoundedRect(QRectF(2.5, 3.5, 15, 10), 2, 2)
            p.drawLine(QPointF(10, 14), QPointF(10, 17))
            p.drawLine(QPointF(6, 17), QPointF(14, 17))
        elif name == "map":
            path = QPainterPath(QPointF(3, 5))
            for x, y in ((7.5, 3), (12.5, 5), (17, 3), (17, 15), (12.5, 17), (7.5, 15), (3, 17), (3, 5)):
                path.lineTo(x, y)
            p.drawPath(path)
            p.drawLine(QPointF(7.5, 3), QPointF(7.5, 15))
            p.drawLine(QPointF(12.5, 5), QPointF(12.5, 17))
        elif name == "book":
            p.drawRoundedRect(QRectF(4, 3, 12, 14), 2, 2)
            p.drawLine(QPointF(7, 3), QPointF(7, 17))
            p.drawLine(QPointF(10, 7), QPointF(13, 7))
            p.drawLine(QPointF(10, 10), QPointF(13, 10))
        elif name == "log":
            p.drawRoundedRect(QRectF(3, 3, 14, 14), 2, 2)
            for y in (6, 10, 14):
                p.drawLine(QPointF(6, y), QPointF(14, y))
        elif name == "save":
            p.drawRoundedRect(QRectF(3, 3, 14, 14), 2, 2)
            p.drawRect(QRectF(7, 3, 6, 5))
            p.drawRoundedRect(QRectF(6, 11, 8, 6), 1, 1)
        elif name == "brand":
            path = QPainterPath(QPointF(4, 7))
            path.lineTo(3, 2)
            path.lineTo(8, 5)
            path.quadTo(10, 4, 12, 5)
            path.lineTo(17, 2)
            path.lineTo(16, 7)
            path.cubicTo(20, 19, 0, 19, 4, 7)
            p.drawPath(path)
            p.drawLine(QPointF(6.5, 9), QPointF(8, 10))
            p.drawLine(QPointF(13.5, 9), QPointF(12, 10))
            p.drawArc(QRectF(7, 10, 6, 4), 180 * 16, 180 * 16)


def make(name, color="#e6e9f0", disabled="#5b6478"):
    icon = QIcon()
    for mode, col in ((QIcon.Normal, color), (QIcon.Disabled, disabled)):
        pm = QPixmap(SIZE * SCALE, SIZE * SCALE)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(SCALE, SCALE)
        _draw(name, p, QColor(col))
        p.end()
        icon.addPixmap(pm, mode)
    return icon


ICON_SIZE = QSize(18, 18)
