"""둥근 카드 기반의 모드 동작 흐름 시각화 위젯."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from mode_flow import FLOW_PAGES


class FlowDiagram(QScrollArea):
    """QTextBrowser 표 대신 실제 Qt 위젯으로 그리는 라운드 흐름도."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.key = "overview"
        self.active_titles = ()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas = QWidget()
        self.layout = QVBoxLayout(self.canvas)
        self.layout.setContentsMargins(22, 18, 22, 28)
        self.layout.setSpacing(8)
        self.setWidget(self.canvas)
        self.set_flow("overview")

    def set_flow(self, key, active_titles=(), colors=None):
        self.colors = colors or self.colors
        self.key = key if key in FLOW_PAGES else "overview"
        self.active_titles = tuple(active_titles or ())
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        page = FLOW_PAGES[self.key]
        intro = QLabel(page["subtitle"])
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self.colors['sub']};font-size:14px;padding:2px 8px 10px 8px;")
        self.layout.addWidget(intro)
        for index, (kind, title, body) in enumerate(page["blocks"]):
            if kind == "branches":
                self.layout.addWidget(self._branch_group(title, body))
            else:
                self.layout.addWidget(self._centered(self._node(kind, title, body)))
            if index < len(page["blocks"]) - 1:
                self.layout.addWidget(self._arrow())
        self.layout.addWidget(self._priority_card())
        self.layout.addStretch(1)

    def _centered(self, widget):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(widget, 8)
        row.addStretch(1)
        return holder

    def _node(self, kind, title, body):
        c = self.colors
        tone = {"start": c["ok"], "action": c["accent"],
                "decision": c["warn"], "loop": c["sub"]}[kind]
        labels = {"start": "시작", "action": "동작", "decision": "판단", "loop": "반복"}
        active = title in self.active_titles
        frame = QFrame()
        frame.setMinimumWidth(430)
        frame.setMaximumWidth(820)
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        background = (f"qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {c['selected']},stop:1 {c['panel']})"
                      if active else c["panel"])
        frame.setStyleSheet(
            f"QFrame{{background:{background};border:{3 if active else 1}px solid "
            f"{c['ok'] if active else c['line']};border-radius:20px;}}"
            "QLabel{background:transparent;border:none;}"
        )
        self._shadow(frame, active)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 15, 20, 16)
        layout.setSpacing(7)
        top = QHBoxLayout()
        badge = QLabel(("● 현재 실행  ·  " if active else "") + labels[kind])
        badge.setStyleSheet(f"color:{c['text_on_accent'] if active else tone};background:"
                            f"{c['ok'] if active else c['panel2']};border-radius:10px;"
                            "padding:4px 10px;font-size:12px;font-weight:800;")
        top.addWidget(badge, 0, Qt.AlignLeft)
        top.addStretch(1)
        layout.addLayout(top)
        heading = QLabel(title)
        heading.setWordWrap(True)
        heading.setStyleSheet(f"color:{c['text']};font-size:16px;font-weight:800;")
        detail = QLabel(body)
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color:{c['sub']};font-size:13px;line-height:1.45;")
        layout.addWidget(heading)
        layout.addWidget(detail)
        return frame

    def _branch_group(self, title, branches):
        c = self.colors
        group = QWidget()
        outer = QVBoxLayout(group)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(9)
        caption = QLabel("◆  " + title)
        caption.setAlignment(Qt.AlignCenter)
        caption.setStyleSheet(f"color:{c['warn']};font-size:14px;font-weight:800;padding:5px;")
        outer.addWidget(caption)
        row = QHBoxLayout()
        row.setSpacing(10)
        for branch_title, branch_body, branch_color in branches:
            active = branch_title in self.active_titles
            tone = {"daily": c["warn"], "story": c["accent"], "normal": c["accent"], "farm": c["ok"],
                    "quest": c["text"], "warn": c["warn"], "accent": c["accent"],
                    "ok": c["ok"], "bad": c["bad"], "text": c["text"]}[branch_color]
            card = QFrame()
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            background = c["selected"] if active else c["panel"]
            card.setStyleSheet(
                f"QFrame{{background:{background};border:{3 if active else 1}px solid "
                f"{c['ok'] if active else c['line']};border-radius:17px;}}"
                "QLabel{background:transparent;border:none;}"
            )
            self._shadow(card, active)
            box = QVBoxLayout(card)
            box.setContentsMargins(14, 13, 14, 14)
            box.setSpacing(6)
            if active:
                current = QLabel("● 현재")
                current.setStyleSheet(f"color:{c['ok']};font-size:12px;font-weight:800;")
                box.addWidget(current)
            name = QLabel(branch_title)
            name.setWordWrap(True)
            name.setStyleSheet(f"color:{tone};font-size:14px;font-weight:800;")
            desc = QLabel(branch_body)
            desc.setWordWrap(True)
            desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            desc.setStyleSheet(f"color:{c['sub']};font-size:12px;")
            box.addWidget(name)
            box.addWidget(desc, 1)
            row.addWidget(card, 1)
        outer.addLayout(row)
        return group

    def _arrow(self):
        arrow = QLabel("●\n│\n▼")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setStyleSheet(f"color:{self.colors['line']};font-size:12px;font-weight:700;line-height:0.8;")
        arrow.setFixedHeight(42)
        return arrow

    def _priority_card(self):
        c = self.colors
        frame = QFrame()
        frame.setStyleSheet(f"background:{c['panel2']};border:1px solid {c['line']};border-radius:16px;")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 11, 16, 11)
        badge = QLabel("실행 우선순위")
        badge.setStyleSheet(f"color:{c['text_on_accent']};background:{c['accent']};"
                            "border:none;border-radius:9px;padding:4px 9px;font-weight:800;")
        text = QLabel("사용자 조작·사망·저체력  →  전투 콘텐츠  →  선택 모드  →  공통 작업  →  화면 재확인")
        text.setWordWrap(True)
        text.setStyleSheet(f"color:{c['text']};border:none;")
        row.addWidget(badge)
        row.addWidget(text, 1)
        return frame

    def _shadow(self, widget, active=False):
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(28 if active else 18)
        shadow.setOffset(0, 5 if active else 3)
        shadow.setColor(QColor(0, 0, 0, 100 if active else 55))
        widget.setGraphicsEffect(shadow)

    def toPlainText(self):
        """UI 회귀 테스트와 접근성 확인용 텍스트 표현."""
        page = FLOW_PAGES[self.key]
        chunks = [page["title"], page["subtitle"]]
        for kind, title, body in page["blocks"]:
            chunks.append(title)
            if kind == "branches":
                for branch_title, branch_body, _color in body:
                    chunks.extend(("현재 " + branch_title if branch_title in self.active_titles else branch_title,
                                   branch_body))
            else:
                if title in self.active_titles:
                    chunks.append("현재 실행")
                chunks.append(body)
        return "\n".join(chunks)
