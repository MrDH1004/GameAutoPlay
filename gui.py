"""도깨비헬퍼 - 도깨비의세계 자동 진행 매크로 제어판 (PySide6).

    python gui.py

게임이 관리자 권한으로 실행되므로 이 프로그램도 관리자 권한이 필요하다 (아니면 자동으로 다시 실행한다).
"""
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)


def _ensure_admin():
    if ctypes.windll.shell32.IsUserAnAdmin() or getattr(sys, "frozen", False):
        return          # exe는 매니페스트(--uac-admin)로 이미 관리자 권한을 요청한다
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    params = " ".join(f'"{a}"' for a in [os.path.abspath(__file__)] + sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, ROOT, 1)
    sys.exit(0)


if __name__ == "__main__":
    _ensure_admin()

import yaml
from datetime import datetime
from PySide6.QtCore import QObject, QRect, QThread, QTime, QTimer, Qt, Signal
from toggle_switch import Toggle
from PySide6.QtGui import QColor, QIcon, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QLineEdit, QScrollArea,
                               QSystemTrayIcon, QMenu, QTimeEdit, QMessageBox)

CONFIG_PATH = os.path.join(ROOT, "config.yaml")
SETTINGS_PATH = os.path.join(ROOT, "gui_settings.json")
LOG_PATH = os.path.join(ROOT, "logs", "bot2.log")

# ---------------------------------------------------------------- 색상/스타일
C = dict(bg="#14161c", panel="#1c1f27", panel2="#232733", line="#2c3140", text="#e6e9f0", sub="#8a93a6",
         accent="#6c8cff", ok="#3ecf8e", warn="#f5b942", bad="#ef5b5b", idle="#5b6478")

