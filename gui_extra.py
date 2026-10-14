"""제어판 추가 화면: 통계 그래프, 실시간 미리보기, 사냥터 DB 편집, 프로필 저장/불러오기."""
import json
import os
import time
from datetime import datetime

import cv2
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from toggle_switch import Toggle
import icons

from app_paths import ROOT
from storage import load_json, save_json
POWER_LOG = os.path.join(ROOT, "logs", "power.jsonl")
PROFILE_DIR = os.path.join(ROOT, "profiles")


# ---------------------------------------------------------------- 그래프
class LineChart(QWidget):
    """(x=시각, y=값) 꺾은선 그래프. 축 라벨은 최소/최대만."""

    def __init__(self, title, color, colors):
        super().__init__()
        self.title, self.color, self.C = title, QColor(color), colors
        self.points = []
        self.setMinimumHeight(190)

    def set_points(self, pts):
        self.points = pts[-2000:]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.C["panel"]))
        p.drawRoundedRect(r, 12, 12)
        p.setPen(QColor(self.C["text"]))
        f = QFont()
        f.setBold(True)
        p.setFont(f)
        p.drawText(r.adjusted(14, 10, 0, 0), Qt.AlignLeft | Qt.AlignTop, self.title)
        p.setFont(QFont())
        plot = r.adjusted(60, 38, -16, -26)
        if len(self.points) < 2:
            p.setPen(QColor(self.C["sub"]))
            p.drawText(plot, Qt.AlignCenter, "데이터 수집 중 (매크로가 돌면 5분마다 기록)")
            return
        xs = [a for a, _ in self.points]
        ys = [b for _, b in self.points]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        if y1 == y0:
            y0, y1 = y0 * 0.98, y1 * 1.02 + 1
        if x1 == x0:
            x1 = x0 + 1
        p.setPen(QPen(QColor(self.C["line"]), 1))
        for i in range(5):
            y = plot.top() + plot.height() * i / 4
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        p.setPen(QColor(self.C["sub"]))
        p.drawText(QRectF(r.left(), plot.top() - 8, 56, 16), Qt.AlignRight, f"{y1:,.0f}")
        p.drawText(QRectF(r.left(), plot.bottom() - 8, 56, 16), Qt.AlignRight, f"{y0:,.0f}")
        p.drawText(QRectF(plot.left(), plot.bottom() + 4, 200, 16), Qt.AlignLeft,
                   datetime.fromtimestamp(x0).strftime("%m/%d %H:%M"))
        p.drawText(QRectF(plot.right() - 200, plot.bottom() + 4, 200, 16), Qt.AlignRight,
                   datetime.fromtimestamp(x1).strftime("%m/%d %H:%M"))
        pts = [QPointF(plot.left() + (a - x0) / (x1 - x0) * plot.width(),
                       plot.bottom() - (b - y0) / (y1 - y0) * plot.height()) for a, b in self.points]
        p.setPen(QPen(self.color, 2.2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setBrush(self.color)
        p.drawEllipse(pts[-1], 3.5, 3.5)


def record_power(power):
    os.makedirs(os.path.dirname(POWER_LOG), exist_ok=True)
    with open(POWER_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "power": power}) + "\n")


