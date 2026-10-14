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
