"""추천 탭의 보물찾기 지도·수동 체크리스트 위젯."""

import os

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from app_paths import ROOT
from storage import load_json, save_json
from treasure_guide import INTERACTIVE_MAP, REGIONS, SOURCE_PAGE, progress_summary, recommended_region


PROGRESS_PATH = os.path.join(ROOT, "treasure_progress.json")
MAP_DIR = os.path.join(ROOT, "assets", "treasure")


class TreasureGuideWidget(QWidget):
    def __init__(self, colors, initial_power=0):
        super().__init__()
        self.C = colors
        self.progress, _warning = load_json(PROGRESS_PATH)
        from treasure_guide import normalize_progress
        self.progress = normalize_progress(self.progress)
        self.checks = []
        self.zoom = 0.52
        self.original = QPixmap()
        self._building_checks = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(8)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("내 전투력"))
        self.power = QSpinBox()
        self.power.setRange(0, 10_000_000)
        self.power.setSingleStep(1000)
        self.power.setGroupSeparatorShown(True)
        self.power.setValue(int(initial_power or 0))
        self.power.valueChanged.connect(self._refresh_region_labels)
        bar.addWidget(self.power)
        self.region = QComboBox()
        self.region.setMinimumWidth(260)
        self.region.currentIndexChanged.connect(self._select_region)
        bar.addWidget(self.region, 1)
        recommended = QPushButton("가능한 미완료 지역")
        recommended.clicked.connect(self._select_recommended)
        bar.addWidget(recommended)
        root.addLayout(bar)

        status = QHBoxLayout()
        self.total_text = QLabel()
        self.total_text.setStyleSheet("font-weight:700")
        status.addWidget(self.total_text)
        self.total_bar = QProgressBar()
        self.total_bar.setRange(0, 91)
        self.total_bar.setTextVisible(False)
        self.total_bar.setMinimumWidth(160)
        status.addWidget(self.total_bar)
        status.addStretch(1)
        for text, slot in (("축소", self._zoom_out), ("화면 맞춤", self._fit), ("확대", self._zoom_in)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            status.addWidget(button)
        root.addLayout(status)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.map_label = QLabel("지도를 불러오는 중…")
        self.map_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.map_label.setStyleSheet(f"background:{colors['panel']};")
        self.map_scroll = QScrollArea()
        self.map_scroll.setWidgetResizable(False)
        self.map_scroll.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.map_scroll.setWidget(self.map_label)
        body.addWidget(self.map_scroll, 3)

        side = QWidget()
        side.setMinimumWidth(255)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(8, 6, 8, 6)
        self.region_title = QLabel()
        self.region_title.setStyleSheet("font-size:16px;font-weight:700")
        self.region_meta = QLabel()
        self.region_meta.setWordWrap(True)
        self.region_meta.setStyleSheet(f"color:{colors['sub']}")
        side_lay.addWidget(self.region_title)
        side_lay.addWidget(self.region_meta)
        side_lay.addWidget(QLabel("찾은 번호를 직접 체크하세요"))
        self.check_grid = QGridLayout()
        self.check_grid.setSpacing(5)
        side_lay.addLayout(self.check_grid)
        side_lay.addStretch(1)
        clear = QPushButton("현재 지역 체크 해제")
        clear.clicked.connect(self._clear_region)
        side_lay.addWidget(clear)
        source = QPushButton("지도 출처 열기")
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SOURCE_PAGE)))
        side_lay.addWidget(source)
        interactive = QPushButton("온라인 전체 지도 열기")
        interactive.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(INTERACTIVE_MAP)))
        side_lay.addWidget(interactive)
        note = QLabel("비공식 팬 지도입니다. 위치는 근사값이며 게임의 보물찾기 창을 최종 기준으로 사용하세요. 헬퍼는 이동·조사·개봉 입력을 보내지 않습니다.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{colors['sub']};font-size:13px")
        side_lay.addWidget(note)
        body.addWidget(side, 1)
        root.addLayout(body, 1)

        self._refresh_region_labels()
        self._select_recommended()

    def set_power(self, value):
        value = int(value or 0)
        if value and self.power.value() != value:
            self.power.setValue(value)

    def _refresh_region_labels(self):
        current_key = REGIONS[self.region.currentIndex()].key if self.region.count() else None
        self.region.blockSignals(True)
        self.region.clear()
        for item in REGIONS:
            found = len(self.progress.get(item.key, ()))
            access = self.power.value() <= 0 or item.power <= self.power.value()
            state = "완료" if found == item.count else f"{found}/{item.count}"
            lock = "" if access else " · 전투력 부족"
            self.region.addItem(f"{item.province}  |  {item.name}  · {state}{lock}", item.key)
        index = next((i for i, item in enumerate(REGIONS) if item.key == current_key), 0)
        self.region.setCurrentIndex(index)
        self.region.blockSignals(False)
        self._update_total()

    def _select_recommended(self):
        item = recommended_region(self.power.value(), self.progress)
        if item is None:
            return
        index = next(i for i, region in enumerate(REGIONS) if region.key == item.key)
        self.region.setCurrentIndex(index)
        self._select_region(index)

    def _select_region(self, index):
        if not 0 <= index < len(REGIONS):
            return
        item = REGIONS[index]
        self.region_title.setText(item.name)
        need = "제한 없음" if item.power <= 0 else f"{item.power:,} 이상"
        found = len(self.progress.get(item.key, ()))
        self.region_meta.setText(
            f"{item.province}\n조사 전투력 {need}\n일반 {item.normal} · 희귀 {item.rare} · 전설 {item.legendary}\n진행 {found}/{item.count}")
        self.original = QPixmap(os.path.join(MAP_DIR, item.image))
        if self.original.isNull():
            self.map_label.setText("지도 이미지가 없습니다")
        else:
            self._fit()
        self._build_checks(item)

    def _build_checks(self, item):
        self._building_checks = True
        while self.check_grid.count():
            child = self.check_grid.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.checks = []
        found = set(self.progress.get(item.key, ()))
        for number in range(1, item.count + 1):
            check = QCheckBox(str(number))
            check.setChecked(number in found)
            check.toggled.connect(self._save_checks)
            self.check_grid.addWidget(check, (number - 1) // 4, (number - 1) % 4)
            self.checks.append(check)
        self._building_checks = False

    def _save_checks(self):
        if self._building_checks:
            return
        item = REGIONS[self.region.currentIndex()]
        values = [number for number, check in enumerate(self.checks, 1) if check.isChecked()]
        if values:
            self.progress[item.key] = values
        else:
            self.progress.pop(item.key, None)
        save_json(PROGRESS_PATH, self.progress)
        self._refresh_region_labels()
        index = next(i for i, region in enumerate(REGIONS) if region.key == item.key)
        self.region.setCurrentIndex(index)
        self._select_region(index)

    def _clear_region(self):
        if not self.region.count():
            return
        self._building_checks = True
        for check in self.checks:
            check.setChecked(False)
        self._building_checks = False
        self._save_checks()

    def _update_total(self):
        found, total, done, region_total = progress_summary(self.progress)
        self.total_text.setText(f"전체 {found}/{total} · 지역 완료 {done}/{region_total}")
        self.total_bar.setRange(0, total)
        self.total_bar.setValue(found)

    def _render_map(self):
        if self.original.isNull():
            return
        width = max(320, round(self.original.width() * self.zoom))
        pixmap = self.original.scaledToWidth(width, Qt.SmoothTransformation)
        self.map_label.setPixmap(pixmap)
        self.map_label.resize(pixmap.size())

    def _fit(self):
        if self.original.isNull():
            return
        viewport = self.map_scroll.viewport().width()
        if viewport <= 100:
            QTimer.singleShot(0, self._fit)
            return
        self.zoom = max(0.25, min(1.5, (viewport - 8) / self.original.width()))
        self._render_map()

    def _zoom_in(self):
        self.zoom = min(1.5, self.zoom + 0.1)
        self._render_map()

    def _zoom_out(self):
        self.zoom = max(0.25, self.zoom - 0.1)
        self._render_map()