def load_power(limit=5000):
    if not os.path.exists(POWER_LOG):
        return []
    out = []
    with open(POWER_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
                out.append((d["t"], d["power"]))
            except (ValueError, KeyError):
                pass
    return out[-limit:]


class StatsTab(QWidget):
    def __init__(self, colors):
        super().__init__()
        self.C = colors
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        self.power_chart = LineChart("전투력 추이", colors["accent"], colors)
        lay.addWidget(self.power_chart)
        row = QHBoxLayout()
        g1 = QGroupBox("퀘스트별 사망")
        self.by_source = QLabel("-")
        self.by_source.setWordWrap(True)
        QVBoxLayout(g1).addWidget(self.by_source)
        row.addWidget(g1, 1)
        g2 = QGroupBox("최근 사망 기록")
        self.death_table = QTableWidget(0, 3)
        self.death_table.setHorizontalHeaderLabels(["시각", "퀘스트", "전투력"])
        self.death_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.death_table.verticalHeader().setVisible(False)
        self.death_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.death_table.setAlternatingRowColors(True)
        QVBoxLayout(g2).addWidget(self.death_table)
        row.addWidget(g2, 2)
        lay.addLayout(row, 1)
        self.refresh()

    def refresh(self):
        from bot2.safety import load_history
        self.power_chart.set_points(load_power())
        hist = load_history(200)
        counts = {}
        for d in hist:
            counts[d.get("source", "?")] = counts.get(d.get("source", "?"), 0) + 1
        self.by_source.setText("\n".join(f"{k}: {v}회" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
                               or "아직 사망 기록 없음")
        rows = list(reversed(hist[-50:]))
        self.death_table.setRowCount(len(rows))
        for i, d in enumerate(rows):
            for j, v in enumerate((d.get("time", "").replace("T", " "), d.get("source", ""),
                                   f"{d['power']:,}" if d.get("power") else "?")):
                self.death_table.setItem(i, j, QTableWidgetItem(v))


# ---------------------------------------------------------------- 실시간 미리보기
class PreviewTab(QWidget):
    """게임 화면 썸네일 + 인식 결과(상태, 전투/이동/자동사냥 여부)."""

    def __init__(self, colors, get_bot):
        super().__init__()
        self.C = colors
        self.get_bot = get_bot
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        top = QHBoxLayout()
        self.chk = Toggle("실시간 미리보기", on_color=colors['accent'], off_color=colors['idle'])
        self.chk.setToolTip("이 화면이 보일 때만 2초마다 캡처합니다. 게임 입력은 보내지 않습니다.")
        top.addWidget(self.chk)
        top.addStretch(1)
        self.info = QLabel("")
        self.info.setObjectName("sub")
        top.addWidget(self.info)
        lay.addLayout(top)
        self.view = QLabel("미리보기가 꺼져 있습니다")
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumHeight(360)
        self.view.setStyleSheet(f"background:{colors['panel']}; border-radius:12px; color:{colors['sub']};")
        lay.addWidget(self.view, 1)
        self._win = None
        self._eyes = None
        self._last = 0.0

    def tick(self, visible):
        if not (visible and self.chk.isChecked()) or time.monotonic() - self._last < 2.0:
            return
        self._last = time.monotonic()
        bot = self.get_bot()
        try:
            if bot:
                win, eyes = bot.window, bot.eyes
            else:
                if self._win is None:
                    import yaml
                    from bot.vision import Matcher
                    from bot.window import GameWindow
                    from bot2.perception import Perceiver
                    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
                    self._win = GameWindow(cfg["window_title"], cfg.get("process_name"))
                    self._eyes = Perceiver(Matcher(os.path.join(ROOT, "templates"), cfg.get("match_threshold", 0.85)), cfg)
                win, eyes = self._win, self._eyes
            if not win.locate():
                self.view.setText("게임 창을 찾지 못함")
                return
            frame = win.grab()
            snap = eyes.look(frame, time.monotonic())
        except Exception as e:  # noqa: BLE001
            self.view.setText(f"캡처 실패: {e}")
            return
        small = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
        color = {"COMBAT": (91, 91, 239), "MOVING": (255, 140, 108), "DIALOG": (66, 185, 245),
                 "DEAD": (60, 60, 200), "OVERLAY": (160, 160, 160)}.get(snap.state, (142, 207, 62))
        cv2.rectangle(small, (0, 0), (959, 539), color, 4)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
        pm = QPixmap.fromImage(img).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.view.setPixmap(pm)
        self.info.setText(f"상태 {snap.state} · 전투 {'O' if snap.combat else 'X'} · 이동 {'O' if snap.auto_moving else 'X'}"
                          f" · 자동사냥 {'켜짐' if snap.auto_on else '꺼짐'} · {datetime.now():%H:%M:%S}")


# ---------------------------------------------------------------- 사냥터 DB 편집
class SpotEditor(QWidget):
    COLS = ["사용", "ID", "지역", "이름", "몬스터 전투력", "읽은 값"]

    def __init__(self, colors):
        super().__init__()
        from bot2 import farm
        self.farm = farm
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        hint = QLabel("세계지도에서 읽은 사냥터 목록입니다. 이름/전투력을 고치거나 '사용'을 끄면 사냥터 선택에서 빠집니다. "
                      "전투력이 비어 있는 곳은 선택되지 않습니다.")
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b1 = QPushButton("다시 불러오기")
        b1.setIcon(icons.make("map", colors['accent']))
        b1.clicked.connect(self.load)
        b2 = QPushButton("저장")
        b2.setIcon(icons.make("save", colors['accent']))
        b2.setObjectName("primary")
        b2.clicked.connect(self.save)
        self.msg = QLabel("")
        self.msg.setObjectName("sub")
        row.addWidget(self.msg, 1)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        self.load()

    def load(self):
        self.db = self.farm.load_spots()
        spots = self.db["spots"]
        self.table.setRowCount(len(spots))
        for i, s in enumerate(spots):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked if s.get("disabled") else Qt.Checked)
            self.table.setItem(i, 0, chk)
            for j, v in enumerate((s["id"], s["region"]), 1):
                it = QTableWidgetItem(v)
                it.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(i, j, it)
            self.table.setItem(i, 3, QTableWidgetItem(s.get("name") or ""))
            self.table.setItem(i, 4, QTableWidgetItem(str(s["power"]) if s.get("power") else ""))
            raw = QTableWidgetItem(", ".join(f"{v // 1000}K" for v in s.get("powers", [])))
            raw.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(i, 5, raw)
        self.msg.setText(f"{len(spots)}곳")

    def save(self):
        for i, s in enumerate(self.db["spots"]):
            s["disabled"] = self.table.item(i, 0).checkState() != Qt.Checked
            s["name"] = self.table.item(i, 3).text().strip()
            txt = self.table.item(i, 4).text().replace(",", "").strip()
            if not txt:
                s["power"] = None
            else:
                try:
                    s["power"] = int(float(txt.upper().rstrip("K")) * (1000 if txt.upper().endswith("K") else 1))
                except ValueError:
                    QMessageBox.warning(self, "저장 실패", f"{s['id']}의 전투력 '{txt}'을(를) 숫자로 읽을 수 없습니다.")
                    return
        self.farm.save_spots(self.db)
        self.msg.setText(f"저장함 ({datetime.now():%H:%M:%S}) · 다음 시작부터 적용")


# ---------------------------------------------------------------- 프로필
def list_profiles():
    if not os.path.isdir(PROFILE_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(PROFILE_DIR) if f.endswith(".json"))


def save_profile(name, data):
    if not name.strip() or any(char in name for char in '\\/:*?"<>|') or name in ('.', '..'):
        raise ValueError("프로필 이름에 경로 또는 파일 이름 금지 문자를 사용할 수 없습니다")
    os.makedirs(PROFILE_DIR, exist_ok=True)
    save_json(os.path.join(PROFILE_DIR, name + ".json"), data)


def load_profile(name):
    if any(char in name for char in '\\/:*?"<>|') or name in ('.', '..'):
        raise ValueError("올바른 프로필을 선택하세요")
    data, warning = load_json(os.path.join(PROFILE_DIR, name + ".json"))
    if warning or not data:
        raise ValueError("프로필을 읽지 못했습니다. 설정 백업에서 복원하세요")
    return data


class ProfileBar(QGroupBox):
    """설정 묶음 저장/불러오기. on_collect() → dict, on_apply(dict)."""

    def __init__(self, on_collect, on_apply):
        super().__init__("프로필 (설정 묶음)")
        self.on_collect, self.on_apply = on_collect, on_apply
        row = QHBoxLayout(self)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(200)
        row.addWidget(self.combo)
        for text, fn in (("불러오기", self._load), ("현재 설정 저장", self._save), ("삭제", self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        self._ensure_defaults()
        self.reload()

    def _ensure_defaults(self):
        if list_profiles():
            return
        save_profile("퀘스트용", {"farm/enabled": False, "farm/fallback_when_blocked": True, "mission/enabled": True,
                                 "yeonggi/enabled": True})
        save_profile("앵벌용", {"farm/enabled": True, "mission/enabled": False, "yeonggi/enabled": True})
        save_profile("밤샘용", {"farm/enabled": False, "farm/fallback_when_blocked": True, "mission/enabled": True,
                                "yeonggi/enabled": True, "death/streak_limit": 2, "user_idle_resume": 30})

    def reload(self):
        self.combo.clear()
        self.combo.addItems(list_profiles())

    def _load(self):
        name = self.combo.currentText()
        if name:
            try:
                self.on_apply(load_profile(name))
            except (OSError, ValueError, TypeError) as error:
                QMessageBox.warning(self, "프로필 불러오기 실패", str(error))

    def _save(self):
        name, ok = QInputDialog.getText(self, "프로필 저장", "프로필 이름:", text=self.combo.currentText())
        if ok and name.strip():
            try:
                save_profile(name.strip(), self.on_collect())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "프로필 저장 실패", str(error))
                return
            self.reload()
            self.combo.setCurrentText(name.strip())

    def _delete(self):
        name = self.combo.currentText()
        if name and QMessageBox.question(self, "삭제", f"'{name}' 프로필을 삭제할까요?") == QMessageBox.Yes:
            os.remove(os.path.join(PROFILE_DIR, name + ".json"))
            self.reload()