QSS = f"""
* {{ font-family: 'Malgun Gothic','Segoe UI'; font-size: 13px; color: {C['text']}; }}
QMainWindow, QWidget#root {{ background: {C['bg']}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {C['sub']}; padding: 10px 22px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {C['text']}; border-bottom: 2px solid {C['accent']}; }}
QTabBar::tab:hover {{ color: {C['text']}; }}
QFrame#card {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QLabel#cardTitle {{ color: {C['sub']}; font-size: 11px; }}
QLabel#big {{ font-size: 13px; font-weight: 700; }}
QLabel#sub {{ color: {C['sub']}; }}
QGroupBox {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 12px; margin-top: 14px; padding: 18px 14px 12px 14px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 6px; color: {C['sub']}; }}
QPushButton {{ background: {C['panel2']}; border: 1px solid {C['line']}; border-radius: 10px; padding: 10px 18px; font-weight: 600; }}
QPushButton:hover {{ border-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['idle']}; background: {C['panel']}; }}
QPushButton#start {{ background: {C['ok']}; color: #07281a; border: none; }}
QPushButton#start:hover {{ background: #55dca0; }}
QPushButton#start:disabled {{ background: {C['panel2']}; color: {C['idle']}; }}
QPushButton#stop {{ background: {C['bad']}; color: #2b0a0a; border: none; }}
QPushButton#stop:disabled {{ background: {C['panel2']}; color: {C['idle']}; }}
QPushButton[on="true"] {{ background: {C['warn']}; color: #2b1d00; border: none; }}
QPlainTextEdit {{ background: #0f1116; border: 1px solid {C['line']}; border-radius: 10px; padding: 8px;
                  font-family: Consolas,'Malgun Gothic'; font-size: 12px; }}
QSpinBox, QDoubleSpinBox {{ background: {C['panel2']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 5px 8px; min-width: 80px; }}
QCheckBox {{ padding: 4px 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {C['line']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""

STATE_INFO = {   # 내부 상태 → (표시 이름, 설명, 색)
    "DEAD": ("사망", "나가기 / 거점 부활 처리 중", C["bad"]),
    "DIALOG": ("대화", "ESC로 NPC 대화 넘기는 중", C["accent"]),
    "OVERLAY": ("메뉴·로딩", "필드가 아닌 화면 (메뉴/컷신/로딩)", C["warn"]),
    "COMBAT": ("전투", "몬스터와 교전 중", C["bad"]),
    "MOVING": ("자동 이동", "퀘스트 위치로 이동 중", C["ok"]),
    "IDLE": ("대기", "정지 상태 · 퀘스트 클릭 대기", C["sub"]),
}

# 설정창 항목: (키 경로, 라벨, 종류, 범위/단위, 설명)
SETTING_FIELDS = [
    ("toggle", ("farm", "enabled"), "사냥터 전용(앵벌) 모드", "퀘스트를 아예 끄고 내 전투력에 맞는 사냥터에서 자동 사냥만 유지"),
    ("toggle", ("farm", "fallback_when_blocked"), "퀘스트 전부 막히면 안전 사냥터로", "사망으로 모든 퀘스트가 중단되면 전투력에 맞는 사냥터에서 사냥"),
    ("toggle", ("yield_only_game_input",), "게임 창 조작에만 양보", "다른 창에서 하는 작업은 무시하고, 게임을 직접 조작할 때만 멈춤"),
    ("toggle", ("notify", "enabled"), "폰 알림/원격 제어", "사망·일시정지·멈춤을 텔레그램/디스코드로 알림 (아래 토큰 입력 필요)"),
    ("toggle", ("rewards", "enabled"), "보상 자동 받기", "1시간마다 우편함·업적·길라잡이 보상의 '받기' 버튼만 누름 (현재 모두 제외 설정이라 꺼 둠)"),
    ("toggle", ("hp", "enabled"), "HP 낮으면 물약", "체력 45% 아래면 물약 단축키(물약 있는 칸)"),
    ("toggle", ("collect", "enabled"), "앵벌 때 화첩 수집", "앵벌 시간에 못 모은 화첩 조각을 주는 몬스터(내 전투력 2배 이하, 약한 것부터)를 사냥, 1시간마다 조각 등록 (유물은 자동화 안 함)"),
    ("toggle", ("hp", "rest_enabled"), "HP 낮으면 쉬고 회복", "보스가 아닐 때 HP 50% 이하면 잡던 몹까지만 잡고 자동 사냥 끔 → 100% 차면 다시 켬"),
    ("toggle", ("preset", "enabled"), "보스면 도술 프리셋 전환", "보스 체력바가 보이면 2 보스, 끝나면 1 일반 프리셋"),
    ("toggle", ("potion_shop", "enabled"), "물약 자동 구매", "HP 물약 50개 미만이면 하루 1회 자동 구매 (위 버튼으로 수동 구매는 언제든)"),
    ("int", ("potion_shop", "buy_below"), "물약 구매 기준", (1, 249, "개 미만")),
    ("toggle", ("enhance", "enabled"), "도술 계열 강화 (실험)", "재료가 충분하면 파멸·저격 계열 강화 (골드 소모, 강화 후 화면 미검증)"),
    ("toggle", ("yeonggi", "enabled"), "영기 수련 자동 진행", "주기적으로 메뉴를 열어 빨간 점 노드를 수련"),
    ("toggle", ("mission", "enabled"), "임무 화면(J) 폴백", "퀘스트가 막히면 본편→서사→일일→주간 순으로 진행"),
    ("toggle", ("quest_tracker", "scan", "enabled"), "트래커 OCR 스캔", "우측 퀘스트 목록을 스크롤해 진행 가능한 퀘스트 탐색"),
    ("toggle", ("autohunt", "after_move"), "이동 종료 후 자동 사냥(T)", "자동 이동이 끝나면 자동 사냥을 켬"),
    ("int", ("yeonggi", "interval"), "영기 수련 확인 주기", (30, 3600, "초")),
    ("float", ("farm", "margin"), "사냥터 허용 비율 (몬스터/내 전투력)", (0.3, 1.5, "배")),
    ("int", ("farm", "reselect_minutes"), "사냥터 재선택 주기", (1, 240, "분")),
    ("int", ("stall", "minutes"), "멈춤으로 보는 시간", (1, 240, "분")),
    ("int", ("death", "retry_limit"), "사망 후 같은 퀘스트 재도전 횟수", (0, 20, "회")),
    ("int", ("death", "gate_deaths"), "전투력 게이트 사망 횟수", (1, 20, "회")),
    ("int", ("user_idle_resume",), "직접 조작 후 재개 대기", (5, 1800, "초")),
    ("float", ("loop_interval",), "화면 확인 주기", (0.1, 3.0, "초")),
    ("int", ("idle", "seconds"), "정지로 판단하는 시간", (2, 60, "초")),
    ("int", ("death", "streak_limit"), "연속 사망 한도", (1, 20, "회")),
    ("int", ("death", "streak_window"), "연속 사망 판단 구간", (60, 3600, "초")),
    ("int", ("death", "block_seconds"), "사망 퀘스트 중단 시간", (60, 7200, "초")),
]


# 문자열 설정: (키 경로, 라벨, 비밀값 여부)
TEXT_FIELDS = [
    (("notify", "telegram", "token"), "텔레그램 봇 토큰", True),
    (("notify", "telegram", "chat_id"), "텔레그램 chat id", False),
    (("notify", "discord", "webhook"), "디스코드 웹훅 URL", True),
]


def get_path(d, path, default=None):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def set_path(d, path, value):
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------- 로그 → Qt
class QtLogHandler(logging.Handler, QObject):
    message = Signal(int, str)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.message.emit(record.levelno, self.format(record))
        except Exception:
            pass


# ---------------------------------------------------------------- 매크로 실행 스레드
class BotThread(QThread):
    failed = Signal(str)

    def __init__(self, config, dry_run, debug):
        super().__init__()
        self.config, self.dry_run, self.debug = config, dry_run, debug
        self.bot = None

    def run(self):
        try:
            from bot.window import enable_dpi_awareness
            from bot2.brain import Bot
            try:
                enable_dpi_awareness()
            except Exception:
                pass
            self.bot = Bot(self.config, dry_run=self.dry_run)
            self.bot.run()
        except Exception as e:      # 화면에 보여주고 스레드 종료
            from bot2.farm import StopRequested
            if isinstance(e, StopRequested) or isinstance(e.__context__, StopRequested):
                logging.info("정지 요청으로 작업을 중단하고 종료함")       # 정지 버튼은 정상 종료 — 에러로 남기지 않는다
            else:
                logging.exception("매크로 오류")
                self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            self.bot = None


# ---------------------------------------------------------------- 위젯
def card(title, parent=None):
    f = QFrame(parent)
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(4)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    lay.addWidget(t)
    return f, lay


class Pill(QLabel):
    def __init__(self, text="", color=C["idle"]):
        super().__init__(text)
        self.set(text, color)

    def set(self, text, color):
        self.setText(text)
        self.setAlignment(Qt.AlignCenter)
        c = QColor(color)
        rgb = f"{c.red()},{c.green()},{c.blue()}"
        self.setStyleSheet(f"background:rgba({rgb},40); color:{color}; border:1px solid rgba({rgb},110); "
                           f"border-radius:13px; padding:4px 14px; font-weight:700;")


class StateChip(QLabel):
    def __init__(self, key):
        super().__init__(STATE_INFO[key][0])
        self.key = key
        self.setAlignment(Qt.AlignCenter)
        self.on(False)

    def on(self, active):
        color = STATE_INFO[self.key][2]
        if active:
            self.setStyleSheet(f"background:{color}; color:#0c0e13; border-radius:10px; padding:7px 4px; font-weight:700;")
        else:
            self.setStyleSheet(f"background:{C['panel2']}; color:{C['idle']}; border-radius:10px; padding:7px 4px;")


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        from version import VERSION
        self.setWindowTitle(f"도깨비헬퍼 v{VERSION}")
        self.resize(1000, 720)
        self.thread = None
        self.started_at = 0.0
        self.paused_total = 0.0
        self.session_total = 0.0
        self.recent = []
        self.controls = {}

        with open(CONFIG_PATH, encoding="utf-8") as f:
            self.base_config = yaml.safe_load(f)
        self.saved = self._load_saved()
        self._geo_timer = QTimer(self)                  # 창을 옮기거나 크기를 바꾸면 잠시 뒤 저장 (강제 종료돼도 위치가 남게)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.timeout.connect(self._save_geometry_now)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 18, 24, 14)
        outer.setSpacing(12)
        outer.addLayout(self._build_header())

        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard(), "대시보드")
        tabs.addTab(self._build_settings(), "설정")
        from gui_extra import PreviewTab, SpotEditor, StatsTab
        self.stats_tab = StatsTab(C)
        self.preview_tab = PreviewTab(C, lambda: self.thread.bot if self.thread else None)
        tabs.addTab(self.stats_tab, "통계")
        tabs.addTab(self.preview_tab, "미리보기")
        tabs.addTab(SpotEditor(C), "사냥터")
        tabs.addTab(self._build_recommend(), "추천")
        tabs.addTab(self._build_log(), "로그")
        outer.addWidget(tabs, 1)
        self.tabs = tabs
        self._last_power = None
        self._power_rec_at = 0.0
        self._stats_at = 0.0
        self._sched_day = None
        self._build_tray()

        foot = QLabel("단축키  F11 일시정지/재개   ·   F12 종료   ·   직접 조작하면 자동으로 양보")
        foot.setObjectName("sub")
        outer.addWidget(foot)

        self.log_handler = QtLogHandler()
        self.log_handler.message.connect(self._on_log)
        root_log = logging.getLogger()
        root_log.setLevel(logging.INFO)
        root_log.addHandler(self.log_handler)
        if not os.environ.get("DOKKAEBI_TEST"):          # 자동 테스트가 창을 만들 때는 실제 로그 파일에 쓰지 않는다 (테스트 로그가 섞여 실제 상황으로 오해되던 문제)
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            root_log.addHandler(fh)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(500)
        self._set_running(False)

    # ---------- 저장된 설정
    def _load_saved(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        data = {}
        for path, w in self.controls.items():
            data["/".join(path)] = self._value(w)
        data["_debug"] = self.chk_debug.isChecked()
        data["_dry"] = self.chk_dry.isChecked()
        data["_sched_start_on"] = self.chk_sched_start.isChecked()
        data["_sched_start"] = self.time_start.time().toString("HH:mm")
        data["_sched_stop_on"] = self.chk_sched_stop.isChecked()
        data["_sched_stop_h"] = self.spin_stop_h.value()
        data["_last_version"] = self.saved.get("_last_version", "")
        data["_user_detect"] = bool(getattr(self, "user_detect", True))
        g = self.geometry() if not self.isMaximized() else self.normalGeometry()
        data["_geometry"] = [g.x(), g.y(), g.width(), g.height(), self.isMaximized()]      # 창 위치/크기 (다음 실행 때 복원)
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    @staticmethod
    def _value(w):
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return w.value()

    def _apply_values(self, data):
        """프로필 적용: 키 경로 문자열 → 값."""
        for path, w in self.controls.items():
            k = "/".join(path)
            if k not in data:
                continue
            v = data[k]
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QLineEdit):
                w.setText(str(v))
            else:
                w.setValue(v)
        logging.info("프로필 적용 (다음 시작부터)")

    def _collect_values(self):
        return {"/".join(p): self._value(w) for p, w in self.controls.items()
                if not isinstance(w, QLineEdit)}       # 토큰은 프로필에 넣지 않는다

    def _build_config(self):
        cfg = yaml.safe_load(open(CONFIG_PATH, encoding="utf-8"))
        for path, w in self.controls.items():
            set_path(cfg, path, self._value(w))
        cfg["user_detect"] = bool(getattr(self, "user_detect", True))       # 상단 토글 버튼 값
        return cfg

    # ---------- UI 구성
    def _build_header(self):
        row = QHBoxLayout()
        title = QLabel("도깨비헬퍼")
        title.setStyleSheet("font-size:12px; font-weight:800;")
        from version import VERSION
        sub = QLabel(f"도깨비의세계 자동 진행  ·  v{VERSION}")
        sub.setObjectName("sub")
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(title)
        col.addWidget(sub)
        row.addLayout(col)
        row.addStretch(1)
        self.pill = Pill("정지됨")
        row.addWidget(self.pill)
        row.addSpacing(14)
        self.btn_start = QPushButton("▶  시작")
        self.btn_start.setObjectName("start")
        self.btn_start.clicked.connect(self.start)
        self.btn_pause = QPushButton("⏸  일시정지")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop = QPushButton("■  정지")
        self.btn_stop.setObjectName("stop")
        self.btn_stop.clicked.connect(self.stop)
        self.btn_potion = QPushButton("🧪  물약 구매")
        self.btn_potion.setToolTip("타바로 성 잡화 상점으로 가서 2단 체력 회복 물약을 한도까지 구매")
        self.btn_potion.clicked.connect(self.buy_potion)
        self._pending_potion = False
        self.btn_farm = QPushButton("⚔  앵벌 모드 켜기")
        self.btn_farm.setToolTip("퀘스트를 멈추고 전투력에 맞는 사냥터에서 자동 사냥만 합니다 (화첩/유물 수집 몬스터를 먼저 사냥). 다시 누르면 꺼지고 퀘스트를 재개합니다.")
        self.btn_farm.clicked.connect(self.toggle_farm)
        self._pending_farm = False
        self.btn_detect = QPushButton("🖱  조작 감지 켜짐")
        self.btn_detect.setToolTip("켜짐: 마우스·키보드를 쓰면 봇이 2분간 양보합니다.\n꺼짐: 마우스·키보드를 써도 봇이 멈추지 않습니다 (봇의 클릭과 겹칠 수 있으니 이동·등록 중에는 주의).")
        self.btn_detect.clicked.connect(self.toggle_detect)
        self.user_detect = bool(self.saved.get("_user_detect", True))
        self._refresh_detect_button()
        for b in (self.btn_start, self.btn_pause, self.btn_stop, self.btn_potion, self.btn_farm, self.btn_detect):
            b.setMinimumHeight(42)
            row.addWidget(b)
        return row

    def _build_dashboard(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        c1, l1 = card("현재 상태")
        self.lbl_state = QLabel("-")
        self.lbl_state.setObjectName("big")
        self.lbl_state_sub = QLabel("매크로가 정지되어 있습니다")
        self.lbl_state_sub.setObjectName("sub")
        l1.addWidget(self.lbl_state)
        l1.addWidget(self.lbl_state_sub)
        top.addWidget(c1, 3)

        c2, l2 = card("게임 창")
        self.lbl_window = QLabel("-")
        self.lbl_window.setObjectName("big")
        self.lbl_window_sub = QLabel("")
        self.lbl_window_sub.setObjectName("sub")
        l2.addWidget(self.lbl_window)
        l2.addWidget(self.lbl_window_sub)
        top.addWidget(c2, 2)

        c3, l3 = card("진행 중인 퀘스트 출처")
        self.lbl_source = QLabel("-")
        self.lbl_source.setObjectName("big")
        self.lbl_source_sub = QLabel("")
        self.lbl_source_sub.setObjectName("sub")
        l3.addWidget(self.lbl_source)
        l3.addWidget(self.lbl_source_sub)
        top.addWidget(c3, 2)
        lay.addLayout(top)

        # 실시간 상황판: 동기화 · 현재 위치 · 잡는 몹 · 하는 퀘스트
        live = QHBoxLayout()
        live.setSpacing(12)
        self.live_lbl = {}
        for key, title, stretch in (("sync", "동기화", 2), ("map", "현재 위치", 2), ("mob", "잡는 몹", 2),
                                    ("quest", "하는 퀘스트", 3)):
            c, l = card(title)
            v = QLabel("-")
            v.setObjectName("big")
            v.setWordWrap(True)
            v.setMinimumHeight(40)                              # 카드 높이는 그대로 유지 (글씨만 줄임)
            if key in ("map", "mob", "quest"):
                v.setStyleSheet("font-size:11px;")             # 지도·몹·퀘스트 이름은 길어서 더 작게
            sub_l = QLabel("")
            sub_l.setObjectName("sub")
            sub_l.setWordWrap(True)
            l.addWidget(v)
            l.addWidget(sub_l)
            self.live_lbl[key] = (v, sub_l)
            live.addWidget(c, stretch)
        lay.addLayout(live)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chips = {}
        for key in ("IDLE", "MOVING", "COMBAT", "DIALOG", "OVERLAY", "DEAD"):
            self.chips[key] = StateChip(key)
            chips.addWidget(self.chips[key], 1)
        lay.addLayout(chips)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_lbl = {}
        for key, title in (("time", "가동 시간"), ("deaths", "사망 횟수"), ("missions", "임무 화면 진행"),
                           ("yeonggi", "영기 수련 확인")):
            c, l = card(title)
            v = QLabel("0")
            v.setObjectName("big")
            l.addWidget(v)
            self.stat_lbl[key] = v
            stats.addWidget(c, 1)
        lay.addLayout(stats)

        c4, l4 = card("최근 이벤트")
        self.lbl_recent = QLabel("아직 이벤트가 없습니다")
        self.lbl_recent.setTextFormat(Qt.PlainText)
        self.lbl_recent.setStyleSheet(f"color:{C['text']};")
        self.lbl_recent.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        l4.addWidget(self.lbl_recent, 1)
        lay.addWidget(c4, 1)
        return w

    def _build_settings(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        g1 = QGroupBox("기능")
        gl1 = QGridLayout(g1)
        gl1.setVerticalSpacing(8)
        g2 = QGroupBox("수치")
        gl2 = QGridLayout(g2)
        gl2.setVerticalSpacing(8)
        r1 = r2 = 0
        for kind, path, label, extra in SETTING_FIELDS:
            saved = self.saved.get("/".join(path))
            if kind == "toggle":
                cb = Toggle(label, on_color=C["accent"], off_color=C["idle"])
                cb.setChecked(bool(saved if saved is not None else get_path(self.base_config, path, False)))
                hint = QLabel(extra)
                hint.setObjectName("sub")
                gl1.addWidget(cb, r1, 0)
                gl1.addWidget(hint, r1, 1)
                self.controls[path] = cb
                r1 += 1
            else:
                lo, hi, unit = extra
                sp = QSpinBox() if kind == "int" else QDoubleSpinBox()
                sp.setRange(lo, hi)
                if kind == "float":
                    sp.setSingleStep(0.1)
                    sp.setDecimals(1)
                sp.setValue(saved if saved is not None else get_path(self.base_config, path, lo))
                sp.setAlignment(Qt.AlignRight)
                u = QLabel(unit)
                u.setObjectName("sub")
                gl2.addWidget(QLabel(label), r2, 0)
                gl2.addWidget(sp, r2, 1)
                gl2.addWidget(u, r2, 2)
                self.controls[path] = sp
                r2 += 1
        gl1.setColumnStretch(1, 1)
        gl2.setColumnStretch(3, 1)
        from gui_extra import ProfileBar
        lay.addWidget(ProfileBar(self._collect_values, self._apply_values))
        lay.addWidget(g1)
        lay.addWidget(g2)

        gt = QGroupBox("알림 연결 (선택)")
        glt = QGridLayout(gt)
        for i, (path, label, secret) in enumerate(TEXT_FIELDS):
            le = QLineEdit()
            saved = self.saved.get("/".join(path))
            le.setText(str(saved if saved is not None else (get_path(self.base_config, path, "") or "")))
            if secret:
                le.setEchoMode(QLineEdit.PasswordEchoOnEdit)
            glt.addWidget(QLabel(label), i, 0)
            glt.addWidget(le, i, 1)
            self.controls[path] = le
        tip = QLabel("텔레그램: @BotFather로 봇 생성 → 봇에게 아무 말 → api.telegram.org/bot(토큰)/getUpdates 에서 chat id 확인. "
                     "원격 명령: /상태 /화면 /일시정지 /재개 /정지")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        glt.addWidget(tip, len(TEXT_FIELDS), 0, 1, 2)
        lay.addWidget(gt)

        gs = QGroupBox("예약 실행")
        gls = QGridLayout(gs)
        self.chk_sched_start = Toggle("매일 이 시각에 자동 시작", on_color=C["accent"], off_color=C["idle"])
        self.chk_sched_start.setChecked(bool(self.saved.get("_sched_start_on", False)))
        self.time_start = QTimeEdit(QTime.fromString(self.saved.get("_sched_start", "02:00"), "HH:mm"))
        self.time_start.setDisplayFormat("HH:mm")
        self.chk_sched_stop = Toggle("시작 후 이 시간이 지나면 자동 정지", on_color=C["accent"], off_color=C["idle"])
        self.chk_sched_stop.setChecked(bool(self.saved.get("_sched_stop_on", False)))
        self.spin_stop_h = QDoubleSpinBox()
        self.spin_stop_h.setRange(0.5, 48)
        self.spin_stop_h.setDecimals(1)
        self.spin_stop_h.setValue(float(self.saved.get("_sched_stop_h", 6)))
        gls.addWidget(self.chk_sched_start, 0, 0)
        gls.addWidget(self.time_start, 0, 1)
        gls.addWidget(self.chk_sched_stop, 1, 0)
        gls.addWidget(self.spin_stop_h, 1, 1)
        gls.addWidget(QLabel("시간"), 1, 2)
        gls.setColumnStretch(3, 1)
        lay.addWidget(gs)

        g3 = QGroupBox("실행 옵션")
        gl3 = QVBoxLayout(g3)
        self.chk_dry = Toggle("연습 모드 (클릭/키 입력 없이 감지 결과만 로그로 확인)", on_color=C["accent"], off_color=C["idle"])
        self.chk_dry.setChecked(bool(self.saved.get("_dry", False)))
        self.chk_debug = Toggle("디버그 로그 (화면 변화량 등 상세 출력)", on_color=C["accent"], off_color=C["idle"])
        self.chk_debug.setChecked(bool(self.saved.get("_debug", False)))
        gl3.addWidget(self.chk_dry)
        gl3.addWidget(self.chk_debug)
        lay.addWidget(g3)

        from version import CHANGELOG, VERSION
        g4 = QGroupBox(f"버전 정보  ·  v{VERSION}")
        gl4 = QVBoxLayout(g4)
        for ver, date, note in CHANGELOG:
            lb = QLabel(f"v{ver}  ({date})  {note}")
            lb.setObjectName("sub")
            lb.setWordWrap(True)
            gl4.addWidget(lb)
        lay.addWidget(g4)

        note = QLabel("※ 설정은 다음 '시작'부터 적용됩니다. 실행 중 변경하려면 정지 후 다시 시작하세요.")
        note.setObjectName("sub")
        lay.addWidget(note)
        lay.addStretch(1)
        return scroll

    def _build_recommend(self):
        """추천 탭: 서브탭 — 스킬(기존) / 무기 / 방어구 / 투력 공략집."""
        sub_tabs = QTabWidget()
        sub_tabs.setDocumentMode(True)
        sub_tabs.addTab(self._build_skills(), "스킬")
        sub_tabs.addTab(self._build_gear_page("weapon"), "무기")
        sub_tabs.addTab(self._build_gear_page("armor"), "방어구")
        sub_tabs.addTab(self._build_power_guide(), "투력 공략집")
        sub_tabs.addTab(self._build_hunt_page(), "사냥터")
        return sub_tabs

    def _guide_browser(self):
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        return tb

    @staticmethod
    def _tag(text):
        """'[웹] …' / '[실측] …' 앞머리를 색 있는 뱃지로 바꿔 출처를 한눈에 보이게 한다."""
        from html import escape
        for tag, color in (("[실측]", C["ok"]), ("[웹]", C["warn"])):
            if text.startswith(tag):
                return (f"<span style='color:{color};font-weight:600'>{escape(tag)}</span> "
                        f"{escape(text[len(tag):].strip())}")
        return escape(text)

    def _build_gear_page(self, kind):
        """무기 / 방어구 서브탭 (gear_guide.py 데이터). 아이템 이름은 지어내지 않고 확인된 원칙·재료만 보여 준다."""
        import gear_guide as g
        from html import escape
        data = g.WEAPON if kind == "weapon" else g.ARMOR
        title = "무기" if kind == "weapon" else "방어구"
        h = f"<body style='color:{C['text']}'>"
        h += f"<p style='color:{C['sub']}'>{escape(g.CAVEAT)}</p>"
        h += (f"<table width='100%' cellpadding=10 style='background:{C['panel2']}'><tr><td>"
              f"<b style='color:{C['warn']}'>추천 {title} 목록은 없습니다.</b><br>{escape(g.NO_ITEM_LIST)}</td></tr></table>")
        h += f"<h2>{title} 점검 원칙</h2><ul>" + "".join(f"<li>{self._tag(t)}</li>" for t in data["principles"]) + "</ul>"
        if data.get("materials"):
            h += ("<h2>강화 재료와 수급처</h2><table cellpadding=5><tr><th align=left>재료</th>"
                  "<th align=left>수급처</th><th align=left>출처</th></tr>")
            for name, where, src in data["materials"]:
                col = C["ok"] if src == "실측" else C["warn"]
                h += (f"<tr><td>{escape(name)}</td><td>{escape(where)}</td>"
                      f"<td><span style='color:{col}'>[{escape(src)}]</span></td></tr>")
            h += "</table>"
        if data.get("measured"):
            h += "<h2>이 PC에서 확인한 것</h2><ul>" + "".join(f"<li>{self._tag(t)}</li>" for t in data["measured"]) + "</ul>"
        h += "<h2>출처</h2><ul>" + "".join(
            f"<li><a style='color:{C['accent']}' href='{u}'>{escape(t)}</a> <span style='color:{C['sub']}'>— {escape(n)}</span></li>"
            for t, u, n in g.SOURCES) + "</ul></body>"
        tb = self._guide_browser()
        tb.setHtml(h)
        return tb

    def _build_hunt_page(self):
        """사냥터 추천: 내 전투력(자동으로 채움, 직접 바꿔 볼 수도 있음)에 맞는 사냥터 — [사냥터 추천] 버튼."""
        import gear_guide as g
        from gui_extra import load_power
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("내 전투력"))
        self.spin_hunt_power = QSpinBox()
        self.spin_hunt_power.setRange(0, 10_000_000)
        self.spin_hunt_power.setSingleStep(1000)
        self.spin_hunt_power.setGroupSeparatorShown(True)
        hist = load_power(1)
        self.spin_hunt_power.setValue(int(hist[-1][1]) if hist else 0)
        row.addWidget(self.spin_hunt_power)
        btn = QPushButton("🗺  사냥터 추천")
        btn.clicked.connect(self._show_hunt_reco)
        row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.hunt_view = self._guide_browser()
        lay.addWidget(self.hunt_view)
        self._show_hunt_reco()
        return page

    def _show_hunt_reco(self):
        import gear_guide as g
        from html import escape
        power = self.spin_hunt_power.value()
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                margin = float(((yaml.safe_load(f) or {}).get("farm") or {}).get("margin", 0.9))
        except Exception:                                    # noqa: BLE001
            margin = 0.9
        h = f"<body style='color:{C['text']}'>"
        h += f"<p style='color:{C['sub']}'>{escape(g.CAVEAT)}</p>"
        if power <= 0:
            h += "<p>전투력을 아직 읽지 못했습니다. 위 칸에 전투력을 직접 넣고 [사냥터 추천]을 누르세요.</p>"
        else:
            r = g.recommend_hunt(power, margin)

            def fmt(x):
                return f"<b>{escape(x[0])}</b> (권장 {x[1]:,})" if x else "없음 (더 낮은 곳을 직접 확인)"
            h += (f"<table width='100%' cellpadding=10 style='background:{C['panel2']}'><tr><td>"
                  f"전투력 <b>{power:,}</b> 기준<br>"
                  f"<span style='color:{C['ok']}'>도깨비헬퍼(앵벌 모드)가 가는 곳</span>: {fmt(r['macro'])} "
                  f"<span style='color:{C['sub']}'>— 전투력의 {margin * 100:.0f}% 이하</span><br>"
                  f"<span style='color:{C['warn']}'>웹 원칙(권장 전투력을 넘긴 가장 높은 곳)</span>: {fmt(r['web'])}"
                  + ("" if not r["web"] or r["web"][2] else " <span style='color:" + C['sub'] + "'>— 도깨비헬퍼는 이 구간으로 이동하지 못함</span>")
                  + f"<br>다음 목표: {fmt(r['next'])}"
                  + (f" — 전투력 {r['next'][1] - power:,} 더 필요" if r["next"] else "")
                  + "</td></tr></table>")
        h += "<h2>사냥터 목록</h2><table cellpadding=4><tr><th align=left>사냥터</th><th align=right>권장 전투력</th>"              "<th align=left>메모</th></tr>"
        mark = g.recommend_hunt(power, margin) if power > 0 else {"macro": None, "web": None}
        for name, need, movable in g.hunt_ladder():
            tags = ""
            if mark["macro"] and mark["macro"][0] == name:
                tags += f" <span style='color:{C['ok']}'>◀ 도깨비헬퍼</span>"
            if mark["web"] and mark["web"][0] == name and (not mark["macro"] or mark["macro"][0] != name):
                tags += f" <span style='color:{C['warn']}'>◀ 웹 원칙</span>"
            ok = power >= need if power > 0 else False
            style = "" if ok or power <= 0 else f" style='color:{C['sub']}'"
            note = g.HUNT_WEB.get(name) or next((n for nm, _p, n in g.HUNT_WEB_LATE if nm == name), "")
            src = "[실측]" if movable else "[웹]"
            h += (f"<tr{style}><td>{escape(name)}{tags}</td><td align=right>{need:,}</td>"
                  f"<td>{escape(note)} <span style='color:{C['sub']}'>{src}</span></td></tr>")
        h += "</table>"
        h += "<h2>알아둘 점</h2><ul>" + "".join(f"<li>{self._tag(t) if t[:4] in ('[웹]', '[실측]') else escape(t)}</li>" for t in g.HUNT_NOTES) + "</ul>"
        h += "<h2>출처</h2><ul>" + "".join(
            f"<li><a style='color:{C['accent']}' href='{u}'>{escape(t)}</a> <span style='color:{C['sub']}'>— {escape(n)}</span></li>"
            for t, u, n in g.SOURCES) + "</ul></body>"
        self.hunt_view.setHtml(h)

    def _build_power_guide(self):
        """투력(전투력) 공략집: 올리는 순서 + 이 PC 실측 + 사냥터 사다리."""
        import gear_guide as g
        from html import escape
        pg = g.POWER_GUIDE
        h = f"<body style='color:{C['text']}'>"
        h += f"<p style='color:{C['sub']}'>{escape(g.CAVEAT)}</p>"
        h += "<h2>전투력 올리는 순서</h2><table cellpadding=6>"
        for n, title, text, src in pg["steps"]:
            col = C["ok"] if src == "실측" else C["warn"]
            h += (f"<tr><td valign=top style='color:{C['accent']};font-size:18px'><b>{n}</b></td>"
                  f"<td><b>{escape(title)}</b> <span style='color:{col}'>[{escape(src)}]</span><br>"
                  f"<span style='color:{C['sub']}'>{escape(text)}</span></td></tr>")
        h += "</table>"
        h += "<h2>이 PC에서 확인한 것</h2><ul>" + "".join(f"<li>{self._tag(t)}</li>" for t in pg["measured"]) + "</ul>"
        ladder = g.ladder()
        if ladder:
            h += (f"<h2>사냥터 사다리 <span style='color:{C['ok']};font-size:13px'>[실측]</span></h2>"
                  f"<p style='color:{C['sub']}'>길라잡이(F5) '사냥터 바로가기'는 무료 순간이동입니다. 권장 전투력 = 그 사냥터 몬스터 전투력. "
                  "도깨비헬퍼는 내 전투력의 90% 이하 중 가장 센 곳을 고릅니다.</p>"
                  "<table cellpadding=4><tr><th align=left>사냥터</th><th align=right>권장 전투력</th></tr>")
            for name, power in ladder:
                h += f"<tr><td>{escape(name)}</td><td align=right>{power:,}</td></tr>"
            h += "</table>"
        h += "<h2>출처</h2><ul>" + "".join(
            f"<li><a style='color:{C['accent']}' href='{u}'>{escape(t)}</a> <span style='color:{C['sub']}'>— {escape(n)}</span></li>"
            for t, u, n in g.SOURCES) + "</ul></body>"
        tb = self._guide_browser()
        tb.setHtml(h)
        return tb

    def _build_skills(self):
        """무과금 도술 추천 (skill_guide.py 데이터)."""
        import skill_guide as g
        from html import escape
        import json
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        sdir = os.path.join(ROOT, "assets", "skills")
        try:
            with open(os.path.join(sdir, "index.json"), encoding="utf-8") as f:
                icons = json.load(f)
        except OSError:
            icons = {}

        def icon_html(name, size=40):
            for k, fn in icons.items():
                if name.startswith(k):
                    url = "file:///" + os.path.join(sdir, fn).replace("\\", "/")
                    return f"<img src='{url}' width={size} height={size}>"
            return (f"<table cellspacing=0><tr><td width={size} height={size} align=center "
                    f"style='background:{C['panel2']};color:{C['idle']}'>?</td></tr></table>")

        def skill_row(names):
            cells = "".join(
                f"<td align=center style='padding:4px'>{icon_html(n)}<br><span style='font-size:11px'>{escape(n)}</span></td>"
                for n in names)
            return f"<table cellspacing=2><tr>{cells}</tr></table>"

        h = f"<body style='color:{C['text']}'>"
        h += f"<p style='color:{C['sub']}'>{escape(g.CAVEAT)}</p>"
        h += "<h2>용도별 추천 세트</h2>"
        for st in g.SETS:
            h += (f"<h3>{escape(st['title'])} <span style='color:{C['sub']}'>[{escape(st['tag'])}]</span></h3>"
                  f"{skill_row(st['skills'])}<p>궁극기: {escape(st['ultimate'])}<br>"
                  f"<span style='color:{C['sub']}'>{escape(st['why'])} (출처: {escape(st['src'])})</span></p>")
        h += "<h2>무과금 팁</h2><ul>" + "".join(f"<li>{escape(t)}</li>" for t in g.TIPS) + "</ul>"
        h += "<h2>12계열 요약</h2><table cellpadding=5 border=0><tr><th align=left>계열</th><th align=left>분류</th><th align=left>궁극기</th><th align=left>용도</th><th align=left>특징</th></tr>"
        for f in g.FAMILIES:
            h += "<tr>" + "".join(f"<td>{escape(x)}</td>" for x in f) + "</tr>"
        h += "</table><h2>실측 스킬 효율 (피해% ÷ 쿨타임)</h2><table cellpadding=5><tr><th align=left>도술</th><th align=left>피해%</th><th align=left>쿨(초)</th><th align=left>초당</th></tr>"
        for n, d, cd, dps in sorted(g.DPS_TABLE, key=lambda r: -r[3]):
            h += f"<tr><td>{icon_html(n, 24)} {escape(n)}</td><td>{escape(d)}</td><td>{cd}</td><td>{dps:.1f}</td></tr>"
        h += "</table><h2>출처</h2><ul>" + "".join(
            f"<li><a style='color:{C['accent']}' href='{u}'>{escape(t)}</a> <span style='color:{C['sub']}'>— {escape(n)}</span></li>"
            for t, u, n in g.SOURCES) + "</ul></body>"
        tb.setHtml(h)
        return tb

    def _build_log(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        bar = QHBoxLayout()
        self.chk_warn = Toggle("경고/오류만 보기", on_color=C["accent"], off_color=C["idle"])
        self.chk_warn.toggled.connect(self._refilter)
        bar.addWidget(self.chk_warn)
        bar.addStretch(1)
        b1 = QPushButton("로그 지우기")
        b1.clicked.connect(self._clear_log)
        b2 = QPushButton("스크린샷 폴더")
        b2.clicked.connect(lambda: os.startfile(os.path.join(ROOT, "screenshots")))
        b3 = QPushButton("로그 파일 열기")
        b3.clicked.connect(lambda: os.startfile(LOG_PATH))
        for b in (b1, b2, b3):
            bar.addWidget(b)
        lay.addLayout(bar)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        lay.addWidget(self.log_view, 1)
        self.log_lines = []
        return w

    # ---------- 로그
    def _color_for(self, level):
        return C["bad"] if level >= logging.ERROR else C["warn"] if level >= logging.WARNING else C["text"]

    def _append(self, level, text):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._color_for(level)))
        cur = self.log_view.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text + "\n", fmt)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_log(self, level, text):
        self.log_lines.append((level, text))
        del self.log_lines[:-3000]
        if not self.chk_warn.isChecked() or level >= logging.WARNING:
            self._append(level, text)
        self.recent.append((level, text))
        del self.recent[:-9]

    def _refilter(self):
        self.log_view.clear()
        for level, text in self.log_lines:
            if not self.chk_warn.isChecked() or level >= logging.WARNING:
                self._append(level, text)

    def _clear_log(self):
        self.log_lines.clear()
        self.log_view.clear()

    # ---------- 실행 제어
    def start(self):
        if self.thread:
            return
        self._save()
        cfg = self._build_config()
        logging.getLogger().setLevel(logging.DEBUG if self.chk_debug.isChecked() else logging.INFO)
        self.thread = BotThread(cfg, self.chk_dry.isChecked(), self.chk_debug.isChecked())
        self.thread.failed.connect(lambda m: self.pill.set("오류: " + m[:40], C["bad"]))
        self.thread.finished.connect(self._on_finished)
        self.thread.start()
        self.started_at = time.monotonic()
        self.session_total = 0.0
        self._set_running(True)

    def buy_potion(self):
        """물약 구매 요청. 실행 중이면 다음 틱에, 정지 상태면 시작한 뒤 바로 구매."""
        bot = self.thread.bot if self.thread else None
        if bot and getattr(bot, "potion_shop", None):
            bot.potion_shop.requested = True
            if bot.paused:
                self.toggle_pause()
            logging.info("물약 구매 요청 (버튼)")
        else:
            self._pending_potion = True
            logging.info("물약 구매 요청 (버튼) → 매크로 시작")
            self.start()

    def toggle_detect(self):
        """사용자 조작 감지 켜기/끄기 (실행 중이면 바로 적용, 정지 상태면 다음 시작부터)."""
        self.user_detect = not self.user_detect
        bot = self.thread.bot if self.thread else None
        if bot:
            bot.set_user_detect(self.user_detect)
        else:
            logging.info("사용자 조작 감지 %s (다음 시작부터 적용)", "켬" if self.user_detect else "끔")
        self._save()
        self._refresh_detect_button()

    def _refresh_detect_button(self):
        on = self.user_detect
        self.btn_detect.setText("🖱  조작 감지 켜짐" if on else "🖱  조작 감지 꺼짐")
        self.btn_detect.setProperty("on", not on)                 # 꺼진 상태를 노란색으로 눈에 띄게
        self.btn_detect.style().unpolish(self.btn_detect)
        self.btn_detect.style().polish(self.btn_detect)

    def toggle_farm(self):
        """앵벌 모드 수동 켜기/끄기. 정지 상태면 매크로를 시작하면서 앵벌 모드로."""
        bot = self.thread.bot if self.thread else None
        if bot:
            pending = bot._manual_farm_request          # 아직 봇이 처리 못 한 요청이 있으면 그것을 기준으로 토글
            on = not (pending if pending is not None else bot.manual_farm)
            bot.request_manual_farm(on)
            if on and bot.paused:
                self.toggle_pause()
            logging.info("앵벌 모드 %s 요청 (버튼)", "켜기" if on else "끄기")
        elif self.thread:
            return                                  # 시작 중
        else:
            self._pending_farm = True
            logging.info("앵벌 모드 켜기 요청 (버튼) → 매크로 시작")
            self.start()

    def stop(self):
        if self.thread and self.thread.bot:
            self.thread.bot.stop_event.set()
            self.btn_stop.setEnabled(False)
            self.btn_pause.setEnabled(False)

    def toggle_pause(self):
        bot = self.thread.bot if self.thread else None
        if not bot:
            return
        if bot.paused:
            bot.activity.ignore_until_now()
            bot.waiting_user = False
            bot._idle_since = bot._verify = None
            bot.alert_reason = ""
            bot.paused = False
            logging.info("재개 (버튼)")
        else:
            bot.paused = True
            logging.info("일시정지 (버튼)")

    def _on_finished(self):
        if self.thread:
            self.session_total = time.monotonic() - self.started_at
        self.thread = None
        self._set_running(False)

    def _set_running(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        for w in self.controls.values():
            w.setEnabled(not running)
        self.chk_dry.setEnabled(not running)
        self.chk_debug.setEnabled(not running)
        if not running:
            self.pill.set("정지됨", C["idle"])
            self.btn_pause.setText("⏸  일시정지")

    # ---------- 주기 갱신
    def _refresh(self):
        th = self.thread
        bot = th.bot if th else None
        self._refresh_extra(bot)
        if self._pending_potion and bot and getattr(bot, "potion_shop", None):
            bot.potion_shop.requested = True
            self._pending_potion = False
        if self._pending_farm and bot:
            bot.request_manual_farm(True)
            self._pending_farm = False
        pending = bot._manual_farm_request if bot else None
        on = bool(bot and (pending if pending is not None else bot.manual_farm))
        self.btn_farm.setText("⚔  앵벌 모드 끄기" if on else "⚔  앵벌 모드 켜기")
        self.btn_farm.setProperty("on", on)
        self.btn_farm.style().unpolish(self.btn_farm)
        self.btn_farm.style().polish(self.btn_farm)
        if not th:
            self.lbl_state.setText("정지됨")
            self.lbl_state.setStyleSheet(f"color:{C['idle']};")
            self.lbl_state_sub.setText("▶ 시작을 누르면 게임 화면을 보고 자동으로 진행합니다")
            for c in self.chips.values():
                c.on(False)
            self.stat_lbl["time"].setText(fmt_time(self.session_total))
        elif bot is None:
            self.pill.set("준비 중…", C["warn"])
        else:
            self._refresh_running(bot)
        self.lbl_recent.setText("\n".join(t for _, t in self.recent[-9:]) or "아직 이벤트가 없습니다")

    def _refresh_live(self, bot):
        """실시간 상황판 갱신 (봇의 LiveStatus가 읽어 둔 값을 그대로 보여 줌 — 여기서는 OCR을 안 한다)."""
        live = getattr(bot, "live", None)
        if live is None:
            return
        d, sy = live.data, live.sync()

        def put(key, big, small="", color=None):
            v, sm = self.live_lbl[key]
            v.setText(big)
            size = "font-size:11px;" if key in ("map", "mob", "quest") else ""
            v.setStyleSheet(size + (f"color:{color};" if color else ""))
            sm.setText(small)

        rate, age, why = sy["rate"], sy["age"], sy["why"]
        if why:
            put("sync", "대기 중", why, C["warn"])
        elif rate is None:
            put("sync", "확인 중", "", None)
        else:
            col = C["ok"] if rate >= 0.9 else C["warn"] if rate >= 0.6 else C["bad"]
            put("sync", f"{rate * 100:.0f}%", f"최근 화면 인식률 · 마지막 인식 {age:.0f}초 전" if age is not None else "화면 인식률", col)

        if not d or d.get("at") is None or time.monotonic() - d["at"] > 20:
            for k in ("map", "mob", "quest"):
                put(k, "-", "읽는 중…" if k == "map" else "")
            return
        if not d.get("field"):
            put("map", "필드 아님", "메뉴·던전 화면·컷신 중일 수 있음", C["warn"])
        else:
            put("map", d.get("map") or "?", f"HP {d['hp'] * 100:.0f}%" if d.get("hp") is not None else "")
        t = d.get("target")
        if t:
            pw = f"{t['power'] // 1000}K" if t.get("power") else ""
            put("mob", t["name"], f"{pw} · 체력 {t['fill'] * 100:.0f}%".strip(" ·"))
        else:
            put("mob", "없음", "타겟 몹이 없음")
        put("quest", d.get("quest") or "-", getattr(bot, "_source", "") and f"출처: {bot._source}" or "")

    def _refresh_running(self, bot):
        self._refresh_live(bot)
        self.stat_lbl["time"].setText(fmt_time(time.monotonic() - self.started_at))
        for k in ("deaths", "missions", "yeonggi"):
            self.stat_lbl[k].setText(str(bot.stats.get(k, 0)))

        win_ok = bool(getattr(bot.window, "hwnd", None)) and bot.window.width
        if win_ok:
            w, h = bot.window.width, bot.window.height
            mode = "풀스크린" if w >= 1900 else "창 모드"
            self.lbl_window.setText(f"{w} × {h}")
            sub = f"{mode} · 배율 {w / 1920:.2f}"
            st = getattr(bot, "startup_state", None)
            if st and st.get("screen") == "필드":                       # 시작 점검 결과: 지도 · 물약 · 퀘스트 수
                q = (st.get("quests") or {}).get("total")
                sub += f"  |  시작 시 {st.get('map') or '?'} · 물약 {st.get('potions', '?')}개" + (f" · 퀘스트 {q}개" if q else "")
            self.lbl_window_sub.setText(sub)
        else:
            self.lbl_window.setText("찾는 중")
            self.lbl_window_sub.setText("게임 창이 없거나 맨 앞에 있지 않습니다")
        if getattr(bot, "farm", None):
            cur = bot.farm.current
            self.lbl_source.setText("사냥터 모드")
            self.lbl_source_sub.setText(bot.farm.describe() if cur or bot.farm.power else "사냥터 선택 대기")
        else:
            self.lbl_source.setText(bot._source or "-")
            blocked = [n for n in ("본편", "서사", "일일", "주간") if bot.mission and bot.mission.is_blocked(n)]
            self.lbl_source_sub.setText("중단 중: " + ", ".join(blocked) if blocked else "중단된 퀘스트 없음")

        if bot.paused:
            self.pill.set("일시정지", C["warn"])
            self.btn_pause.setText("▶  재개")
            msg = bot.alert_reason or "일시정지 중입니다"
            self._show_state("일시정지", msg, C["warn"], None)
            return
        self.btn_pause.setText("⏸  일시정지")
        if bot.waiting_user:
            self.pill.set("사용자 조작 감지", C["accent"])
            self._show_state("양보 중", f"직접 조작을 감지했습니다 · {bot.user_resume}초간 조작이 없으면 재개", C["accent"], None)
            return
        if not win_ok or not bot.window.is_foreground():
            self.pill.set("게임 창 대기", C["warn"])
            self._show_state("게임 창 대기", "게임 창을 맨 앞으로 가져오면 시작합니다", C["warn"], None)
            return
        self.pill.set("연습 중" if bot.dry_run else "실행 중", C["ok"])
        key = bot._last_state or "IDLE"
        name, desc, color = STATE_INFO.get(key, (key, "", C["sub"]))
        if key == "IDLE" and bot._want_hunt:
            desc = "자동 사냥 켜는 중"
        self._show_state(name, desc, color, key)

    def _show_state(self, name, desc, color, key):
        self.lbl_state.setText(name)
        self.lbl_state.setStyleSheet(f"color:{color};")
        self.lbl_state_sub.setText(desc)
        for k, c in self.chips.items():
            c.on(k == key)

    # ---------- 통계 기록 / 미리보기 / 예약
    def _refresh_extra(self, bot):
        now = time.monotonic()
        if bot and bot.power and (bot.power != self._last_power or now - self._power_rec_at > 1800):
            from gui_extra import record_power
            record_power(bot.power)
            self._last_power, self._power_rec_at = bot.power, now
        if now - self._stats_at > 60 and self.tabs.currentWidget() is self.stats_tab:
            self._stats_at = now
            self.stats_tab.refresh()
        self.preview_tab.tick(self.tabs.currentWidget() is self.preview_tab and self.isVisible())
        # 예약 시작
        if self.chk_sched_start.isChecked() and not self.thread:
            t = self.time_start.time()
            nowt = datetime.now()
            if (nowt.hour, nowt.minute) == (t.hour(), t.minute()) and self._sched_day != nowt.date():
                self._sched_day = nowt.date()
                logging.info("예약 시작")
                self.start()
        # 예약 정지
        if (self.chk_sched_stop.isChecked() and self.thread and self.started_at
                and now - self.started_at > self.spin_stop_h.value() * 3600):
            logging.info("예약 정지 (%.1f시간 경과)", self.spin_stop_h.value())
            self.stop()

    # ---------- 트레이
    def _build_tray(self):
        self.tray = None
        self._quitting = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(os.path.join(ROOT, "assets", "app.ico")), self)
        m = QMenu()
        m.addAction("열기", self._show_from_tray)
        m.addAction("시작", self.start)
        m.addAction("일시정지/재개", self.toggle_pause)
        m.addAction("정지", self.stop)
        m.addSeparator()
        m.addAction("완전히 종료", self._quit)
        self.tray.setContextMenu(m)
        self.tray.setToolTip("도깨비헬퍼")
        self.tray.activated.connect(lambda r: self._show_from_tray() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self):
        self._quitting = True
        self.close()

    def show_changelog_if_new(self):
        from version import CHANGELOG, VERSION
        last = self.saved.get("_last_version", "")
        if last == VERSION:
            return
        new = [c for c in CHANGELOG if not last or tuple(map(int, c[0].split("."))) > tuple(map(int, last.split(".")))]
        text = "\n".join(f"v{v} ({d})  {n}" for v, d, n in new[:8])
        self.saved["_last_version"] = VERSION
        self._save()
        QMessageBox.information(self, f"v{VERSION} 업데이트", "바뀐 점:\n\n" + text)

    def restore_geometry(self):
        """저장된 창 위치/크기를 복원. 저장된 자리가 지금 연결된 모니터 안에 없으면 기본 위치를 쓴다."""
        g = self.saved.get("_geometry")
        if not (isinstance(g, list) and len(g) == 5):
            return
        x, y, w, h, maxi = g
        rect = QRect(int(x), int(y), max(int(w), 600), max(int(h), 400))
        # 창의 제목 줄 일부(왼쪽 위 100x40)가 어떤 화면에든 보여야 한다
        grab = QRect(rect.x(), rect.y(), 100, 40)
        if not any(sc.availableGeometry().intersects(grab) for sc in QApplication.screens()):
            return
        self.setGeometry(rect)
        if maxi:
            self.showMaximized()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._geo_timer.start(600)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._geo_timer.start(600)

    def _save_geometry_now(self):
        if self.saved is not None:
            try:
                self._save()
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, e):
        self._save_geometry_now()
        if self.tray and self.thread and not self._quitting:
            # 실행 중에는 창만 숨기고 트레이에서 계속 돈다
            e.ignore()
            self.hide()
            self.tray.showMessage("도깨비헬퍼", "트레이에서 계속 실행 중입니다. 완전히 끄려면 트레이 메뉴 → 완전히 종료",
                                  QSystemTrayIcon.Information, 3000)
            return
        if self.thread:
            self.stop()
            self.thread.wait(4000)
        self._save()
        e.accept()


def main():
    try:        # 작업표시줄에서 python 아이콘 대신 앱 아이콘이 보이도록 별도 앱 ID 지정
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameAutoPlay.Dokkaebi.Panel")
    except Exception:  # noqa: BLE001
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(os.path.join(ROOT, "assets", "app.ico")))
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    win = Window()
    win.restore_geometry()
    win.show()
    if "--autostart" in sys.argv:
        logging.info("업데이트 내용은 설정 탭의 '버전 정보'에서 볼 수 있습니다")
    else:
        QTimer.singleShot(800, win.show_changelog_if_new)     # 자동 시작 때는 팝업이 게임 창 포커스를 뺏으므로 생략
    if "--autostart" in sys.argv:
        if "--farm" in sys.argv:                  # 시작하면서 바로 앵벌 모드 (제어판 버튼을 누르지 않고도 켜기)
            win._pending_farm = True
        QTimer.singleShot(1500, win.start)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
