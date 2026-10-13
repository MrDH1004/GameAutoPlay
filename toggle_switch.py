"""iOS 스타일 토글 스위치. QCheckBox를 상속해서 isChecked()/setChecked()/toggled 등 기존 코드가 그대로 동작한다.

둥근 트랙 안에서 동그란 손잡이가 좌우로 움직이고, 트랙 색이 꺼짐 → 켜짐으로 부드럽게 바뀐다 (0.18초).
라벨 글씨는 QCheckBox가 평소대로 그린다 (폰트·색·비활성 모양이 앱 스타일시트를 그대로 따름).
스위치 부분만 직접 그리고, 기본 체크 표시(indicator)는 같은 크기의 빈 자리로 만들어 라벨과 겹치지 않게 한다.
"""
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox

TRACK_W, TRACK_H, PAD = 44, 24, 3          # 트랙 크기와 손잡이 안쪽 여백
GAP = 10                                    # 스위치와 라벨 사이 간격


class Toggle(QCheckBox):
    def __init__(self, text="", parent=None, on_color="#6c8cff", off_color="#5b6478", knob_color="#ffffff"):
        super().__init__(text, parent)
        self._on, self._off, self._knob = QColor(on_color), QColor(off_color), QColor(knob_color)
        self._progress = 1.0 if self.isChecked() else 0.0
        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.setCursor(Qt.PointingHandCursor)
        # 기본 체크 표시는 투명한 빈 자리로 (스위치 크기만큼 비워 두면 라벨이 그 오른쪽에 그려진다)
        self.setStyleSheet(
            f"QCheckBox {{ spacing: {GAP}px; padding: 4px 0; }}"
            f"QCheckBox::indicator {{ width: {TRACK_W}px; height: {TRACK_H}px; background: transparent; border: none; }}")
        self.toggled.connect(self._animate)

    # ---- 애니메이션 진행값 (0=꺼짐 … 1=켜짐) ----
    def _get_progress(self):
        return self._progress

    def _set_progress(self, v):
        self._progress = float(v)
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked):
        """프로그램이 값을 넣을 때(저장값/프로필 불러오기)는 애니메이션 없이 바로 그 상태로 맞춘다.
        (toggled 시그널은 평소처럼 나간다 — 시그널 안에서 애니메이션이 시작돼도 아래에서 바로 덮어쓴다.)"""
        super().setChecked(checked)
        self._anim.stop()
        self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def hitButton(self, pos):
        return self.rect().contains(pos)                              # 글씨를 눌러도 토글

    def paintEvent(self, event):
        super().paintEvent(event)                                      # 라벨(과 투명한 빈 인디케이터)은 기본대로
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = (self.height() - TRACK_H) / 2
        track = QRectF(0, y, TRACK_W, TRACK_H)

        # 트랙 색: 진행값만큼 꺼짐색 → 켜짐색
        t = self._progress
        c = QColor(int(self._off.red() + (self._on.red() - self._off.red()) * t),
                   int(self._off.green() + (self._on.green() - self._off.green()) * t),
                   int(self._off.blue() + (self._on.blue() - self._off.blue()) * t))
        if not self.isEnabled():
            c.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(track, TRACK_H / 2, TRACK_H / 2)

        # 손잡이: 왼쪽 끝 ↔ 오른쪽 끝
        d = TRACK_H - 2 * PAD
        x = track.left() + PAD + (TRACK_W - 2 * PAD - d) * t
        knob = QRectF(x, track.top() + PAD, d, d)
        p.setBrush(QColor(0, 0, 0, 45))                                # 손잡이 그림자
        p.drawEllipse(knob.translated(0, 1))
        k = QColor(self._knob)
        if not self.isEnabled():
            k.setAlpha(170)
        p.setBrush(k)
        p.drawEllipse(knob)

        # 키보드로 포커스가 왔을 때만 얇은 링 (마우스 클릭 후에는 안 보이게 TabFocus/Backtab 이유일 때만)
        if self.hasFocus() and self.focusReason() in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(self._on), 1.5))
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), TRACK_H / 2 + 2, TRACK_H / 2 + 2)

    def focusReason(self):
        return getattr(self, "_focus_reason", Qt.NoFocusReason)

    def focusInEvent(self, e):
        self._focus_reason = e.reason()
        super().focusInEvent(e)

    def focusOutEvent(self, e):
        self._focus_reason = Qt.NoFocusReason
        super().focusOutEvent(e)
