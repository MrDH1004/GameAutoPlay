"""벡터 도깨비로 리뉴얼 앱 아이콘을 만든다: python -m tools.theme_icon."""
from pathlib import Path
import sys
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen
import icons
from ui_theme import C


def draw(size=512):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    background = QLinearGradient(0, 0, size, size)
    background.setColorAt(0, QColor('#2b405f'))
    background.setColorAt(1, QColor(C['bg']))
    painter.setBrush(background)
    painter.setPen(QPen(QColor('#425d83'), size * .012))
    painter.drawRoundedRect(QRectF(size * .04, size * .04, size * .92, size * .92), size * .22, size * .22)
    painter.translate(size * .16, size * .16)
    painter.scale(size * .68 / icons.SIZE, size * .68 / icons.SIZE)
    icons._draw('brand', painter, QColor('#a6c2ff'))
    painter.end()
    return img


def main():
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    destination = Path(__file__).resolve().parents[1] / 'assets'
    destination.mkdir(exist_ok=True)
    png = destination / 'app.png'
    draw().save(str(png))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    data = []
    entries = []
    offset = 6 + 16 * len(sizes)
    for size in sizes:
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.WriteOnly)
        draw(size).save(buffer, 'PNG')
        buffer.close()
        blob = bytes(payload)
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(blob), offset))
        data.append(blob)
        offset += len(blob)
    (destination / 'app.ico').write_bytes(struct.pack('<HHH', 0, 1, len(sizes)) + b''.join(entries) + b''.join(data))
    print(destination)


if __name__ == '__main__':
    main()
