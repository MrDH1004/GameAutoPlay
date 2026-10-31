"""도깨비헬퍼 - 도깨비의세계 자동 진행 매크로 제어판 (PySide6).

    python gui.py

게임이 관리자 권한으로 실행되므로 이 프로그램도 관리자 권한이 필요하다 (아니면 자동으로 다시 실행한다).
"""
import ctypes
import html
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import time

from app_paths import ROOT, initialize_data
from storage import load_json, save_json
if getattr(sys, "frozen", False):
    initialize_data()
os.chdir(ROOT)


# GUI는 일반 사용자 권한으로 실행한다. 관리자 게임 창 입력은 사용자가 한 번 승인해
# 등록한 Cua Driver 서비스가 담당하므로 앱 시작 때마다 UAC를 다시 요청하지 않는다.

import yaml
from datetime import datetime
from PySide6.QtCore import QObject, QRect, QThread, QTime, QTimer, Qt, Signal, QLockFile, QEvent
from toggle_switch import Toggle
import icons
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QFont, QKeySequence, QShortcut,
                           QPixmap, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QSpinBox,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QLineEdit, QScrollArea, QComboBox,
                               QSystemTrayIcon, QMenu, QTimeEdit, QMessageBox, QButtonGroup, QProgressBar, QFileDialog,
                               QGraphicsOpacityEffect, QSizePolicy, QToolTip)

CONFIG_PATH = os.path.join(ROOT, "config.yaml")
SETTINGS_PATH = os.path.join(ROOT, "gui_settings.json")
LOG_PATH = os.path.join(ROOT, "logs", "bot2.log")
LOCKED_SETTINGS = {("collect", "enabled"), ("rewards", "enabled"), ("preset", "enabled")}

# 게임 내 도감에 표시되는 계열별 순서. 목록에 없는 기술은 기존 순서를 유지한다.
SKILL_DISPLAY_ORDER = {
    "절명": ["찌르기", "연속 찌르기", "약점 베기", "삼단 찌르기", "내려찍기(절명)", "기습", "환영 참격", "심장 절단", "은신", "암습"],
    "연타": ["난타", "회전 난격", "집중 연타", "주먹 지르기", "휘둘러 내려치기", "묵직한 주먹", "연쇄 타격", "폭주"],
    "강타": ["대검 강타", "돌풍 강격", "내려치기(강타)", "올려치기", "대검 찍기", "크게 베기", "연속 충격", "휘둘러치기", "검기 날리기", "검기 폭발", "천벌"],
    "척살": ["묵영점정", "용사비등", "먹 베기", "삼묵법", "무수불축", "필력종횡", "일필휘지"],
    "저격": ["꿰뚫는 저격", "사격", "이중 사격", "밀쳐내는 화살", "쇠뇌 사격", "쐐기 화살", "일점 저격", "폭발하는 화살", "반동 사격", "집중 사격", "정조준"],
    "주술": ["영혼 소용돌이", "부적 던지기", "살 날리기", "저주 내리기", "신벌의 영역", "신내림"],
    "파멸": ["화염 분출", "붕괴하는 대지", "번개 폭풍"],
    "강인": ["방패 치기", "전투의 함성", "방패 밀기", "방패 충격파", "방패 내려찍기", "강인한 의지", "방패 낙하"],
}


def _replace_running_gui(instance_lock, lock_path):
    """Terminate the GUI recorded by QLockFile and take ownership of its lock."""
    try:
        with open(lock_path, encoding="utf-8") as stream:
            previous_pid = int(stream.readline().strip())
    except (OSError, TypeError, ValueError):
        previous_pid = 0

    if previous_pid and previous_pid != os.getpid():
        process = ctypes.windll.kernel32.OpenProcess(
            0x0001 | 0x00100000,  # PROCESS_TERMINATE | SYNCHRONIZE
            False,
            previous_pid,
        )
        if process:
            try:
                ctypes.windll.kernel32.TerminateProcess(process, 0)
                ctypes.windll.kernel32.WaitForSingleObject(process, 5000)
            finally:
                ctypes.windll.kernel32.CloseHandle(process)

    # The owner is now gone (or the file was stale), so remove the old lock and
    # retry briefly while Windows finishes releasing the process resources.
    for _ in range(20):
        instance_lock.removeStaleLockFile()
        if instance_lock.tryLock(0):
            return True
        time.sleep(0.1)
    return False

# ---------------------------------------------------------------- 색상/스타일
from ui_theme import C, QSS, qss_for, theme_colors
from event_schedule import dashboard_schedule


class UpdateCheckThread(QThread):
    available = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            from updater import check_for_update
            from version import VERSION
            info = check_for_update(VERSION)
            if info is not None:
                self.available.emit(info)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class UpdateDownloadThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        try:
            from updater import stage_update
            self.ready.emit(stage_update(self.info))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))

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
    ("toggle", ("normal_mode", "enabled"), "일반 모드 기본 시작", "현재 위치에서 자동 사냥 · 저체력 때 추천 사냥터 피신 · 회복 후 원래 지역 복귀"),
    ("toggle", ("daily_mode", "enabled"), "일일 전용 모드 기본 시작", "본편·서사·주간은 제외 · 메인 목록에서 [일일]이 모두 사라지면 앵벌 모드 전환"),
    ("toggle", ("story_mode", "enabled"), "서사 전용 모드 기본 시작", "서사 탭에서 현재 전투력에 맞는 '적합' 임무만 수락·자동 진행"),
    ("toggle", ("secret_realm_combat", "enabled"), "망자의 마을 HP별 프리셋 전환", "HP가 충분하면 광역, 낮으면 단일 프리셋으로 전환"),
    ("toggle", ("farm", "fallback_when_blocked"), "퀘스트 전부 막히면 안전 사냥터로", "사망으로 모든 퀘스트가 중단되면 전투력에 맞는 사냥터에서 사냥"),
    ("toggle", ("yield_only_game_input",), "게임 창 조작에만 양보", "다른 창에서 하는 작업은 무시하고, 게임을 직접 조작할 때만 멈춤"),
    ("toggle", ("notify", "enabled"), "폰 알림/원격 제어", "사망·일시정지·멈춤을 텔레그램/디스코드로 알림 (아래 토큰 입력 필요)"),
    ("toggle", ("rewards", "enabled"), "보상 자동 받기", "1시간마다 우편함·업적·길라잡이 보상의 '받기' 버튼만 누름 (현재 모두 제외 설정이라 꺼 둠)"),
    ("toggle", ("hp", "enabled"), "HP 회복 관리", "평소 물약은 게임에 맡기고, 휴식·자리 회피 중에만 Ctrl+1로 회복"),
    ("toggle", ("clan_help", "enabled"), "문파 도움 자동 응답", "필드 악수 요청에 응답하고 영기 수련은 5분마다 바로가기 후 문파 도움 확인"),
    ("toggle", ("boss_hunt", "enabled"), "21시 보스토벌 자동 참가", "공통 이벤트 최우선 · 비경이면 나가기 · 20:55 걸어가기 · 21:00 미도착 시 금액 제한 없이 축지술"),
    ("toggle", ("boss_hunt", "mountain_tiger_enabled"), "산신 산군대호 자동 참가", "월·수·금·일 21시 산신 산군대호 보스토벌 참가"),
    ("toggle", ("boss_hunt", "skeleton_general_enabled"), "해골장군 자동 참가", "화·목·토 21시 해골장군 보스토벌 참가"),
    ("toggle", ("nakhwa_boss", "enabled"), "22시 요화선녀 자동 참가", "공통 이벤트 최우선 · 비경이면 나가기 · 21:58 준비 · 낙화의 전장 중앙 이동 후 AUTO"),
    ("toggle", ("collect", "enabled"), "화첩 자동 수집", "사용자가 직접 수집하므로 자동 사냥·등록·스캔은 꺼 둡니다"),
    ("toggle", ("hp", "rest_enabled"), "HP 낮으면 쉬고 회복", "보스가 아닐 때 HP 50% 이하면 잡던 몹까지만 잡고 자동 사냥 끔 → 100% 차면 다시 켬"),
    ("float", ("hp", "evade_below"), "피신 시작 HP 비율", (0.05, 0.80, " 이하")),
    ("toggle", ("hp", "startup_risk_check"), "시작·재개 시 위험도 확인", "같은 몬스터가 내 전투력의 허용 비율을 넘는지 두 프레임으로 확인 · 무료 순간이동 실패 시 일시정지"),
    ("toggle", ("preset", "enabled"), "도술 프리셋 자동 변경 금지", "항상 꺼짐 · 사용자가 지정한 도술 프리셋을 앱이 변경하지 않음"),
    ("toggle", ("potion_shop", "enabled"), "물약 자동 구매", "HP 물약 50개 이하면 하루 1회, 물약 200개만 구매"),
    ("int", ("potion_shop", "buy_below"), "물약 구매 기준", (1, 199, "개 이하")),
    ("int", ("potion_shop", "purchase_amount"), "물약 구매 수량", (1, 249, "개")),
    ("toggle", ("enhance", "enabled"), "도술 계열 강화 (실험)", "재료가 충분하면 파멸·저격 계열 강화 (골드 소모, 강화 후 화면 미검증)"),
    ("toggle", ("yeonggi", "enabled"), "영기 수련 자동 진행", "주기적으로 메뉴를 열어 빨간 점 노드를 수련"),
    ("toggle", ("mission", "enabled"), "임무 화면(J) 자동 진행", "일일 퀘스트를 먼저 확인하고 완료하면 다음 일일 임무를 진행"),
    ("toggle", ("quest_tracker", "scan", "enabled"), "트래커 OCR 스캔", "우측 퀘스트 목록을 스크롤해 진행 가능한 퀘스트 탐색"),
    ("toggle", ("autohunt", "after_move"), "이동 종료 후 자동 사냥(T)", "자동 이동이 끝나면 자동 사냥을 켬"),
    ("int", ("yeonggi", "interval"), "영기 수련 확인 주기", (30, 3600, "초")),
    ("int", ("secret_realm_combat", "aoe_preset"), "망자의 마을 광역 프리셋", (1, 5, "  A=1 · B=2 · C=3 · D=4 · E=5")),
    ("int", ("secret_realm_combat", "single_preset"), "망자의 마을 단일 프리셋", (1, 5, "  A=1 · B=2 · C=3 · D=4 · E=5")),
    ("int", ("secret_realm_combat", "single_below_hp_percent"), "단일 프리셋 전환 HP", (10, 90, "% 이하")),
    ("int", ("secret_realm_combat", "aoe_resume_hp_percent"), "광역 프리셋 복귀 HP", (20, 100, "% 이상")),
    ("int", ("secret_realm_combat", "daily_duration_minutes"), "일일 망자의 마을 체류 시간", (1, 180, "분")),
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
def _is_normal_stop(error, requested=False, bot=None):
    """정지와 동시에 진행 중 입력이 취소돼 다른 예외로 감싸져도 정상 종료로 분류한다."""
    from bot2.farm import StopRequested
    event = getattr(bot, "stop_event", None)
    if requested or (event is not None and event.is_set()):
        return True
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, StopRequested):
            return True
        seen.add(id(current))
        current = current.__context__ or current.__cause__
    return False


class BotThread(QThread):
    failed = Signal(str)

    def __init__(self, config, dry_run, debug):
        super().__init__()
        self.config, self.dry_run, self.debug = config, dry_run, debug
        self.bot = None
        self.stop_requested = False

    def run(self):
        execution_lock = None
        try:
            from execution_lock import acquire_bot_lock
            execution_lock = acquire_bot_lock(self.config)
            from bot.window import enable_dpi_awareness
            from bot2.brain import Bot
            try:
                enable_dpi_awareness()
            except Exception:
                pass
            self.bot = Bot(self.config, dry_run=self.dry_run)
            if self.stop_requested:
                return
            self.bot.run()
        except Exception as e:      # 화면에 보여주고 스레드 종료
            if _is_normal_stop(e, self.stop_requested, self.bot):
                logging.info("정지 요청으로 진행 중 입력을 취소하고 정상 종료함")
            else:
                logging.exception("매크로 오류")
                self.failed.emit(f"{type(e).__name__}: {e}")
        finally:
            if self.bot:
                self.bot.stop_event.set()
                self.bot.notifier.stop()
                self.bot.live.stop()
            # 봇 정지·앱 종료 시 dokkaebi-helper Cua 세션과 커서 상태를 남기지 않는다.
            from bot import actions
            actions.clear_input_backend()
            self.bot = None
            if execution_lock is not None:
                execution_lock.unlock()


# ---------------------------------------------------------------- 위젯
def card(title, parent=None):
    f = QFrame(parent)
    f.setObjectName("card")
    # 긴 상태 문구 하나가 카드의 최소 너비를 키워 대시보드 전체를
    # 가로로 밀지 않도록 항상 현재 뷰포트 폭에 맞춰 줄어들게 한다.
    f.setMinimumWidth(0)
    f.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(4)
    t = QLabel(title)
    t.setObjectName("cardTitle")
    t.setMinimumWidth(0)
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


class OwnedSkillIcon(QCheckBox):
    """보유 스킬은 좌측 상단 체크 배지, 미보유 스킬은 어두운 아이콘으로 표시한다."""
    def __init__(self, name, icon_path, parent=None):
        super().__init__(parent)
        self.setText("")
        self.setAccessibleName(name)
        self.setFixedSize(62, 62)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QCheckBox {{border:1px solid {C['line']};border-radius:8px;background:transparent;}}"
            f"QCheckBox:hover {{border-color:{C['accent']};background:{C['panel2']};}}"
            "QCheckBox::indicator {width:0;height:0;}"
        )
        self.icon_label = QLabel(self)
        self.icon_label.setGeometry(7, 7, 48, 48)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.icon_opacity = QGraphicsOpacityEffect(self.icon_label)
        self.icon_label.setGraphicsEffect(self.icon_opacity)

        self.badge = QLabel("✓", self)
        self.badge.setGeometry(2, 2, 19, 19)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.setStyleSheet(
            f"background:{C['accent']};color:#091019;border:1px solid #ffffff;"
            "border-radius:9px;font-weight:900;font-size:12px;"
        )
        self.toggled.connect(self._render_owned_state)
        self._render_owned_state(False)

    def _render_owned_state(self, checked):
        self.badge.setVisible(bool(checked))
        self.icon_opacity.setOpacity(1.0 if checked else 0.28)

    def hitButton(self, position):
        """빈 텍스트와 숨긴 기본 체크 영역 대신 아이콘 전체를 클릭 영역으로 사용한다."""
        return self.rect().contains(position)


class InstantTooltipBrowser(QTextBrowser):
    """문서의 title 툴팁을 Qt 기본 지연 없이 마우스 이동 즉시 표시한다."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._instant_tip = ""
        self._collapsible_template = ""
        self._collapsible_sections = {}
        self._expanded_sections = set()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._open_anchor)

    def set_collapsible_html(self, template, sections):
        self._collapsible_template = template
        self._collapsible_sections = dict(sections)
        self._expanded_sections.intersection_update(self._collapsible_sections)
        self._render_collapsible()

    def _render_collapsible(self):
        value = self.verticalScrollBar().value()
        document = self._collapsible_template
        for key, body in self._collapsible_sections.items():
            expanded = key in self._expanded_sections
            document = document.replace(f"@@DETAIL_{key}@@", body if expanded else "")
            document = document.replace(f"@@ARROW_{key}@@", "▾" if expanded else "▸")
            document = document.replace(f"@@LABEL_{key}@@", "상세 접기" if expanded else "상세 보기")
        self.setHtml(document)
        self.verticalScrollBar().setValue(min(value, self.verticalScrollBar().maximum()))

    def _open_anchor(self, url):
        if url.scheme() == "skilldetail":
            key = url.path().lstrip("/") or url.host()
            if key in self._expanded_sections:
                self._expanded_sections.remove(key)
            else:
                self._expanded_sections.add(key)
            self._render_collapsible()
            return
        QDesktopServices.openUrl(url)

    def mouseMoveEvent(self, event):
        tip = html.unescape(self.cursorForPosition(event.position().toPoint()).charFormat().toolTip())
        if tip:
            if tip != self._instant_tip:
                self._instant_tip = tip
                QToolTip.showText(event.globalPosition().toPoint(), tip, self.viewport())
        elif self._instant_tip:
            self._instant_tip = ""
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._instant_tip = ""
        QToolTip.hideText()
        super().leaveEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            return True
        return super().viewportEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        from version import VERSION
        self.setWindowTitle(f"도깨비헬퍼 v{VERSION}")
        self.resize(1000, 720)
        self.setMinimumSize(900, 640)
        self.setWindowIcon(icons.make("brand", C["accent"]))
        self.thread = None
        self._last_error = ""
        self._closing = False
        self.started_at = 0.0
        self.paused_total = 0.0
        self.session_total = 0.0
        self.recent = []
        self.controls = {}

        with open(CONFIG_PATH, encoding="utf-8") as f:
            self.base_config = yaml.safe_load(f)
        self.saved = self._load_saved()
        self._theme_name = self.saved.get("_theme", "dark")
        self._apply_theme(self._theme_name)
        self._geo_timer = QTimer(self)                  # 창을 옮기거나 크기를 바꾸면 잠시 뒤 저장 (강제 종료돼도 위치가 남게)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.timeout.connect(self._save_geometry_now)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(12)
        outer.addLayout(self._build_header())
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setStyleSheet(f"background:{C['panel2']}; color:{C['warn']}; padding:10px; border-radius:8px;")
        self.notice.hide()
        outer.addWidget(self.notice)

        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard(), "대시보드")
        tabs.addTab(self._build_settings(), "설정")
        tabs.addTab(self._build_flow_page(), "동작 흐름")
        from gui_extra import PreviewTab, SpotEditor, StatsTab
        self.stats_tab = StatsTab(C)
        self.preview_tab = PreviewTab(C, lambda: self.thread.bot if self.thread else None)
        tabs.addTab(self.stats_tab, "통계")
        tabs.addTab(self.preview_tab, "미리보기")
        tabs.addTab(SpotEditor(C), "사냥터")
        tabs.addTab(self._build_recommend(), "추천")
        tabs.addTab(self._build_log(), "로그")
        tabs.addTab(self._build_updates(), "업데이트")
        tabs.addTab(self._build_support(), "도움말")
        self.tabs = tabs
        tabs.tabBar().hide()
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_navigation())
        body.addWidget(tabs, 1)
        outer.addLayout(body, 1)
        self._last_power = None
        self._power_rec_at = 0.0
        self._stats_at = 0.0
        self._sched_day = None
        self._build_tray()

        foot = QLabel("F11 일시정지 / 재개   ·   F12 종료   ·   창을 닫으면 헬퍼와 관련 작업을 모두 종료")
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
            self.file_handler = fh

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(1000)  # 대시보드 수치는 초 단위라 1초 갱신이면 충분하며 불필요한 재그리기를 줄인다
        self._set_running(False)
        self._settings_snapshot = self._settings_values()
        for path, w in self.controls.items():
            if isinstance(w, QCheckBox):
                w.toggled.connect(lambda checked, p=path: self._toggle_setting_changed(p, checked))
            else:
                signal = w.textChanged if isinstance(w, QLineEdit) else w.valueChanged
                signal.connect(self._settings_changed)
        for w, key, index in (
                (self.chk_debug, "_debug", 1), (self.chk_dry, "_dry", 2),
                (self.chk_sched_start, "_sched_start_on", 3),
                (self.chk_sched_stop, "_sched_stop_on", 5)):
            w.toggled.connect(
                lambda checked, k=key, i=index: self._aux_toggle_changed(k, i, checked))
        self.time_start.timeChanged.connect(self._settings_changed)
        self.spin_stop_h.valueChanged.connect(self._settings_changed)
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.search_shortcut.activated.connect(self._focus_settings_search)
        if self._settings_load_warning:
            self._notify(self._settings_load_warning)
        QTimer.singleShot(3000, self._start_skill_learning)

        if getattr(sys, "frozen", False) and not os.environ.get("DOKKAEBI_TEST"):
            QTimer.singleShot(1800, self._check_for_updates)

    def _check_for_updates(self):
        """앱 시작을 막지 않고 GitHub 최신 정식 Release를 확인한다."""
        if getattr(self, "_update_check_thread", None):
            return
        self._update_check_thread = UpdateCheckThread(self)
        self._update_check_thread.available.connect(self._show_update_prompt)
        self._update_check_thread.failed.connect(
            lambda message: logging.info("업데이트 확인 보류: %s", message))
        self._update_check_thread.finished.connect(
            lambda: setattr(self, "_update_check_thread", None))
        self._update_check_thread.start()

    def _show_update_prompt(self, info):
        from version import VERSION
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(f"도깨비헬퍼 업데이트 v{info.version}")
        box.setText(f"새 버전 v{info.version}을 설치할 수 있습니다.")
        notes = info.notes.strip() or "업데이트 내역이 없습니다."
        box.setInformativeText(f"현재 버전: v{VERSION}\n\n{notes[:1600]}")
        install = box.addButton("업데이트 설치", QMessageBox.AcceptRole)
        box.addButton("나중에", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is install:
            self._download_update(info)

    def _download_update(self, info):
        if getattr(self, "_update_download_thread", None):
            return
        self._notify(f"v{info.version} 업데이트를 다운로드하고 검증하는 중입니다…")
        self._update_download_thread = UpdateDownloadThread(info, self)
        self._update_download_thread.ready.connect(self._apply_downloaded_update)
        self._update_download_thread.failed.connect(self._update_download_failed)
        self._update_download_thread.finished.connect(
            lambda: setattr(self, "_update_download_thread", None))
        self._update_download_thread.start()

    def _update_download_failed(self, message):
        self._notify("업데이트를 설치하지 못했습니다: " + message)
        QMessageBox.warning(self, "업데이트 실패", message)

    def _apply_downloaded_update(self, package):
        try:
            from updater import create_patch_script, launch_patch_script
            script = create_patch_script(package)
            launch_patch_script(script)
        except Exception as error:  # noqa: BLE001
            self._update_download_failed(str(error))
            return
        self._notify("업데이트 검증이 끝났습니다. 앱을 종료하고 새 버전으로 다시 시작합니다.")
        self._quitting = True
        QTimer.singleShot(300, self.close)

    def _start_skill_learning(self, force=False):
        """깨비지지 도감/덱을 백그라운드에서 갱신하고 CPU 절반 범위로 재탐색한다."""
        if os.environ.get("DOKKAEBI_TEST"):
            return False
        running = getattr(self, "_skill_learning_process", None)
        if running is not None and running.poll() is None:
            status = getattr(self, "skill_sim_status", None)
            if status:
                status.setText("CPU 시뮬레이션 실행 중 · CPU 최대 50%")
            return False
        result_path = os.path.join(ROOT, "assets", "skills", "simulation_results.json")
        try:
            if not force and time.time() - os.path.getmtime(result_path) < 6 * 3600:
                return False
        except OSError:
            pass
        args = [sys.executable]
        local_only = force == "owned"
        if getattr(sys, "frozen", False):
            args += ["--skill-recalculate" if local_only else "--skill-learn"]
        else:
            args += [os.path.abspath(__file__), "--skill-recalculate" if local_only else "--skill-learn"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            self._skill_learning_process = subprocess.Popen(  # noqa: S603
                args, cwd=ROOT, creationflags=flags, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if local_only:
                self._pending_owned_recalculation = False
            status = getattr(self, "skill_sim_status", None)
            if status:
                status.setText("CPU 시뮬레이션 실행 중 · CPU 최대 50%")
            QTimer.singleShot(1500, self._poll_skill_learning)
            logging.info("도술 학습 시작: 깨비지지 전체 도감·유저 덱 갱신 및 CPU 절반 제한 조합 탐색")
            return True
        except OSError as exc:
            logging.warning("도술 학습 프로세스 시작 실패: %s", exc)
            status = getattr(self, "skill_sim_status", None)
            if status:
                status.setText("시뮬레이션 시작 실패")
            return False

    def _manual_skill_learning(self):
        self._start_skill_learning(force=True)

    def _poll_skill_learning(self):
        process = getattr(self, "_skill_learning_process", None)
        if process is None:
            return
        code = process.poll()
        if code is None:
            QTimer.singleShot(1500, self._poll_skill_learning)
            return
        self._skill_learning_process = None
        if code != 0:
            self.skill_sim_status.setText("시뮬레이션 실패 · 로그 확인")
            if hasattr(self, "skill_owned_status"):
                self.skill_owned_status.setText("추천 재계산 실패 · 로그를 확인하세요")
            logging.warning("도술 CPU 시뮬레이션 실패 (종료 코드 %s)", code)
            return
        recommend = self.tabs.widget(6)
        current = recommend.currentIndex()
        old_skill = recommend.widget(0)
        old_f2p = recommend.widget(1)
        recommend.removeTab(1)
        recommend.removeTab(0)
        recommend.insertTab(0, self._build_skills(), "스킬")
        recommend.insertTab(1, self._build_skills(f2p_only=True), "무과금")
        recommend.setCurrentIndex(current)
        old_skill.deleteLater()
        old_f2p.deleteLater()
        if hasattr(self, "purchase_guide"):
            self._show_purchase_guide()
        self.skill_sim_status.setText("계산 완료 · 최신 결과 표시 중")
        if hasattr(self, "skill_owned_status"):
            checked = sum(box.isChecked() for box in self.skill_owned_checks.values())
            self.skill_owned_status.setText(f"보유 {checked}개 저장 · 추천 재계산 완료")
        logging.info("도술 CPU 시뮬레이션 완료 → 추천 화면 갱신")
        if getattr(self, "_pending_owned_recalculation", False):
            QTimer.singleShot(0, lambda: self._start_skill_learning(force="owned"))

    def _build_navigation(self):
        rail = QFrame()
        rail.setObjectName("sidebar")
        rail.setFixedWidth(138)
        col = QVBoxLayout(rail)
        col.setContentsMargins(10, 14, 10, 12)
        label = QLabel("WORKSPACE")
        label.setObjectName("sub")
        col.addWidget(label)
        self.nav_group = QButtonGroup(self)
        for i, name in enumerate(("dashboard", "settings", "flow", "stats", "preview", "map", "book", "log", "update", "support")):
            button = QPushButton(self.tabs.tabText(i))
            button.setObjectName("nav")
            button.setIcon(icons.make(name, C["accent"]))
            button.setIconSize(icons.ICON_SIZE)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(self.tabs.tabText(i))
            self.nav_group.addButton(button, i)
            col.addWidget(button)
        self.nav_group.button(0).setChecked(True)
        self.nav_group.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda i: self.nav_group.button(i).setChecked(True))
        col.addStretch()
        help_label = QLabel("설정 검색\nCtrl + K\n\n무료 이동만 사용\n수집은 직접 진행")
        help_label.setObjectName("sub")
        col.addWidget(help_label)
        return rail

    def _build_updates(self):
        """자동 업데이트 상태와 전체 변경 이력을 표시한다."""
        from html import escape
        from version import CHANGELOG, VERSION
        h = (f"<body style='color:{C['text']}'><h1>업데이트 내역</h1>"
             f"<p style='color:{C['sub']}'>현재 버전 <b style='color:{C['accent']}'>v{escape(VERSION)}</b>"
             " · 실행 시 GitHub의 새 정식 버전을 자동 확인하고 업데이트 팝업을 표시합니다.</p>")
        for i, (ver, date, note) in enumerate(CHANGELOG):
            current = i == 0
            color = C["accent"] if current else C["text"]
            badge = " &nbsp;현재 버전" if current else ""
            h += (f"<h3 style='color:{color}'>v{escape(ver)} &nbsp; "
                  f"<span style='color:{C['sub']}'>{escape(date)}</span>{badge}</h3>"
                  f"<p>{escape(note)}</p><hr style='border-color:{C['line']}'>")
        h += "</body>"
        browser = self._guide_browser()
        browser.setHtml(h)
        return browser

    def _build_flow_page(self):
        """모드별 판단과 복귀 경로를 UML형 흐름도로 표시한다."""
        from mode_flow import FLOW_CHOICES
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        header = QHBoxLayout()
        title = QLabel("자동화 동작 흐름")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(QLabel("보기"))
        self.flow_follow = QCheckBox("실시간 따라가기")
        self.flow_follow.setChecked(True)
        header.addWidget(self.flow_follow)
        self.flow_combo = QComboBox()
        for label, key in FLOW_CHOICES:
            self.flow_combo.addItem(label, key)
        self.flow_combo.setMinimumWidth(180)
        header.addWidget(self.flow_combo)
        layout.addLayout(header)

        live_card, live_layout = card("실시간 동기화")
        live_grid = QGridLayout()
        self.flow_live = {}
        for column, (key, label) in enumerate((("mode", "활성 모드"), ("state", "현재 상태"),
                                                ("place", "현재 위치"), ("auto", "HP · AUTO"))):
            box = QVBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("sub")
            value = QLabel("—")
            value.setObjectName("big")
            value.setWordWrap(True)
            box.addWidget(caption)
            box.addWidget(value)
            live_grid.addLayout(box, 0, column)
            self.flow_live[key] = value
        live_layout.addLayout(live_grid)
        self.flow_decision = QLabel("시작 후 실제 판단 단계가 표시됩니다.")
        self.flow_decision.setWordWrap(True)
        self.flow_decision.setStyleSheet(f"background:{C['panel2']};color:{C['accent']};padding:9px;border-radius:7px;font-weight:700;")
        live_layout.addWidget(self.flow_decision)
        layout.addWidget(live_card)
        from flow_widget import FlowDiagram
        self.flow_view = FlowDiagram(C)
        layout.addWidget(self.flow_view, 1)
        self._flow_runtime = None
        self._flow_render_sig = None
        self.flow_combo.currentIndexChanged.connect(self._flow_manual_selected)
        self.flow_follow.toggled.connect(lambda _on: self._refresh_flow_runtime(
            self.thread.bot if self.thread else None))
        self._refresh_flow_page()
        return page

    def _flow_manual_selected(self, *_):
        if hasattr(self, "flow_follow") and self.flow_follow.isChecked():
            self.flow_follow.setChecked(False)
        self._flow_render_sig = None
        self._refresh_flow_page()

    def _refresh_flow_page(self, *_):
        if not hasattr(self, "flow_view"):
            return
        key = self.flow_combo.currentData() if hasattr(self, "flow_combo") else "overview"
        active = (self._flow_runtime or {}).get("active", ()) if getattr(self, "flow_follow", None) and self.flow_follow.isChecked() else ()
        self.flow_view.set_flow(key or "overview", active, C)

    def _refresh_flow_runtime(self, bot):
        if not hasattr(self, "flow_view"):
            return
        from mode_flow import runtime_status
        status = runtime_status(bot)
        self._flow_runtime = status
        self.flow_live["mode"].setText(status["mode"])
        self.flow_live["state"].setText(status["state"])
        self.flow_live["place"].setText(status["place"])
        hp = "?" if status["hp"] is None else f"{status['hp'] * 100:.0f}%"
        auto = "?" if status["auto"] is None else "켜짐" if status["auto"] else "꺼짐"
        self.flow_live["auto"].setText(f"HP {hp} · AUTO {auto}")
        self.flow_decision.setText("● " + status["decision"])
        key = self.flow_combo.currentData() or "overview"
        active = ()
        if self.flow_follow.isChecked():
            key = status["mode_key"]
            index = self.flow_combo.findData(key)
            if index >= 0 and index != self.flow_combo.currentIndex():
                self.flow_combo.blockSignals(True)
                self.flow_combo.setCurrentIndex(index)
                self.flow_combo.blockSignals(False)
            active = status["active"]
        signature = (key, tuple(active), self._theme_name)
        if signature != self._flow_render_sig:
            self._flow_render_sig = signature
            self._refresh_flow_page()

    def _focus_settings_search(self):
        self.tabs.setCurrentIndex(1)
        self.settings_search.setFocus()
        self.settings_search.selectAll()

    def _apply_theme(self, name):
        name = name if name in ("dark", "light") else "dark"
        self._theme_name = name
        C.clear()
        C.update(theme_colors(name))
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss_for(C))

    def _on_theme_changed(self, *_):
        if hasattr(self, "theme_combo"):
            self._apply_theme(self.theme_combo.currentData())
        self._refresh_flow_page()
        self._settings_changed()

    def _settings_values(self):
        return (tuple((p, self._value(w)) for p, w in self.controls.items()),
                self.chk_debug.isChecked(), self.chk_dry.isChecked(),
                self.chk_sched_start.isChecked(), self.time_start.time().toString("HH:mm"),
                self.chk_sched_stop.isChecked(), self.spin_stop_h.value(), self._theme_name)

    def _settings_changed(self, *_):
        if not hasattr(self, "_settings_snapshot"):
            return
        dirty = self._settings_values() != self._settings_snapshot
        self.settings_status.setText("저장하지 않은 변경이 있습니다 · 다음 시작부터 적용" if dirty else
                                     "저장된 설정 · 다음 시작부터 적용")
        self.btn_save_settings.setEnabled(dirty)

    def _persist_toggle_value(self, key, checked):
        """토글은 누르는 즉시 저장해 비정상 종료 뒤에도 마지막 선택을 복원한다."""
        previous = self.saved.get(key, None)
        self.saved[key] = bool(checked)
        try:
            self._persist_saved(self.saved)
            return True
        except OSError as error:
            if previous is None:
                self.saved.pop(key, None)
            else:
                self.saved[key] = previous
            self._notify("토글 상태 저장 실패: " + str(error), True)
            logging.error("토글 상태 저장 실패 (%s): %s", key, error)
            return False

    def _toggle_setting_changed(self, path, checked):
        value = False if path in LOCKED_SETTINGS else bool(checked)
        if not self._persist_toggle_value("/".join(path), value):
            self._settings_changed()
            return
        snapshot = list(self._settings_snapshot)
        snapshot[0] = tuple((p, value if p == path else old) for p, old in snapshot[0])
        self._settings_snapshot = tuple(snapshot)
        self._settings_changed()

    def _aux_toggle_changed(self, key, snapshot_index, checked):
        if not self._persist_toggle_value(key, checked):
            self._settings_changed()
            return
        snapshot = list(self._settings_snapshot)
        snapshot[snapshot_index] = bool(checked)
        self._settings_snapshot = tuple(snapshot)
        self._settings_changed()

    def _save_settings(self):
        if self._save():
            self.settings_status.setText("설정을 저장했습니다 · 다음 시작부터 적용")

    def _persist_saved(self, data):
        if os.environ.get("DOKKAEBI_TEST") and os.path.abspath(SETTINGS_PATH) == os.path.join(ROOT, "gui_settings.json"):
            return
        save_json(SETTINGS_PATH, data)

    def _notify(self, text, error=False):
        self.notice.setText(text)
        self.notice.setStyleSheet(f"background:{C['panel2']}; color:{C['bad'] if error else C['warn']}; padding:10px; border-radius:8px;")
        self.notice.show()

    def _backup_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "설정 백업", "도깨비헬퍼-설정.json", "설정 파일 (*.json)")
        if not path:
            return
        try:
            save_json(path, {"_schema": 1, **self._collect_values()})
            self._notify("설정을 백업했습니다. 알림 토큰과 계정 정보는 포함하지 않습니다.")
        except (OSError, ValueError) as error:
            self._notify("설정 백업 실패: " + str(error), True)

    def _restore_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "설정 복원", "", "설정 파일 (*.json)")
        if not path:
            return
        try:
            data, warning = load_json(path)
            if warning or not data:
                raise ValueError("정상적인 설정 백업 파일을 선택하세요")
            restored = {}
            for kind, setting_path, label, extra in SETTING_FIELDS:
                key = "/".join(setting_path)
                if key not in data:
                    continue
                value = data[key]
                valid = isinstance(value, bool) if kind == "toggle" else (
                    type(value) in (int, float) and extra[0] <= value <= extra[1]
                    and (kind != "int" or type(value) is int))
                if not valid:
                    raise ValueError(label + ": 값의 형식 또는 범위가 올바르지 않습니다")
                restored[key] = value
            if "_theme" in data:
                if data["_theme"] not in ("dark", "light"):
                    raise ValueError("테마 값은 dark 또는 light여야 합니다")
                restored["_theme"] = data["_theme"]
            if not restored:
                raise ValueError("복원할 수 있는 설정이 없습니다")
            self._apply_values(restored)
            self._notify("백업 설정을 불러왔습니다. 내용을 확인한 뒤 설정 저장을 누르세요.")
        except (OSError, ValueError) as error:
            self._notify("설정 복원 실패: " + str(error), True)

    def _build_support(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        title = QLabel("도움말과 환경 점검")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        instructions = QLabel("1. 게임을 전체화면으로 실행하세요.\n"
                              "2. 진행 모드와 설정을 확인하고 시작을 누르세요.\n"
                              "3. 직접 플레이할 때는 F11로 일시정지하세요. F12는 자동 진행을 종료합니다.\n"
                              "무료 이동만 사용하며, 수집은 직접 진행합니다. 평소 물약은 게임에 맡깁니다.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        actions = QHBoxLayout()
        check = QPushButton("환경 점검")
        check.setIcon(icons.make("settings", C["accent"]))
        check.setToolTip("권한·설정·필수 파일·게임 창을 읽기 전용으로 확인합니다")
        check.clicked.connect(self._check_environment)
        export = QPushButton("진단 파일 저장")
        export.setIcon(icons.make("save", C["accent"]))
        export.setToolTip("비밀값을 제외한 환경 정보와 최근 로그를 ZIP으로 저장합니다. 외부 전송은 하지 않습니다.")
        export.clicked.connect(self._export_diagnostics)
        actions.addWidget(check)
        actions.addWidget(export)
        actions.addStretch()
        layout.addLayout(actions)
        self.diagnostic_view = QTextBrowser()
        self.diagnostic_view.setPlainText("환경 점검을 누르면 결과와 해결 방법을 표시합니다. 게임 입력은 보내지 않습니다.")
        layout.addWidget(self.diagnostic_view, 1)
        self.diagnostic_report = None
        return page

    def _check_environment(self):
        from app_support import environment_report
        from html import escape
        try:
            self.diagnostic_report = environment_report(self._build_config(), ROOT)
            colors = {"ok": C["ok"], "warning": C["warn"], "error": C["bad"]}
            labels = {"ok": "정상", "warning": "확인", "error": "조치 필요"}
            rows = "".join(f"<tr><td>{escape(check['name'])}</td><td style='color:{colors[check['status']]}'>{labels[check['status']]}</td><td>{escape(check['detail']).replace(chr(10), '<br>')}</td></tr>" for check in self.diagnostic_report["checks"])
            self.diagnostic_view.setHtml(f"<p>마지막 점검 {escape(self.diagnostic_report['at'])}</p><table cellpadding='8'>{rows}</table>")
        except Exception as error:
            self._notify("환경 점검 실패: " + str(error), True)

    def _export_diagnostics(self):
        from app_support import environment_report, export_support
        path, _ = QFileDialog.getSaveFileName(self, "진단 파일 저장", "도깨비헬퍼-진단.zip", "ZIP 파일 (*.zip)")
        if not path:
            return
        try:
            cfg = self._build_config()
            report = environment_report(cfg, ROOT)
            export_support(path, report, {"/".join(p): self._value(w) for p, w in self.controls.items()},
                           [text for _, text in self.log_lines], cfg)
            self._notify("진단 파일을 저장했습니다. 토큰·웹훅·화면은 포함하지 않으며 외부로 전송하지 않습니다.")
        except (OSError, ValueError) as error:
            self._notify("진단 파일 저장 실패: " + str(error), True)

    # ---------- 저장된 설정
    def _load_saved(self):
        data, self._settings_load_warning = load_json(SETTINGS_PATH)
        invalid = []
        for kind, path, _label, extra in SETTING_FIELDS:
            key = "/".join(path)
            if key not in data:
                continue
            value = data[key]
            valid = isinstance(value, bool) if kind == "toggle" else (
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and extra[0] <= value <= extra[1] and (kind != "int" or isinstance(value, int)))
            if not valid:
                invalid.append(key)
        for key in ("_dry", "_debug", "_sched_start_on", "_sched_stop_on", "_user_detect"):
            if key in data and not isinstance(data[key], bool):
                invalid.append(key)
        if "_theme" in data and data["_theme"] not in ("dark", "light"):
            invalid.append("_theme")
        owned_skills = data.get("_owned_skills")
        if owned_skills is not None and not (
                isinstance(owned_skills, list) and all(isinstance(name, str) for name in owned_skills)):
            invalid.append("_owned_skills")
        for key in ("_daily_selected_titles", "_daily_quest_catalog"):
            daily_titles = data.get(key)
            if daily_titles is not None and not (
                    isinstance(daily_titles, list) and all(isinstance(title, str) for title in daily_titles)):
                invalid.append(key)
        value = data.get("_sched_stop_h", 6)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.5 <= value <= 48:
            invalid.append("_sched_stop_h")
        if not isinstance(data.get("_sched_start", "02:00"), str) or not QTime.fromString(data.get("_sched_start", "02:00"), "HH:mm").isValid():
            invalid.append("_sched_start")
        import re
        if not isinstance(data.get("_last_version", ""), str) or not re.fullmatch(r"\d+\.\d+\.\d+|", data.get("_last_version", "")):
            invalid.append("_last_version")
        geometry = data.get("_geometry")
        if geometry is not None and not (isinstance(geometry, list) and len(geometry) == 5
                and all(type(v) is int for v in geometry[:4]) and isinstance(geometry[4], bool)):
            invalid.append("_geometry")
        if invalid:
            for key in invalid:
                data.pop(key, None)
            self._settings_load_warning += " 잘못된 설정값을 기본값으로 바꿨습니다: " + ", ".join(invalid)
        return data

    def _save(self):
        data = dict(self.saved)
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
        data["_theme"] = self._theme_name
        data["_schema"] = 1
        g = self.geometry() if not self.isMaximized() else self.normalGeometry()
        data["_geometry"] = [g.x(), g.y(), g.width(), g.height(), self.isMaximized()]      # 창 위치/크기 (다음 실행 때 복원)
        try:
            self._persist_saved(data)
            self.saved = data
            if hasattr(self, "_settings_snapshot"):
                self._settings_snapshot = self._settings_values()
                self._settings_changed()
            return True
        except OSError as e:
            logging.error("설정을 저장하지 못했습니다: %s", e)
            if hasattr(self, "settings_status"):
                self.settings_status.setText("저장 실패 · 파일 권한과 디스크 공간을 확인하세요")
            return False

    @staticmethod
    def _value(w):
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return w.value()

    def _apply_values(self, data):
        """프로필 적용: 키 경로 문자열 → 값."""
        if not isinstance(data, dict):
            raise ValueError("설정 묶음은 JSON 객체여야 합니다")
        if "_theme" in data and data["_theme"] not in ("dark", "light"):
            raise ValueError("테마 값은 dark 또는 light여야 합니다")
        for kind, path, label, extra in SETTING_FIELDS:
            key = "/".join(path)
            if key not in data or path in LOCKED_SETTINGS:
                continue
            value = data[key]
            valid = isinstance(value, bool) if kind == "toggle" else (
                type(value) in (int, float) and extra[0] <= value <= extra[1]
                and (kind != "int" or type(value) is int))
            if not valid:
                raise ValueError(label + ": 올바른 형식과 범위의 값을 사용하세요")
        for path, w in self.controls.items():
            k = "/".join(path)
            if k not in data:
                continue
            v = False if path in LOCKED_SETTINGS else data[k]
            if isinstance(w, QCheckBox):
                w.setChecked(bool(v))
            elif isinstance(w, QLineEdit):
                w.setText(str(v))
            else:
                w.setValue(v)
        if "_theme" in data:
            self._apply_theme(data["_theme"])
            if hasattr(self, "theme_combo"):
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentIndex(self.theme_combo.findData(self._theme_name))
                self.theme_combo.blockSignals(False)
        self._settings_changed()
        logging.info("프로필 적용 (다음 시작부터)")

    def _collect_values(self):
        if hasattr(self, "theme_combo"):
            self._theme_name = self.theme_combo.currentData() or self._theme_name
        values = {"/".join(p): self._value(w) for p, w in self.controls.items()
                  if not isinstance(w, QLineEdit)}
        values["_theme"] = self._theme_name
        return values

    def _build_config(self):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config.yaml이 설정 객체가 아닙니다")
        for path, w in self.controls.items():
            set_path(cfg, path, False if path in LOCKED_SETTINGS else self._value(w))
        set_path(cfg, ("hp", "potion_normal"), False)
        cfg["user_detect"] = bool(getattr(self, "user_detect", True))       # 상단 토글 버튼 값
        cfg.setdefault("daily_mode", {})["selected_titles"] = list(
            self.saved.get("_daily_selected_titles", []))
        return cfg

    # ---------- UI 구성
    def _build_header(self):
        header = QVBoxLayout()
        header.setSpacing(10)
        row = QHBoxLayout()
        brand = QLabel()
        brand.setPixmap(icons.make("brand", C["accent"]).pixmap(32, 32))
        row.addWidget(brand)
        title = QLabel("도깨비헬퍼")
        title.setStyleSheet("font-size:16px; font-weight:800;")
        from version import VERSION
        sub = QLabel(f"도깨비의세계  ·  v{VERSION}")
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
        ic = lambda name, col=C["text"], dis=C["idle"]: icons.make(name, col, dis)       # noqa: E731
        self.btn_start = QPushButton(" 시작")
        self.btn_start.setObjectName("start")
        self.btn_start.setIcon(ic("play", "#07281a", C["idle"]))
        self.btn_start.clicked.connect(self.start)
        self.btn_pause = QPushButton(" 일시정지")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop = QPushButton(" 정지")
        self.btn_stop.setObjectName("stop")
        self.btn_stop.setIcon(ic("stop", C["bad"], C["idle"]))
        self.btn_stop.clicked.connect(self.stop)
        self.btn_potion = QPushButton(" 물약 구매")
        self.btn_potion.setIcon(ic("potion"))
        self.btn_potion.setToolTip("타바로 성 잡화 상점으로 가서 2단 체력 회복 물약을 200개만 구매")
        self.btn_potion.clicked.connect(self.buy_potion)
        self._pending_potion = False
        self.btn_evade = QPushButton(" 자리 회피")
        self.btn_evade.setIcon(ic("evade"))
        self.btn_evade.setToolTip("전투력에 맞는 길라잡이 무료 순간이동을 먼저 시도합니다. 일반 모드는 회복 후 저장한 원래 지역으로 복귀하며, 그 외 모드는 안전을 위해 순간이동한 자리에 남습니다.")
        self.btn_evade.clicked.connect(self.request_evade)
        self._pending_farm = False
        self._pending_normal = False
        self._pending_daily = False
        self._pending_story = False
        self._pending_daily_scan = False
        self._pending_daily_titles = None
        # 켜고 끄는 기능은 설정 탭과 같은 토글 스위치로 (켜짐/꺼짐이 글자가 아니라 스위치 모양으로 보인다)
        self.btn_farm = Toggle("앵벌 모드", on_color=C["ok"], off_color=C["idle"])
        self.btn_farm.setToolTip("켜짐: 퀘스트를 멈추고 전투력에 맞는 사냥터에서 자동 사냥만 합니다.\n꺼짐: 퀘스트를 다시 진행합니다.")
        self.btn_farm.clicked.connect(self.toggle_farm)
        self.btn_normal = Toggle("일반 모드", on_color=C["accent"], off_color=C["idle"])
        self.btn_normal.setToolTip("현재 위치에서 자동 사냥합니다. HP가 낮아지면 추천 사냥터로 무료 피신하고 회복 후 저장한 원래 지역으로 복귀합니다.")
        self.btn_normal.clicked.connect(self.toggle_normal)
        self.btn_daily = Toggle("일일 전용", on_color=C["warn"], off_color=C["idle"])
        self.btn_daily.setToolTip("본편·서사·주간은 건드리지 않고 일일 임무만 진행합니다. 메인 목록 전체에서 [일일]이 연속 두 번 사라지면 앵벌 모드로 자동 전환합니다.")
        self.btn_daily.clicked.connect(self.toggle_daily)
        self.btn_story = Toggle("서사 전용", on_color=C["accent"], off_color=C["idle"])
        self.btn_story.setToolTip("본편·일일·주간은 건드리지 않고 현재 전투력에 맞는 '적합' 서사 임무만 수락하고 자동 진행합니다.")
        self.btn_story.clicked.connect(self.toggle_story)
        self._set_checked_quiet(self.btn_farm, bool(self.saved.get(
            "farm/enabled", get_path(self.base_config, ("farm", "enabled"), False))))
        self._set_checked_quiet(self.btn_normal, bool(self.saved.get(
            "normal_mode/enabled", get_path(self.base_config, ("normal_mode", "enabled"), True))))
        self._set_checked_quiet(self.btn_daily, bool(self.saved.get(
            "daily_mode/enabled", get_path(self.base_config, ("daily_mode", "enabled"), False))))
        self._set_checked_quiet(self.btn_story, bool(self.saved.get(
            "story_mode/enabled", get_path(self.base_config, ("story_mode", "enabled"), False))))
        self.btn_detect = Toggle("조작 감지", on_color=C["accent"], off_color=C["idle"])
        self.btn_detect.setToolTip("켜짐: 마우스·키보드를 쓰면 봇이 2분간 양보합니다.\n꺼짐: 마우스·키보드를 써도 봇이 멈추지 않습니다 (봇의 클릭과 겹칠 수 있으니 이동·등록 중에는 주의).")
        self.btn_detect.clicked.connect(lambda _checked=False: self.toggle_detect())
        self.user_detect = bool(self.saved.get("_user_detect", True))
        self._refresh_detect_button()
        self._set_pause_label(False)
        for b in (self.btn_start, self.btn_pause, self.btn_stop, self.btn_potion, self.btn_evade):
            b.setMinimumHeight(36)
            b.setIconSize(icons.ICON_SIZE)
            b.setCursor(Qt.PointingHandCursor)
        for b in (self.btn_start, self.btn_pause, self.btn_stop):
            row.addWidget(b)
        self.btn_start.setToolTip("저장된 설정으로 자동 진행을 시작합니다")
        self.btn_pause.setToolTip("봇 입력을 잠시 멈추거나 재개합니다 · F11")
        self.btn_stop.setToolTip("자동 진행을 종료합니다 · F12")
        header.addLayout(row)
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        actions = QHBoxLayout(toolbar)
        actions.setContentsMargins(12, 7, 12, 7)
        for t in (self.btn_daily, self.btn_story, self.btn_normal, self.btn_farm, self.btn_detect):
            actions.addWidget(t)
        actions.addStretch()
        actions.addWidget(self.btn_potion)
        actions.addWidget(self.btn_evade)
        header.addWidget(toolbar)
        return header

    def _build_dashboard(self):
        w = QWidget()
        w.setMinimumWidth(0)
        w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)

        intro = QLabel("지금 진행 상황")
        intro.setObjectName("pageTitle")
        lay.addWidget(intro)
        top = QHBoxLayout()
        top.setSpacing(12)
        c1, l1 = card("현재 상태")
        self.lbl_state = QLabel("-")
        self.lbl_state.setObjectName("big")
        self.lbl_state_sub = QLabel("매크로가 정지되어 있습니다")
        self.lbl_state_sub.setObjectName("sub")
        self.lbl_state_sub.setWordWrap(True)
        l1.addWidget(self.lbl_state)
        l1.addWidget(self.lbl_state_sub)
        top.addWidget(c1, 3)

        c2, l2 = card("게임 창")
        self.lbl_window = QLabel("-")
        self.lbl_window.setObjectName("big")
        self.lbl_window_sub = QLabel("")
        self.lbl_window_sub.setObjectName("sub")
        self.lbl_window_sub.setWordWrap(True)
        l2.addWidget(self.lbl_window)
        l2.addWidget(self.lbl_window_sub)
        top.addWidget(c2, 2)

        c3, l3 = card("진행 중인 퀘스트 출처")
        self.lbl_source = QLabel("-")
        self.lbl_source.setObjectName("big")
        self.lbl_source_sub = QLabel("")
        self.lbl_source_sub.setObjectName("sub")
        self.lbl_source_sub.setWordWrap(True)
        l3.addWidget(self.lbl_source)
        l3.addWidget(self.lbl_source_sub)
        top.addWidget(c3, 2)
        lay.addLayout(top)

        # 실시간 상황판: 동기화 · 현재 위치 · 잡는 몹 · 하는 퀘스트
        live = QGridLayout()
        live.setSpacing(12)
        self.live_lbl = {}
        for column in range(3):
            live.setColumnStretch(column, 1)
        for index, (key, title) in enumerate((("map", "현재 위치"), ("power", "내 전투력"), ("potions", "HP 물약"),
                                             ("quest", "진행 중인 퀘스트"), ("mob", "전투 대상"), ("sync", "화면 인식"))):
            c, l = card(title)
            c.setMinimumWidth(0)
            c.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            v = QLabel("-")
            v.setObjectName("big")
            v.setWordWrap(True)
            v.setMinimumHeight(24)
            if key in ("map", "mob", "quest"):
                v.setStyleSheet("font-size:13px;")             # 지도·몹·퀘스트 이름은 길어서 더 작게
            sub_l = QLabel("")
            sub_l.setObjectName("sub")
            sub_l.setWordWrap(True)
            l.addWidget(v)
            l.addWidget(sub_l)
            self.live_lbl[key] = (v, sub_l)
            if key == "map":
                self.hp_bar = QProgressBar()
                self.hp_bar.setRange(0, 100)
                self.hp_bar.setTextVisible(False)
                self.hp_bar.setValue(0)
                self.hp_bar.hide()
                l.addWidget(self.hp_bar)
            live.addWidget(c, index // 3, index % 3)
        lay.addLayout(live)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.chips = {}
        for key in ("IDLE", "MOVING", "COMBAT", "DIALOG", "OVERLAY", "DEAD"):
            self.chips[key] = StateChip(key)
            chips.addWidget(self.chips[key], 1)
        lay.addLayout(chips)

        self.daily_card, daily_layout = card("일일 퀘스트 선택")
        daily_head = QHBoxLayout()
        self.btn_daily_scan = QPushButton("목록 불러오기")
        self.btn_daily_scan.setToolTip("게임의 임무 화면을 열어 자동 수행 가능한 일일 퀘스트를 읽습니다")
        self.btn_daily_scan.clicked.connect(self._request_daily_scan)
        self.btn_daily_selected = QPushButton("체크한 퀘스트 진행")
        self.btn_daily_selected.setObjectName("start")
        self.btn_daily_selected.setEnabled(False)
        self.btn_daily_selected.clicked.connect(self._start_selected_daily)
        daily_head.addWidget(self.btn_daily_scan)
        daily_head.addWidget(self.btn_daily_selected)
        daily_head.addStretch(1)
        daily_layout.addLayout(daily_head)
        self.lbl_daily_picker = QLabel("목록 불러오기를 누른 뒤 진행할 퀘스트를 체크하세요")
        self.lbl_daily_picker.setObjectName("sub")
        self.lbl_daily_picker.setWordWrap(True)
        daily_layout.addWidget(self.lbl_daily_picker)
        self.daily_quest_list = QWidget()
        self.daily_quest_layout = QVBoxLayout(self.daily_quest_list)
        self.daily_quest_layout.setContentsMargins(0, 4, 0, 0)
        self.daily_quest_layout.setSpacing(4)
        daily_layout.addWidget(self.daily_quest_list)
        self.daily_quest_checks = {}
        self._daily_picker_revision = -1
        remembered_catalog = self.saved.get("_daily_quest_catalog", [])
        if not remembered_catalog:
            # 이전 버전에서 저장한 체크 목록도 첫 1회 복원 자료로 활용한다.
            remembered_catalog = self.saved.get("_daily_selected_titles", [])
        if remembered_catalog:
            self._show_daily_quests(remembered_catalog)
            self.lbl_daily_picker.setText(
                f"최근 불러온 일일 퀘스트 {len(remembered_catalog)}개 · 목록 불러오기 전까지 유지")
        lay.addWidget(self.daily_card)
        self._sync_daily_picker_visibility()

        schedule_card, schedule_layout = card("보스·이벤트 일정")
        self.lbl_schedule = QLabel("")
        self.lbl_schedule.setObjectName("sub")
        self.lbl_schedule.setWordWrap(True)
        self.lbl_schedule.setTextInteractionFlags(Qt.TextSelectableByMouse)
        schedule_layout.addWidget(self.lbl_schedule)
        lay.addWidget(schedule_card)

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
        self.lbl_recent.setWordWrap(True)
        self.lbl_recent.setMinimumWidth(0)
        self.lbl_recent.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        l4.addWidget(self.lbl_recent, 1)
        lay.addWidget(c4, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        return scroll

    def _build_settings(self):
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(12)
        self.settings_rows = []
        self.settings_groups = []
        groups = {}
        for name in ("진행과 사냥", "회복과 안전", "생활과 알림", "고급 동작"):
            box = QGroupBox(name)
            grid = QGridLayout(box)
            grid.setColumnStretch(1, 1)
            grid.setVerticalSpacing(10)
            groups[name] = (box, grid)
        g2 = QGroupBox("수치")
        gl2 = QGridLayout(g2)
        gl2.setVerticalSpacing(8)
        r1 = r2 = 0
        for kind, path, label, extra in SETTING_FIELDS:
            saved = self.saved.get("/".join(path))
            if kind == "toggle":
                group_name = "진행과 사냥" if path[0] in ("farm", "collect", "autohunt") else "회복과 안전" if path[0] in ("hp", "potion_shop", "yield_only_game_input") else "생활과 알림" if path[0] in ("notify", "rewards", "yeonggi") else "고급 동작"
                box, gl1 = groups[group_name]
                row = gl1.rowCount()
                cb = Toggle(label, on_color=C["accent"], off_color=C["idle"])
                default = path == ("hp", "startup_risk_check")
                cb.setChecked(bool(saved if saved is not None else get_path(self.base_config, path, default)))
                hint = QLabel(extra)
                hint.setObjectName("sub")
                hint.setWordWrap(True)
                cb.setToolTip(extra)
                gl1.addWidget(cb, row, 0)
                gl1.addWidget(hint, row, 1)
                self.settings_rows.append((box, label + extra, (cb, hint)))
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
                label_widget = QLabel(label)
                label_widget.setWordWrap(True)
                gl2.addWidget(label_widget, r2, 0)
                gl2.addWidget(sp, r2, 1)
                gl2.addWidget(u, r2, 2)
                self.controls[path] = sp
                self.settings_rows.append((g2, label, (label_widget, sp, u)))
                r2 += 1
        gl2.setColumnStretch(3, 1)
        from gui_extra import ProfileBar
        self.profile_bar = ProfileBar(self._collect_values, self._apply_values)
        lay.addWidget(self.profile_bar)
        for box, _ in groups.values():
            lay.addWidget(box)
            self.settings_groups.append(box)
        lay.addWidget(g2)
        self.settings_groups.append(g2)

        theme_box = QGroupBox("화면 테마")
        theme_layout = QGridLayout(theme_box)
        theme_label = QLabel("테마")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("어두운 테마", "dark")
        self.theme_combo.addItem("밝은 테마", "light")
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self._theme_name))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_layout.addWidget(theme_label, 0, 0)
        theme_layout.addWidget(self.theme_combo, 0, 1)
        theme_layout.setColumnStretch(2, 1)
        self.settings_rows.append((theme_box, "화면 테마 어두운 테마 밝은 테마", (theme_label, self.theme_combo)))
        lay.addWidget(theme_box)
        self.settings_groups.append(theme_box)

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
            self.settings_rows.append((gt, label, (glt.itemAtPosition(i, 0).widget(), le)))
        tip = QLabel("텔레그램: @BotFather로 봇 생성 → 봇에게 아무 말 → api.telegram.org/bot(토큰)/getUpdates 에서 chat id 확인. "
                     "원격 명령: /상태 /화면 /일시정지 /재개 /정지")
        tip.setObjectName("sub")
        tip.setWordWrap(True)
        glt.addWidget(tip, len(TEXT_FIELDS), 0, 1, 2)
        lay.addWidget(gt)
        self.settings_groups.append(gt)

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
        schedule_hint = QLabel("예약 변경은 설정 저장 후 반영됩니다. 실행 중에도 예약 정지 시간을 저장할 수 있습니다.")
        schedule_hint.setObjectName("sub")
        gls.addWidget(schedule_hint, 2, 0, 1, 4)

        g3 = QGroupBox("실행 옵션")
        gl3 = QVBoxLayout(g3)
        self.chk_dry = Toggle("연습 모드 (클릭/키 입력 없이 감지 결과만 로그로 확인)", on_color=C["accent"], off_color=C["idle"])
        self.chk_dry.setChecked(bool(self.saved.get("_dry", False)))
        self.chk_debug = Toggle("디버그 로그 (화면 변화량 등 상세 출력)", on_color=C["accent"], off_color=C["idle"])
        self.chk_debug.setChecked(bool(self.saved.get("_debug", False)))
        gl3.addWidget(self.chk_dry)
        gl3.addWidget(self.chk_debug)
        lay.addWidget(g3)
        self.settings_sections = [(self.profile_bar, "프로필 설정 묶음 불러오기 저장"),
                                  (gs, "예약 실행 자동 시작 정지 시간"), (g3, "실행 옵션 연습 모드 디버그 로그")]

        from version import CHANGELOG, VERSION
        g4 = QGroupBox(f"버전 정보  ·  v{VERSION}")
        gl4 = QVBoxLayout(g4)
        for ver, date, note in CHANGELOG[:6]:
            lb = QLabel(f"v{ver}  ({date})  {note}")
            lb.setObjectName("sub")
            lb.setWordWrap(True)
            gl4.addWidget(lb)
        lay.addWidget(g4)
        self.settings_sections.append((g4, "버전 정보 업데이트 변경 이력"))

        note = QLabel("※ 설정은 다음 '시작'부터 적용됩니다. 실행 중 변경하려면 정지 후 다시 시작하세요.")
        note.setObjectName("sub")
        lay.addWidget(note)
        lay.addStretch(1)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 8, 0, 0)
        search_row = QHBoxLayout()
        self.settings_search = QLineEdit()
        self.settings_search.setPlaceholderText("설정 검색 · 물약, 사냥, 회복…  (Ctrl+K)")
        self.settings_search.setClearButtonEnabled(True)
        self.settings_search.textChanged.connect(self._filter_settings)
        search_row.addWidget(self.settings_search, 1)
        self.btn_save_settings = QPushButton("설정 저장")
        self.btn_save_settings.setIcon(icons.make("save", C["accent"]))
        self.btn_save_settings.clicked.connect(self._save_settings)
        self.btn_save_settings.setEnabled(False)
        search_row.addWidget(self.btn_save_settings)
        self.btn_backup_settings = QPushButton("백업")
        self.btn_backup_settings.clicked.connect(self._backup_settings)
        self.btn_backup_settings.setToolTip("현재 설정을 파일로 저장합니다. 알림 비밀값은 제외합니다.")
        self.btn_restore_settings = QPushButton("복원")
        self.btn_restore_settings.clicked.connect(self._restore_settings)
        search_row.addWidget(self.btn_backup_settings)
        search_row.addWidget(self.btn_restore_settings)
        page_layout.addLayout(search_row)
        self.settings_status = QLabel("저장된 설정 · 다음 시작부터 적용")
        self.settings_status.setObjectName("sub")
        page_layout.addWidget(self.settings_status)
        self.settings_empty = QLabel("일치하는 설정이 없습니다. 다른 검색어를 입력하세요.")
        self.settings_empty.setObjectName("sub")
        self.settings_empty.hide()
        page_layout.addWidget(self.settings_empty)
        page_layout.addWidget(scroll, 1)
        return page

    def _filter_settings(self, query):
        query = query.strip().casefold()
        visible = set()
        for group, text, widgets in self.settings_rows:
            match = not query or query in (group.title() + text).casefold()
            for widget in widgets:
                widget.setVisible(match)
            if match:
                visible.add(group)
        for group in self.settings_groups:
            group.setVisible(group in visible)
        found = bool(visible)
        for group, text in self.settings_sections:
            match = not query or query in text.casefold()
            group.setVisible(match)
            found = found or match
        self.settings_empty.setVisible(not found)

    def _build_recommend(self):
        """추천 탭: 전투·성장 추천과 사용자가 직접 진행하는 보물찾기 안내."""
        sub_tabs = QTabWidget()
        sub_tabs.setObjectName("recommendTabs")
        sub_tabs.setDocumentMode(True)
        sub_tabs.addTab(self._build_skills(), "스킬")
        sub_tabs.addTab(self._build_skills(f2p_only=True), "무과금")
        sub_tabs.addTab(self._build_owned_skills(), "보유 스킬")
        sub_tabs.addTab(self._build_purchase_guide(), "구매 추천")
        from gui_extra import load_power
        from treasure_widget import TreasureGuideWidget
        history = load_power(1)
        initial_power = int(history[-1][1]) if history else 0
        self.treasure_guide = TreasureGuideWidget(C, initial_power)
        sub_tabs.addTab(self.treasure_guide, "보물찾기")
        return sub_tabs

    def _build_owned_skills(self):
        """전체 도술 아이콘을 보여 주고 사용자가 보유 목록을 직접 관리하게 한다."""
        from skill_simulator import owned_skill_names

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        tools = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("도술 이름 또는 계열 검색")
        tools.addWidget(search, 1)
        select_all = QPushButton("전체 선택")
        clear_all = QPushButton("전체 해제")
        save_button = QPushButton("저장 후 추천 다시 계산")
        save_button.setObjectName("primary")
        tools.addWidget(select_all)
        tools.addWidget(clear_all)
        tools.addWidget(save_button)
        outer.addLayout(tools)

        self.skill_owned_status = QLabel()
        self.skill_owned_status.setObjectName("sub")
        outer.addWidget(self.skill_owned_status)

        try:
            with open(os.path.join(ROOT, "assets", "skills", "catalog.json"), encoding="utf-8") as stream:
                catalog = json.load(stream)
            with open(os.path.join(ROOT, "assets", "skills", "index.json"), encoding="utf-8") as stream:
                icons_by_name = json.load(stream)
        except (OSError, ValueError):
            catalog, icons_by_name = {"skills": []}, {}

        selected = set(owned_skill_names())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(4, 8, 4, 8)
        rows.setSpacing(8)
        self.skill_owned_checks = {}
        self.skill_owned_cards = {}
        self.skill_owned_rows = {}
        self.skill_owned_order = {}
        self.skill_owned_regulars = {}
        self.skill_owned_ultimates = {}
        skills = catalog.get("skills", [])
        style_order = catalog.get("styles", []) or list(dict.fromkeys(skill.get("style", "") for skill in skills))
        for style in style_order:
            style_skills = [skill for skill in skills if skill.get("style") == style]
            if not style_skills:
                continue
            preferred = {name: index for index, name in enumerate(SKILL_DISPLAY_ORDER.get(style, []))}
            original = {skill["name"]: index for index, skill in enumerate(style_skills)}
            style_skills.sort(
                key=lambda skill: preferred.get(
                    skill["name"], len(preferred) + original[skill["name"]]
                )
            )
            self.skill_owned_order[style] = [skill["name"] for skill in style_skills]
            row_widget = QFrame()
            row_widget.setObjectName("card")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(8)
            style_label = QLabel(style)
            style_label.setObjectName("sectionTitle")
            style_label.setFixedWidth(54)
            style_label.setAlignment(Qt.AlignCenter)
            row_layout.addWidget(style_label)
            for skill in style_skills:
                name = skill["name"]
                filename = icons_by_name.get(name)
                icon_path = os.path.join(ROOT, "assets", "skills", filename) if filename else ""
                check = OwnedSkillIcon(name, icon_path)
                suffix = " · 궁극기" if skill.get("ultimate") else ""
                check.setToolTip(
                    f"<b>{html.escape(name)}</b>{suffix}<br>"
                    f"{html.escape(style)} · {html.escape(str(skill.get('description', '') or '설명 없음'))}")
                check.setChecked(name in selected)
                check.toggled.connect(self._update_owned_skill_count)
                row_layout.addWidget(check)
                self.skill_owned_checks[name] = check
                self.skill_owned_cards[name] = (check, style)
                if skill.get("ultimate"):
                    check.setEnabled(False)
                    check.setCursor(Qt.ArrowCursor)
                    self.skill_owned_ultimates[style] = check
                else:
                    self.skill_owned_regulars.setdefault(style, []).append(check)
            row_layout.addStretch(1)
            rows.addWidget(row_widget)
            self.skill_owned_rows[style] = row_widget
        rows.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        def set_all(value):
            for checkboxes in self.skill_owned_regulars.values():
                for checkbox in checkboxes:
                    checkbox.setChecked(value)

        def filter_skills(text):
            query = text.strip().casefold()
            visible_styles = set()
            for name, (widget, style) in self.skill_owned_cards.items():
                match = not query or query in name.casefold() or query in style.casefold()
                widget.setVisible(match)
                if match:
                    visible_styles.add(style)
            for style, widget in self.skill_owned_rows.items():
                widget.setVisible(style in visible_styles)

        select_all.clicked.connect(lambda: set_all(True))
        clear_all.clicked.connect(lambda: set_all(False))
        search.textChanged.connect(filter_skills)
        save_button.clicked.connect(self._save_owned_skills)
        self._update_owned_skill_count()
        return page

    def _update_owned_skill_count(self, *_):
        if not hasattr(self, "skill_owned_status"):
            return
        for style, ultimate in getattr(self, "skill_owned_ultimates", {}).items():
            regular_count = sum(box.isChecked() for box in self.skill_owned_regulars.get(style, []))
            ultimate.blockSignals(True)
            ultimate.setChecked(regular_count >= 3)
            ultimate.blockSignals(False)
        total = len(getattr(self, "skill_owned_checks", {}))
        regular_checked = sum(
            box.isChecked() for boxes in getattr(self, "skill_owned_regulars", {}).values() for box in boxes
        )
        ultimate_checked = sum(box.isChecked() for box in getattr(self, "skill_owned_ultimates", {}).values())
        self.skill_owned_status.setText(
            f"보유 일반 {regular_checked}개 · 자동 궁극기 {ultimate_checked}개 · 전체 도술 {total}개"
        )

    def _save_owned_skills(self):
        selected = sorted(
            name for name, box in self.skill_owned_checks.items()
            if box.isChecked() and all(box is not ultimate for ultimate in self.skill_owned_ultimates.values())
        )
        data = dict(self.saved)
        data["_owned_skills"] = selected
        try:
            self._persist_saved(data)
            self.saved = data
        except OSError as error:
            self._notify("보유 스킬을 저장하지 못했습니다: " + str(error), True)
            return
        self.skill_owned_status.setText(f"보유 {len(selected)}개 저장 · 추천 재계산 시작")
        self._pending_owned_recalculation = True
        self._start_skill_learning(force="owned")

    def _build_purchase_guide(self):
        """현자 단각 상점: 현재 보유 덱을 기준으로 요령서 구매 순위를 보여 준다."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("보유 현자 단각"))
        self.spin_sage_shards = QSpinBox()
        self.spin_sage_shards.setRange(0, 999_999)
        self.spin_sage_shards.setSingleStep(100)
        self.spin_sage_shards.setGroupSeparatorShown(True)
        self.spin_sage_shards.setValue(5300)
        self.spin_sage_shards.setSuffix("개")
        self.spin_sage_shards.valueChanged.connect(self._show_purchase_guide)
        bar.addWidget(self.spin_sage_shards)
        note = QLabel("도술 강화 요령서 1개 100 · 계열당 일일 최대 10개")
        note.setObjectName("sub")
        bar.addWidget(note)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.purchase_guide = self._guide_browser()
        layout.addWidget(self.purchase_guide, 1)
        self._show_purchase_guide()
        return page

    def _show_purchase_guide(self):
        from html import escape
        import json
        from skill_simulator import (community_style_summary, family_purchase_priority,
                                     owned_skill_names)
        sdir = os.path.join(ROOT, "assets", "skills")
        try:
            with open(os.path.join(sdir, "catalog.json"), encoding="utf-8") as f:
                catalog = json.load(f)
            with open(os.path.join(sdir, "simulation_results.json"), encoding="utf-8") as f:
                simulation = json.load(f)
            rows = family_purchase_priority(catalog, simulation)
        except (OSError, ValueError, KeyError):
            self.purchase_guide.setHtml("<h2>구매 추천 계산 자료를 불러오지 못했습니다.</h2>")
            return
        budget = self.spin_sage_shards.value()
        useful = [row for row in rows if row["score"] > 0]
        core = useful[:3]
        core_cost = len(core) * 1000
        remaining = max(0, budget - min(budget, core_cost))
        h = (f"<body style='color:{C['text']}'><h2>현자 단각 구매 추천</h2>"
             f"<p>현재 보유 최고 광역·단일 덱을 다시 계산한 결과입니다. "
             f"<b style='color:{C['accent']}'>{escape(' → '.join(row['style'] for row in useful[:5]))}</b> 순입니다.</p>")
        if budget >= core_cost:
            h += (f"<table width='100%' cellpadding=10 style='background:{C['panel2']}'><tr><td>"
                  f"<b style='color:{C['ok']}'>오늘 핵심 구매</b> · "
                  f"{escape(', '.join(row['style'] + ' 요령서 10개' for row in core))}<br>"
                  f"사용 {core_cost:,}개 · 남음 {remaining:,}개</td></tr></table>")
        else:
            buy_count = budget // 100
            first = useful[0]["style"] if useful else "보류"
            h += (f"<table width='100%' cellpadding=10 style='background:{C['panel2']}'><tr><td>"
                  f"<b style='color:{C['warn']}'>현재 예산 추천</b> · {escape(first)} 요령서 "
                  f"{min(10, buy_count)}개부터 구매</td></tr></table>")
        h += ("<h2>상점 구매 우선순위</h2><table width='100%' cellpadding=7>"
              "<tr><th align=left>순위·상품</th><th align=left>강화되는 현재 도술</th>"
              "<th align=right>광역 상승</th><th align=right>단일 상승</th><th align=left>판정</th></tr>")
        for row in rows:
            skills = list(dict.fromkeys(row["aoe_skills"] + row["single_skills"] + row["ultimates"]))
            if row["rank"] <= 3:
                verdict, color = "10개 우선 구매", C["ok"]
            elif row["score"] > 0:
                verdict, color = "주력 전환 시 구매", C["warn"]
            else:
                verdict, color = "현재 덱은 보류", C["sub"]
            h += (f"<tr><td><b style='color:{C['accent']}'>{row['rank']}</b> · "
                  f"{escape(row['style'])} 도술 강화 요령서<br><span style='color:{C['sub']}'>100 단각</span></td>"
                  f"<td>{escape(', '.join(skills) if skills else '현재 추천 덱에 없음')}</td>"
                  f"<td align=right>+{row['aoe_gain']:.3f}%</td>"
                  f"<td align=right>+{row['single_gain']:.3f}%</td>"
                  f"<td style='color:{color}'><b>{verdict}</b></td></tr>")
        h += "</table>"

        kido = community_style_summary(catalog, "기동", owned_skill_names())
        if kido:
            popular = kido[0]
            best = max(kido, key=lambda row: row["aoe_score"])
            current_aoe = float(simulation.get("current", {}).get("aoe", [{}])[0].get("score", 0))
            h += (f"<h2>깨비지지 기동덱 비교</h2><p><b>기동덱이 나쁜 것은 아닙니다.</b> "
                  "이동·몹몰이·생존이 좋아 실제 분당 처치에서 유리할 수 있지만, 현재 계산기는 제자리 60초 피해 중심입니다.<br>"
                  f"인기 1위: {escape(popular['name'])} · 추천 {popular['likes']} · "
                  f"광역 {popular['aoe_score']:.2f}<br>"
                  f"기동덱 계산 최고: {escape(best['name'])} · 광역 {best['aoe_score']:.2f} · "
                  f"현재 보유 최고 {current_aoe:.2f}<br>"
                  f"현재 미보유 핵심: {escape(', '.join(best['missing']) if best['missing'] else '없음')}</p>")
            if best["missing"]:
                h += (f"<p style='color:{C['warn']}'>현재는 핵심 기동 도술이 부족하므로 기동 요령서 구매는 보류하고, "
                      "해당 도술을 확보한 뒤 다시 계산하는 편이 효율적입니다.</p>")
        h += (f"<p style='color:{C['sub']}'>상승률은 각 계열 기본 공격계수가 동일하게 1% 증가한다고 가정한 비교입니다. "
              "정수·가호 주머니·강화 비법서는 계정별 재료 병목을 확인할 수 없어 순위에서 제외했습니다.</p></body>")
        self.purchase_guide.setHtml(h)

    def _guide_browser(self):
        tb = InstantTooltipBrowser()
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
        btn = QPushButton("사냥터 추천")
        btn.setIcon(icons.make("map", C["accent"]))
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
        bot = self.thread.bot if self.thread else None
        margin = float(get_path(bot.cfg, ("farm", "margin"), 1.5)) if bot else self.controls[("farm", "margin")].value()
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
                  f"<span style='color:{C['ok']}'>앵벌 허용 범위의 사냥터</span>: {fmt(r['macro'])} "
                  f"<span style='color:{C['sub']}'>— 전투력의 {margin * 100:.0f}% 이하</span><br>"
                  f"길라잡이 무료 순간이동 후보: {fmt(g.recommend_hunt(power, min(margin, 1.0))['macro'])}<br>"
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
                  "앵벌 허용 범위는 설정의 전투력 비율을 따릅니다. 길라잡이 무료 순간이동은 내 전투력 이하만 가능하며, 1.5배 구역으로 걸어 올라가는 기능은 아직 없습니다.</p>"
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

    def _build_skills(self, f2p_only=False):
        """무과금 도술 추천 (skill_guide.py 데이터)."""
        import skill_guide as g
        from html import escape
        import json
        tb = InstantTooltipBrowser()
        sdir = os.path.join(ROOT, "assets", "skills")
        try:
            with open(os.path.join(sdir, "index.json"), encoding="utf-8") as f:
                icons = json.load(f)
        except OSError:
            icons = {}
        try:
            with open(os.path.join(sdir, "catalog.json"), encoding="utf-8") as f:
                catalog = json.load(f)
            skill_details = {row["name"]: row for row in catalog.get("skills", [])}
        except (OSError, ValueError):
            catalog = {"skills": []}
            skill_details = {}
        try:
            with open(os.path.join(sdir, "styles", "index.json"), encoding="utf-8") as f:
                style_icons = json.load(f)
        except (OSError, ValueError):
            style_icons = {}
        style_colors = catalog.get("style_colors", {})
        bright_style_colors = {
            "절명": "#FF8C85", "연타": "#FF9A76", "강타": "#FFD166",
            "척살": "#FF8FBC", "저격": "#75B7FF", "주술": "#C2A5FF",
            "파멸": "#9DADFF", "강인": "#7FE293", "기동": "#62DCC4",
            "헌신": "#E8D76F", "기만": "#D8A7EA", "제압": "#C5D0DC",
        }

        def icon_html(name, size=40):
            # 표의 '(궁)' 같은 설명 접미사는 허용하되 비슷한 이름끼리
            # 잘못 연결되지 않도록 정확한 이름을 먼저 찾는다.
            fn = icons.get(name)
            if fn is None:
                matches = [(k, v) for k, v in icons.items() if name.startswith(k + "(")]
                if matches:
                    fn = max(matches, key=lambda item: len(item[0]))[1]
            if fn:
                url = "file:///" + os.path.join(sdir, fn).replace("\\", "/")
                return f"<img src='{url}' width={size} height={size}>"
            return (f"<table cellspacing=0><tr><td width={size} height={size} align=center "
                    f"style='background:{C['panel2']};color:{C['idle']}'>?</td></tr></table>")

        def tooltip_html(name):
            detail = skill_details.get(name, {})
            if not detail:
                matches = [(key, value) for key, value in skill_details.items() if name.startswith(key + "(")]
                if matches:
                    detail = max(matches, key=lambda item: len(item[0]))[1]
            style = str(detail.get("style") or "미확인")
            roles = " · ".join(detail.get("roles") or []) or "일반"
            targets = int(detail.get("targets") or 1)
            coefficient = float(detail.get("coefficient") or 0)
            cooldown = detail.get("cooldown")
            effects = str(detail.get("effects") or "특수 효과 없음")
            effect_types = []
            for effect in detail.get("effect_details", []):
                kind = str(effect.get("type") or "효과")
                effect_name = str(effect.get("name") or "").strip()
                if effect_name:
                    effect_types.append(f"{kind}: {effect_name}")
            effect_type_text = " · ".join(effect_types)
            try:
                from skill_simulator import AOE_HIT_FACTORS, _aoe_reach_factor
                hit_factor = AOE_HIT_FACTORS.get(detail.get("name"))
                reach_factor = _aoe_reach_factor(detail)
            except ImportError:
                hit_factor = None
                reach_factor = 1.0
            hit_note = (f"<br>광역 실전 보정: 직선형 ×{hit_factor:.2f}"
                        if hit_factor is not None else "")
            return (f"<qt>유형: <span style=\"color:{C['accent']};font-weight:700\">"
                    f"{escape(style)}</span> · {escape(roles)}<br>도술: {escape(name)}<br>"
                    f"대상: {'범위 최대 ' + str(targets) + '인' if targets > 1 else '단일'}<br>"
                    f"사거리: {float(detail.get('range') or 0):.1f}m"
                    f"{' · 반경 ' + format(float(detail.get('radius')), '.1f') + 'm' if detail.get('radius') else ''}"
                    f"{' · 흡인/몹몰이' if detail.get('pull') else ''}<br>"
                    f"광역 사거리 보정: ×{reach_factor:.2f}<br>"
                    f"기본 공격계수: {coefficient:.2f}%<br>"
                    f"재사용 대기시간: {cooldown}초{hit_note}<br>"
                    f"{('공식 분류: ' + escape(effect_type_text) + '<br>') if effect_type_text else ''}"
                    f"효과: {escape(effects)}</qt>")

        def icon_with_tooltip(name, size=40):
            tooltip = tooltip_html(name)
            return (f"<span title='{escape(tooltip, quote=True)}' style='cursor:help'>"
                    f"{icon_html(name, size)}</span>")

        def style_header(name):
            detail = skill_details.get(name, {})
            style = str(detail.get("style") or "미확인")
            filename = style_icons.get(style)
            image = ""
            if filename:
                path = "file:///" + os.path.join(sdir, "styles", filename).replace("\\", "/")
                image = f"<img src='{path}' width=14 height=14> "
            # 원본 계열색은 어두운 배경에서 거의 보이지 않아, 같은 색조의 밝은 팔레트를 사용한다.
            color = bright_style_colors.get(style) or style_colors.get(style) or C["text"]
            return f"{image}<span style='font-size:12px;color:{color}'><b>{escape(style)}</b></span><br>"

        def skill_badge(name, size=40, font_size=13):
            tooltip = tooltip_html(name)
            return (f"<span title='{escape(tooltip, quote=True)}' style='cursor:help'>"
                    f"{style_header(name)}{icon_html(name, size)}<br>"
                    f"<span style='font-size:{font_size}px'>{escape(name)}</span></span>")

        def skill_row(names, extra_cells=""):
            cells = ""
            for n in names:
                cells += (f"<td align=center style='padding:4px'>"
                          f"{skill_badge(n)}</td>")
            return f"<table cellspacing=2><tr>{cells}{extra_cells}</tr></table>"

        ultimate_names = [row["name"] for row in catalog.get("skills", []) if row.get("ultimate")]

        def ultimate_cell(value):
            """일반 도술 8칸과 분리해 궁극기를 제목 위·아이콘 아래 구조로 표시한다."""
            value = str(value or "")
            name = next((candidate for candidate in ultimate_names if candidate in value), "")
            if not name:
                return (f"<td align=center style='border-left:1px solid {C['line']};padding-left:12px'>"
                        f"<b style='color:{C['accent']}'>궁</b><br>미활성</td>")
            return (f"<td align=center style='border-left:1px solid {C['line']};padding-left:12px'>"
                    f"<b style='color:{C['accent']}'>궁</b><br>{skill_badge(name)}</td>")

        def heal_swap_cells(names, mode, score=0, unit=""):
            """추천 도술 행의 오른쪽 구획에 체력 부족 시 교체할 도술을 표시한다."""
            heal_name = "생명의 축복"
            if heal_name in names:
                return (f"<td style='border-left:1px solid {C['line']};padding-left:12px;"
                        f"color:{C['ok']}'><b>HP 부족 대응<br>힐 포함</b></td>"
                        f"<td align=center>{skill_badge(heal_name, 30, 12)}</td>")
            try:
                from skill_simulator import _heal_swap
                swap = _heal_swap(catalog, {"skills": list(names), "score": score}, mode)
            except (ImportError, KeyError, TypeError, ValueError):
                swap = None
            if not swap:
                return ""
            remove, add = swap["remove"], swap["add"]
            damage_unit = unit or ("%공격력·대상/초" if mode == "aoe" else "%공격력/초")
            ultimate_text = (f"궁극기 {swap.get('ultimate')} 유지"
                             if swap.get("keeps_ultimate") and swap.get("ultimate")
                             else f"궁극기 {swap.get('original_ultimate') or '미활성'} → "
                                  f"{swap.get('ultimate') or '미활성'}")
            return (f"<td style='border-left:1px solid {C['line']};padding-left:12px;"
                    f"color:{C['warn']}'><b>HP 부족 시 교체</b></td>"
                    f"<td align=center>{skill_badge(remove, 30, 12)}</td>"
                    f"<td style='font-size:20px;color:{C['accent']}'>→</td>"
                    f"<td align=center>{skill_badge(add, 30, 12)}</td>"
                    f"<td style='padding-left:10px'><span style='font-size:12px;color:{C['sub']}'>"
                    f"이유: 8개 교체안 중 데미지 손실 최소<br>"
                    f"예상 데미지: {swap['original_score']:.2f} → {swap['score']:.2f} "
                    f"{escape(damage_unit)} <span style='color:{C['bad']}'>"
                    f"(-{swap['loss_percent']:.1f}%)</span><br>{escape(ultimate_text)}</span></td>")

        def recommendation_row(names, mode, score=0, unit="", ultimate=None):
            return skill_row(names, ultimate_cell(ultimate) + heal_swap_cells(names, mode, score, unit))

        def sequence_row(names, mode, ultimate):
            ordered = []
            try:
                from skill_simulator import _recommended_order
                details = [skill_details[name] for name in names if name in skill_details]
                ordered = [row["name"] for row in _recommended_order(details, mode)]
            except (ImportError, KeyError, TypeError):
                ordered = list(names)
            if len(ordered) != len(names):
                ordered = list(names)
            cells = []
            for position, name in enumerate(ordered, 1):
                cells.append(f"<td align=center><span style='font-size:12px;color:{C['sub']}'>{position}</span><br>"
                             f"{skill_badge(name, 30, 12)}</td>")
                if position < len(ordered):
                    cells.append(f"<td style='font-size:16px;color:{C['accent']}'>→</td>")
            ultimate_name = next((candidate for candidate in ultimate_names if candidate in str(ultimate or "")), "")
            if ultimate_name:
                cells.append(f"<td style='font-size:16px;color:{C['accent']}'>→</td>"
                             f"<td align=center style='border-left:1px solid {C['line']};padding-left:8px'>"
                             f"<b style='font-size:12px;color:{C['accent']}'>궁</b><br>"
                             f"{skill_badge(ultimate_name, 30, 12)}</td>")
            return (f"<p style='margin-bottom:2px'><b>사용 순서 설정</b></p>"
                    f"<table cellspacing=3><tr>{''.join(cells)}</tr></table>")

        def growth_row(names, mode):
            try:
                from skill_simulator import _growth_priority
                rows = _growth_priority(catalog, list(names), mode)
            except (ImportError, KeyError, TypeError, ValueError):
                rows = []
            if not rows:
                return ""
            cells = []
            for position, row in enumerate(rows, 1):
                cells.append(f"<td align=center><b style='font-size:12px;color:{C['warn']}'>"
                             f"{position}순위</b><br>{skill_badge(row['name'], 26, 12)}<br>"
                             f"<span style='font-size:12px;color:{C['ok']}'>+{row['gain']:.3f}</span></td>")
                if position < len(rows):
                    cells.append(f"<td style='color:{C['sub']}'>›</td>")
            return (f"<p style='margin-bottom:2px'><b>도술 성장 우선순위</b> "
                    f"<span style='font-size:12px;color:{C['sub']}'>동일하게 기본 계수 1% 성장 시 덱 딜 상승</span></p>"
                    f"<table cellspacing=3><tr>{''.join(cells)}</tr></table>")

        def effect_row(names, ultimate):
            try:
                from skill_simulator import effect_indices
                result = effect_indices(catalog, list(names), ultimate)
            except (ImportError, KeyError, TypeError, ValueError):
                return ""
            enhance = ", ".join(result["enhance_labels"]) or "없음"
            weaken = ", ".join(result["weaken_labels"]) or "없음"
            return (f"<p style='font-size:12px'><b style='color:{C['ok']}'>강화 효과 "
                    f"{result['enhance']:.2f}</b> · {escape(enhance)}<br>"
                    f"<b style='color:{C['warn']}'>약화 효과 {result['weaken']:.2f}</b> · "
                    f"{escape(weaken)}</p>")

        detail_sections = {}

        def recommendation_details(names, mode, ultimate):
            key = f"d{len(detail_sections)}"
            detail_sections[key] = (sequence_row(names, mode, ultimate) + growth_row(names, mode)
                                    + effect_row(names, ultimate))
            return (f"<p style='margin:4px 0 10px 0'><a href='skilldetail:{key}' "
                    f"style='color:{C['accent']}'><b>@@ARROW_{key}@@ @@LABEL_{key}@@</b></a> "
                    f"<span style='font-size:12px;color:{C['sub']}'>사용 순서 · 성장 우선순위 · 강화/약화</span></p>"
                    f"@@DETAIL_{key}@@")

        def score_text(row):
            dps = float(row.get("score", 0))
            dpm = float(row.get("dpm", dps * 60))
            return (f"DPS {dps:.2f} {escape(row.get('unit', ''))} · "
                    f"60초 총 {dpm:.2f}%공격력")

        def range_text(names, mode):
            if mode != "aoe":
                return ""
            try:
                from skill_simulator import range_profile
                profile = range_profile(catalog, names)
            except (ImportError, KeyError, TypeError, ValueError):
                return ""
            pull = (" · 몹몰이 " + escape(", ".join(profile["pull_skills"]))) if profile["pull_skills"] else ""
            return (f"<p style='color:{C['ok']}'><b>사냥 유효거리 평균 {profile['average']:.1f}m · "
                    f"최대 {profile['maximum']:.1f}m · 원거리 광역 {profile['long_aoe']}개 · "
                    f"커버리지 {profile['score']:.1f}점</b>{pull}</p>")

        def recommendation_card(title, contents):
            """추천 하나를 독립된 카드로 감싸 다음 추천과 시각적으로 구분한다."""
            return ("<table width='100%' cellspacing='0' cellpadding='0' "
                    "style='margin-top:12px; margin-bottom:14px'><tr>"
                    f"<td style='background-color:{C['panel']}; border:2px solid {C['line']}; "
                    "padding:14px 16px'>"
                    f"<h3 style='margin:0 0 10px 0'>{title}</h3>{contents}"
                    "</td></tr></table>")

        h = f"<body style='color:{C['text']}'>"
        h += f"<p style='color:{C['sub']}'>{escape(g.CAVEAT)}</p>"
        simulation = None
        try:
            with open(os.path.join(sdir, "simulation_results.json"), encoding="utf-8") as f:
                simulation = json.load(f)
        except (OSError, ValueError):
            pass
        if f2p_only:
            h += ("<h2>무과금 집중 육성 시뮬레이션</h2>"
                  f"<p style='color:{C['sub']}'>현재 보유 도술만 사용하고, 12계열에 재화를 분산하지 않도록 "
                  "2계열 또는 3계열에 각 2개 이상 배치해 60초 지속 DPS를 계산합니다. "
                  "강화·돌파·각성 수치는 제외합니다.</p>")
            if not simulation or not simulation.get("f2p"):
                h += "<p>무과금 계산 결과가 없습니다. 위의 CPU 시뮬레이션을 실행해 주세요.</p>"
            else:
                modes = (("aoe", "광역"), ("single", "단일"))
                groups = (("two_styles", "2계열 집중"), ("three_styles", "3계열 집중"))
                for mode, mode_label in modes:
                    h += f"<h2>{mode_label} 1분 지속딜</h2>"
                    for group, group_label in groups:
                        rows = simulation.get("f2p", {}).get(mode, {}).get(group, [])
                        if not rows:
                            h += f"<p><b>{group_label}</b> · 조건을 만족하는 보유 조합 없음</p>"
                            continue
                        for rank, row in enumerate(rows[:3], 1):
                            distribution = row.get("style_distribution", {})
                            mix = " · ".join(f"{style} {count}개" for style, count in distribution.items())
                            title = (f"{group_label} {rank}위 · "
                                     f"<span style='color:{C['warn']}'>{escape(mix)}</span> · "
                                     f"<span style='color:{C['accent']}'>{score_text(row)}</span>")
                            contents = (f"{recommendation_row(row['skills'], mode, row['score'], row['unit'], row.get('ultimate'))}"
                                        f"{recommendation_details(row['skills'], mode, row.get('ultimate'))}")
                            h += recommendation_card(title, contents)
                h += (f"<p style='color:{C['sub']}'>계산 {escape(simulation.get('generated_at', '?'))} · "
                      "계열별 상위 후보와 상태 연계·시전 시간·쿨타임·궁극기를 함께 비교했습니다.</p>")
            h += "</body>"
            tb.set_collapsible_html(h, detail_sections)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 8, 0, 0)
            bar = QHBoxLayout()
            run_button = QPushButton("무과금 시뮬레이션 다시 실행")
            run_button.setObjectName("primary")
            run_button.clicked.connect(self._manual_skill_learning)
            run_button.setToolTip("보유 도술을 기준으로 2계열·3계열 집중 조합을 다시 계산합니다")
            bar.addWidget(run_button)
            bar.addStretch(1)
            layout.addLayout(bar)
            layout.addWidget(tb, 1)
            return page
        if simulation:
            h += ("<h2>CPU 도술 조합 시뮬레이션</h2>"
                  f"<p><b style='color:{C['warn']}'>도술 각성 제외 · 강화/돌파 제외 · 기본 계수 비교</b></p>"
                  f"<p style='color:{C['sub']}'>깨비지지 전체 {simulation['catalog_count']}개 도술 · "
                  f"유저 덱 {simulation['user_build_count']}개 · 현재 보유 {simulation['owned_count']}개 · "
                  f"60초 기본 계수 기준<br>{escape(simulation['method'])}</p>")
            if simulation.get("unmodeled_owned"):
                h += (f"<p style='color:{C['warn']}'>계수 자료가 없어 조합 계산에서 제외: "
                      f"{escape(', '.join(simulation['unmodeled_owned']))}</p>")
            exact_rows = simulation.get("kkaebigg_exact", {}).get("rows", [])
            if exact_rows:
                h += ("<h3>깨비지지 원본 계산기 교차검증</h3>"
                      f"<p style='color:{C['sub']}'>사이트와 같은 정상상태·첫 5칸 궁 조건·60초 계산식입니다. "
                      "광역 대상 수 보정이 없는 단일 대상 기준 상위 결과입니다.</p>")
                for rank, row in enumerate(exact_rows[:2], 1):
                    title = (f"{rank}위 · {escape(row['main'])} 궁 · DPS "
                             f"<span style='color:{C['accent']}'>{row['dps']:.2f}</span>")
                    contents = (f"{recommendation_row(row['skills'], 'single', row['dps'], '%공격력/초', row.get('ultimate'))}"
                                f"{recommendation_details(row['skills'], 'single', row.get('ultimate'))}")
                    h += recommendation_card(title, contents)
            equipped = list(getattr(g, "CURRENT_AOE_LOADOUT", []) or [])
            equipped_ultimate = getattr(g, "CURRENT_AOE_ULTIMATE", None)
            if equipped and all(name in skill_details for name in equipped):
                try:
                    from skill_simulator import _rotation_score
                    ultimates = {row["style"]: row for row in catalog.get("skills", []) if row.get("ultimate")}
                    score, _style, ultimate, _casts, dpm = _rotation_score(
                        [skill_details[name] for name in equipped], "aoe", ultimates)
                    equipped_ultimate = ultimate["name"] if ultimate else equipped_ultimate
                    equipped_row = {"score": score, "dpm": dpm, "unit": "%공격력·대상/초"}
                    title = ("현재 실제 설정 · 광역 실전 덱 "
                             f"<span style='color:{C['ok']}'>{score_text(equipped_row)}</span>")
                    contents = (f"{recommendation_row(equipped, 'aoe', score, equipped_row['unit'], equipped_ultimate)}"
                                f"{range_text(equipped, 'aoe')}"
                                f"{recommendation_details(equipped, 'aoe', equipped_ultimate)}"
                                f"<p style='color:{C['sub']}'>검기 날리기 → 영혼 소용돌이 적용 · "
                                "전방 판정보다 흡인형 전방향 범위를 우선한 실전 설정</p>")
                    h += recommendation_card(title, contents)
                except (ImportError, KeyError, TypeError, ValueError):
                    pass
            labels = (("current", "현재 보유만"), ("future_3_4", "상자에서 미보유 최대 4개 추가"),
                      ("community", "깨비지지 유저 덱 재계산 1위"))
            modes = (("aoe", "광역"), ("single", "단일"))
            for group, group_label in labels:
                for mode, mode_label in modes:
                    rows = simulation.get(group, {}).get(mode, [])
                    if not rows:
                        continue
                    row = rows[0]
                    new = ""
                    if row.get("new_skills"):
                        new = ("<br><span style='color:" + C["warn"] + "'>추가 필요: "
                               + escape(", ".join(row["new_skills"])) + "</span>")
                    title = (f"{escape(group_label)} · {escape(mode_label)} 1분 지속딜 "
                             f"<span style='color:{C['accent']}'>{score_text(row)}</span>")
                    contents = (f"{recommendation_row(row['skills'], mode, row['score'], row['unit'], row.get('ultimate'))}"
                                f"{range_text(row['skills'], mode)}"
                                f"{recommendation_details(row['skills'], mode, row.get('ultimate'))}"
                                f"<p>{new}</p>")
                    h += recommendation_card(title, contents)
            h += "<h2>최종 추천 · 스킬 사용 순서</h2>"
            for mode, mode_label in modes:
                row = simulation.get("final", {}).get(mode)
                if not row:
                    continue
                priority = simulation.get("unlock_priority", {}).get(mode, [])
                source = row.get("source", "자체 CPU 탐색")
                if row.get("build_name"):
                    source += " · " + row["build_name"]
                title = f"{escape(mode_label)} 1분 DPS 1위 · {score_text(row)}"
                contents = (f"{recommendation_row(row['skills'], mode, row['score'], row['unit'], row.get('ultimate'))}"
                            f"{range_text(row['skills'], mode)}"
                            f"{recommendation_details(row['skills'], mode, row.get('ultimate'))}"
                            f"<p>사용 순서: {escape(' → '.join(row.get('cast_order') or row['skills']))}<br>"
                            f"출처: {escape(source)}<br>"
                            f"<span style='color:{C['warn']}'>상자 획득 우선순위: "
                            f"{escape(' → '.join(priority))}</span></p>")
                h += recommendation_card(title, contents)
            h += (f"<p style='color:{C['sub']}'>수집 {escape(simulation['generated_at'])} · 원본 도감 데이터 "
                  f"{escape(str(simulation.get('source_date') or '?'))}. 실제 피해는 강화·돌파·각성·방어력·"
                  "치명타·시전 순서에 따라 달라집니다.</p>")
        h += "<h2>용도별 추천 세트</h2>"
        for st in g.SETS:
            mode = "single" if any(word in st["title"] for word in ("단일", "보스", "DPS")) else "aoe"
            title = f"{escape(st['title'])} <span style='color:{C['sub']}'>[{escape(st['tag'])}]</span>"
            contents = (f"{recommendation_row(st['skills'], mode, ultimate=st['ultimate'])}"
                        f"{recommendation_details(st['skills'], mode, st['ultimate'])}"
                        f"<p><span style='color:{C['sub']}'>{escape(st['why'])} "
                        f"(출처: {escape(st['src'])})</span></p>")
            h += recommendation_card(title, contents)
        if getattr(g, "KKAEBIGG_BUILDS", None):
            meta = g.KKAEBIGG_META
            h += (f"<h2>깨비지지 인기 빌드 원본</h2>"
                  f"<p style='color:{C['sub']}'>도감 {meta['catalog_updated']} 갱신 · {meta['styles']}계열 {meta['skills']}도술 · "
                  f"{meta['collected_at']} 인기순 수집 · 추천 수는 수집 시점 값</p>")
            for build in g.KKAEBIGG_BUILDS:
                mode = "single" if any(word in build["title"] for word in ("단일", "보스", "극딜")) else "aoe"
                title = (f"{escape(build['title'])} "
                         f"<span style='color:{C['warn']}'>추천 {build['likes']:,}</span> "
                         f"<span style='color:{C['sub']}'>· 현재 보유 {escape(build['owned'])}</span>")
                contents = (f"{recommendation_row(build['skills'], mode, ultimate=build['ultimate'])}"
                            f"{recommendation_details(build['skills'], mode, build['ultimate'])}")
                h += recommendation_card(title, contents)
        if getattr(g, "NEWLY_LEARNED_SKILLS", None):
            h += ("<h2>새로 습득한 도술</h2><table cellpadding=5>"
                  "<tr><th align=left>도술</th><th align=left>계열</th><th align=left>대상</th>"
                  "<th align=left>피해/회복</th><th align=left>쿨</th><th align=left>거리</th><th align=left>효과</th></tr>")
            for n, family, target, amount, cooldown, distance, effect in g.NEWLY_LEARNED_SKILLS:
                h += (f"<tr><td>{icon_with_tooltip(n, 24)} {escape(n)}</td><td>{escape(family)}</td>"
                      f"<td>{escape(target)}</td><td>{escape(amount)}</td><td>{escape(cooldown)}</td>"
                      f"<td>{escape(distance)}</td><td>{escape(effect)}</td></tr>")
            h += "</table>"
        h += "<h2>무과금 팁</h2><ul>" + "".join(f"<li>{escape(t)}</li>" for t in g.TIPS) + "</ul>"
        h += "<h2>12계열 요약</h2><table cellpadding=5 border=0><tr><th align=left>계열</th><th align=left>분류</th><th align=left>궁극기</th><th align=left>용도</th><th align=left>특징</th></tr>"
        for f in g.FAMILIES:
            h += "<tr>" + "".join(f"<td>{escape(x)}</td>" for x in f) + "</tr>"
        h += (f"</table><h2>현재 잠금 해제 도술 전체 ({len(g.UNLOCKED_SKILLS)}개)</h2>"
              "<table cellpadding=5><tr><th align=left>도술</th><th align=left>계열</th><th align=left>대상</th>"
              "<th align=left>피해%</th><th align=left>쿨</th><th align=left>거리</th><th align=left>핵심 효과</th></tr>")
        for n, family, target, damage, cooldown, distance, effect in g.UNLOCKED_SKILLS:
            h += (f"<tr><td>{icon_with_tooltip(n, 24)} {escape(n)}</td><td>{escape(family)}</td><td>{escape(target)}</td>"
                  f"<td>{escape(damage)}</td><td>{cooldown}초</td><td>{distance}</td><td>{escape(effect)}</td></tr>")
        h += "</table><h2>실측 스킬 효율 (피해% ÷ 쿨타임)</h2><table cellpadding=5><tr><th align=left>도술</th><th align=left>피해%</th><th align=left>쿨(초)</th><th align=left>초당</th></tr>"
        for n, d, cd, dps in sorted(g.DPS_TABLE, key=lambda r: -r[3]):
            h += f"<tr><td>{icon_with_tooltip(n, 24)} {escape(n)}</td><td>{escape(d)}</td><td>{cd}</td><td>{dps:.1f}</td></tr>"
        h += "</table><h2>출처</h2><ul>" + "".join(
            f"<li><a style='color:{C['accent']}' href='{u}'>{escape(t)}</a> <span style='color:{C['sub']}'>— {escape(n)}</span></li>"
            for t, u, n in g.SOURCES) + "</ul></body>"
        tb.set_collapsible_html(h, detail_sections)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        bar = QHBoxLayout()
        run_button = QPushButton("CPU 시뮬레이션 다시 실행")
        run_button.setObjectName("primary")
        run_button.clicked.connect(self._manual_skill_learning)
        run_button.setToolTip("깨비지지 전체 도술·유저 덱을 다시 수집하고 CPU 최대 절반으로 계산합니다")
        self.skill_sim_status = QLabel("자동 갱신 6시간 · CPU 최대 50%")
        self.skill_sim_status.setObjectName("sub")
        bar.addWidget(run_button)
        bar.addWidget(self.skill_sim_status)
        bar.addStretch(1)
        layout.addLayout(bar)
        layout.addWidget(tb, 1)
        return page

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
        try:
            from bot2.selfcheck import validate_config, missing_templates
            cfg = self._build_config()
            # 예약 모드는 Bot 생성 전에 적용한다. 생성 뒤 요청하면 첫 루프에서
            # 기본 퀘스트 확인이 먼저 실행돼 임무 창이 열릴 수 있다.
            if self._pending_normal:
                cfg.setdefault("normal_mode", {})["enabled"] = True
                cfg.setdefault("daily_mode", {})["enabled"] = False
                cfg.setdefault("story_mode", {})["enabled"] = False
                cfg.setdefault("farm", {})["enabled"] = False
            elif self._pending_daily or self._pending_daily_titles:
                cfg.setdefault("daily_mode", {})["enabled"] = True
                cfg.setdefault("normal_mode", {})["enabled"] = False
                cfg.setdefault("story_mode", {})["enabled"] = False
                cfg.setdefault("farm", {})["enabled"] = False
            elif self._pending_story:
                cfg.setdefault("story_mode", {})["enabled"] = True
                cfg.setdefault("daily_mode", {})["enabled"] = False
                cfg.setdefault("normal_mode", {})["enabled"] = False
                cfg.setdefault("farm", {})["enabled"] = False
            elif self._pending_farm:
                cfg.setdefault("farm", {})["enabled"] = True
                cfg.setdefault("normal_mode", {})["enabled"] = False
                cfg.setdefault("daily_mode", {})["enabled"] = False
                cfg.setdefault("story_mode", {})["enabled"] = False
            if not isinstance(cfg, dict):
                raise ValueError("config.yaml이 설정 객체가 아닙니다")
            issues = validate_config(cfg)
            missing = missing_templates(cfg)
            if issues or missing:
                raise ValueError("; ".join(issues + (["필수 인식 파일 없음: " + ", ".join(missing)] if missing else [])))
            if not self._save():
                raise OSError("설정을 저장하지 못했습니다. 저장 위치와 권한을 확인하세요.")
        except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
            self._pending_potion = self._pending_farm = self._pending_normal = self._pending_daily = self._pending_story = False
            self._pending_daily_scan = False
            self._pending_daily_titles = None
            self._on_failed(str(error))
            return
        self._last_error = ""
        self.notice.hide()
        logging.getLogger().setLevel(logging.DEBUG if self.chk_debug.isChecked() else logging.INFO)
        self.thread = BotThread(cfg, self.chk_dry.isChecked(), self.chk_debug.isChecked())
        self.thread.failed.connect(self._on_failed)
        self.thread.finished.connect(self._on_finished)
        self.thread.start()
        self.started_at = time.monotonic()
        self.session_total = 0.0
        self._set_running(True)

    def _on_failed(self, message):
        self._last_error = message
        self._pending_potion = self._pending_farm = self._pending_normal = self._pending_daily = self._pending_story = False
        self._pending_daily_scan = False
        self._pending_daily_titles = None
        self.pill.set("오류 · 도움말에서 점검", C["bad"])
        self._notify("자동 진행을 시작하거나 계속할 수 없습니다. " + message + " · 도움말의 환경 점검과 로그를 확인하세요.", True)

    def buy_potion(self):
        """물약 구매 요청. 실행 중이면 다음 틱에, 정지 상태면 시작한 뒤 바로 구매."""
        bot = self.thread.bot if self.thread else None
        if bot and getattr(bot, "potion_shop", None):
            bot.request_manual_focus()
            bot.potion_shop.requested = True
            if bot.paused:
                self.toggle_pause()
            logging.info("물약 구매 요청 (버튼)")
        else:
            self._pending_potion = True
            logging.info("물약 구매 요청 (버튼) → 매크로 시작")
            self.start()

    def _request_daily_scan(self):
        """실제 임무 화면에서 일일 퀘스트 목록을 OCR로 가져온다."""
        bot = self.thread.bot if self.thread else None
        self.lbl_daily_picker.setText("게임에서 일일 목록을 읽는 중입니다…")
        self.btn_daily_scan.setEnabled(False)
        if bot:
            bot.request_daily_quest_scan()
            return
        if self.thread:
            self._pending_daily_scan = True
            return
        self._pending_daily_scan = True
        self.start()

    def _checked_daily_titles(self):
        return [title for title, checkbox in self.daily_quest_checks.items()
                if checkbox.isChecked()]

    def _daily_check_changed(self):
        titles = self._checked_daily_titles()
        self.btn_daily_selected.setEnabled(bool(titles))
        self.saved["_daily_selected_titles"] = list(titles)
        try:
            self._persist_saved(self.saved)
        except OSError as error:
            self.lbl_daily_picker.setText("선택한 일일 목록 저장 실패: " + str(error))
            return
        bot = self.thread.bot if self.thread else None
        if bot and getattr(bot, "mission", None):
            bot.mission.set_selected_daily_titles(titles)

    def _start_selected_daily(self):
        titles = self._checked_daily_titles()
        if not titles:
            self.lbl_daily_picker.setText("진행할 일일 퀘스트를 하나 이상 체크해 주세요")
            return
        if not self._persist_mode_toggles(daily=True):
            return
        self.saved["_daily_selected_titles"] = list(titles)
        try:
            self._persist_saved(self.saved)
        except OSError as error:
            self.lbl_daily_picker.setText("선택한 일일 목록 저장 실패: " + str(error))
            return
        bot = self.thread.bot if self.thread else None
        if bot:
            bot.request_selected_daily(titles)
            if bot.paused:
                self.toggle_pause()
        elif self.thread:
            self._pending_daily_titles = titles
        else:
            self._pending_daily_titles = titles
            self.start()
        self.lbl_daily_picker.setText(f"선택한 일일 {len(titles)}개를 준비 중입니다…")

    def _show_daily_quests(self, titles, remember=False):
        while self.daily_quest_layout.count():
            item = self.daily_quest_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.daily_quest_checks = {}
        title_key = lambda value: re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or ""))
        remembered = {title_key(title)
                      for title in self.saved.get("_daily_selected_titles", [])}
        for title in titles:
            checkbox = QCheckBox(title)
            checkbox.setChecked(title_key(title) in remembered)
            checkbox.toggled.connect(self._daily_check_changed)
            self.daily_quest_layout.addWidget(checkbox)
            self.daily_quest_checks[title] = checkbox
        self.btn_daily_selected.setEnabled(bool(self._checked_daily_titles()))
        if remember and titles:
            self.saved["_daily_quest_catalog"] = list(titles)
            try:
                self._persist_saved(self.saved)
            except OSError as error:
                self.lbl_daily_picker.setText("최근 일일 목록 저장 실패: " + str(error))

    def _refresh_daily_picker(self, bot):
        if not bot:
            self.btn_daily_scan.setEnabled(not bool(self.thread))
            return
        state = getattr(bot, "daily_picker_state", {})
        revision = int(state.get("revision", -1))
        status = state.get("status", "idle")
        self.btn_daily_scan.setEnabled(status not in ("loading", "starting"))
        if revision == self._daily_picker_revision:
            return
        self._daily_picker_revision = revision
        items = state.get("items") or []
        if status == "ready" and items:
            self._show_daily_quests(items, remember=True)
        message = state.get("message")
        if message:
            self.lbl_daily_picker.setText(message)

    def toggle_detect(self):
        """사용자 조작 감지 켜기/끄기 (실행 중이면 바로 적용, 정지 상태면 다음 시작부터)."""
        self.user_detect = not self.user_detect
        bot = self.thread.bot if self.thread else None
        if bot:
            bot.set_user_detect(self.user_detect)
        else:
            logging.info("사용자 조작 감지 %s (다음 시작부터 적용)", "켬" if self.user_detect else "끔")
        self.saved["_user_detect"] = self.user_detect
        try:
            self._persist_saved(self.saved)
        except OSError as error:
            self._notify("조작 감지 설정 저장 실패: " + str(error), True)
        self._refresh_detect_button()

    def _refresh_detect_button(self):
        self._set_checked_quiet(self.btn_detect, self.user_detect)

    @staticmethod
    def _set_checked_quiet(toggle, value):
        """프로그램이 상태를 맞출 때는 신호 없이 바로 그 모양으로 (사용자가 누른 것으로 착각해 다시 실행하지 않게)."""
        if toggle.isChecked() != bool(value):
            toggle.blockSignals(True)
            toggle.setChecked(bool(value))
            toggle.blockSignals(False)

    def _sync_daily_picker_visibility(self, enabled=None):
        """일일 전용 모드에서만 퀘스트 선택 영역을 보여 준다."""
        if enabled is None:
            enabled = self.btn_daily.isChecked()
        self.daily_card.setVisible(bool(enabled))

    def _set_pause_label(self, paused):
        """일시정지 버튼: 실행 중이면 ⏸ 일시정지, 멈춘 상태면 ▶ 재개 (아이콘도 같이 바꾼다)."""
        self.btn_pause.setText(" 재개" if paused else " 일시정지")
        self.btn_pause.setIcon(icons.make("play" if paused else "pause", C["accent"] if not paused else C["ok"], C["idle"]))

    def request_evade(self):
        """자리 회피 수동 실행 (봇이 필드에서 자동 사냥 중일 때)."""
        bot = self.thread.bot if self.thread else None
        if not bot:
            logging.info("자리 회피: 매크로가 실행 중이 아님")
            return
        bot.request_evade()
        logging.info("자리 회피 요청 (버튼)")

    def _persist_mode_toggles(self, farm=False, normal=False, daily=False, story=False):
        """상단 모드 토글을 즉시 JSON에 저장하고 설정 화면과 함께 맞춘다."""
        values = {
            "farm/enabled": bool(farm),
            "normal_mode/enabled": bool(normal),
            "daily_mode/enabled": bool(daily),
            "story_mode/enabled": bool(story),
        }
        previous = dict(self.saved)
        self.saved.update(values)
        for key, value in values.items():
            path = tuple(key.split("/"))
            if path in self.controls:
                self._set_checked_quiet(self.controls[path], value)
        try:
            self._persist_saved(self.saved)
        except OSError as error:
            self.saved = previous
            self._notify("모드 상태 저장 실패: " + str(error), True)
            logging.error("모드 상태 저장 실패: %s", error)
            return False
        for button, value in ((self.btn_farm, farm), (self.btn_normal, normal),
                              (self.btn_daily, daily), (self.btn_story, story)):
            self._set_checked_quiet(button, bool(value))
        return True

    def toggle_farm(self, checked=None):
        """앵벌 모드 수동 켜기/끄기. 정지 상태면 매크로를 시작하면서 앵벌 모드로."""
        bot = self.thread.bot if self.thread else None
        on = bool(checked) if checked is not None else not bool(
            bot.farm_mode if bot else self.saved.get("farm/enabled", False))
        if not self._persist_mode_toggles(farm=on):
            self._refresh()
            return
        if bot:
            bot.request_manual_farm(on, force=True)
            if on:
                bot.request_normal_mode(False, force=True)
                bot.request_daily_mode(False, force=True)
                bot.request_story_mode(False, force=True)
            if on and bot.paused:
                self.toggle_pause()
            logging.info("앵벌 모드 %s 요청 (버튼)", "켜기" if on else "끄기")
        elif self.thread:
            return                                  # 시작 중
        elif on:
            self._pending_farm = True
            self._pending_normal = False
            self._pending_daily = False
            self._pending_story = False
            logging.info("앵벌 모드 켜기 요청 (버튼) → 매크로 시작")
            self.start()

    def toggle_normal(self, checked=None):
        """현재 위치 일반 모드 켜기/끄기. 앵벌 모드와는 상호 배타적이다."""
        bot = self.thread.bot if self.thread else None
        on = bool(checked) if checked is not None else not bool(
            bot.normal_mode if bot else self.saved.get("normal_mode/enabled", True))
        if not self._persist_mode_toggles(normal=on):
            self._refresh()
            return
        if bot:
            bot.request_normal_mode(on, force=True)
            if on:
                bot.request_manual_farm(False, force=True)
                bot.request_daily_mode(False, force=True)
                bot.request_story_mode(False, force=True)
            if on and bot.paused:
                self.toggle_pause()
            logging.info("일반 모드 %s 요청 (버튼)", "켜기" if on else "끄기")
        elif self.thread:
            return
        elif on:
            self._pending_normal = True
            self._pending_farm = False
            self._pending_daily = False
            self._pending_story = False
            logging.info("일반 모드 켜기 요청 (버튼) → 매크로 시작")
            self.start()

    def toggle_daily(self, checked=None):
        """본편·서사·주간을 제외하고 일일 임무만 진행한다."""
        bot = self.thread.bot if self.thread else None
        on = bool(checked) if checked is not None else not bool(
            bot.daily_mode if bot else self.saved.get("daily_mode/enabled", False))
        if not self._persist_mode_toggles(daily=on):
            self._refresh()
            return
        self._sync_daily_picker_visibility(on)
        if bot:
            bot.request_daily_mode(on, force=True)
            if on:
                bot.request_normal_mode(False, force=True)
                bot.request_manual_farm(False, force=True)
                bot.request_story_mode(False, force=True)
            if on and bot.paused:
                self.toggle_pause()
            logging.info("일일 전용 모드 %s 요청 (버튼)", "켜기" if on else "끄기")
        elif self.thread:
            return
        elif on:
            self._pending_daily = True
            self._pending_normal = False
            self._pending_farm = False
            self._pending_story = False
            logging.info("일일 전용 모드 켜기 요청 (버튼) → 매크로 시작")
            self.start()

    def toggle_story(self, checked=None):
        """현재 전투력에 맞는 서사 임무만 수행한다."""
        bot = self.thread.bot if self.thread else None
        on = bool(checked) if checked is not None else not bool(
            bot.story_mode if bot else self.saved.get("story_mode/enabled", False))
        if not self._persist_mode_toggles(story=on):
            self._refresh()
            return
        if bot:
            bot.request_story_mode(on, force=True)
            if on:
                bot.request_normal_mode(False, force=True)
                bot.request_daily_mode(False, force=True)
                bot.request_manual_farm(False, force=True)
            if on and bot.paused:
                self.toggle_pause()
            logging.info("서사 전용 모드 %s 요청 (버튼)", "켜기" if on else "끄기")
        elif self.thread:
            return
        elif on:
            self._pending_story = True
            self._pending_daily = False
            self._pending_normal = False
            self._pending_farm = False
            logging.info("서사 전용 모드 켜기 요청 (버튼) → 매크로 시작")
            self.start()

    def stop(self):
        if self.thread:
            self.thread.stop_requested = True
            if self.thread.bot:
                self.thread.bot.stop_event.set()
            self.btn_stop.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.pill.set("정지 중…", C["warn"])

    def toggle_pause(self):
        bot = self.thread.bot if self.thread else None
        if not bot:
            return
        if bot.paused:
            bot.request_manual_focus()
            bot.activity.ignore_until_now()
            bot.waiting_user = False
            bot._idle_since = bot._verify = None
            bot.alert_reason = ""
            bot.paused = False
            bot.risk_guard.arm()
            logging.info("재개 (버튼)")
        else:
            bot.paused = True
            logging.info("일시정지 (버튼)")

    def _on_finished(self):
        finished = self.thread
        if finished:
            finished.wait()
            self.session_total = time.monotonic() - self.started_at
        self.thread = None
        self._pending_potion = self._pending_farm = self._pending_normal = self._pending_daily = self._pending_story = False
        self._pending_daily_scan = False
        self._pending_daily_titles = None
        if finished:
            finished.deleteLater()
        self._set_running(False)
        if self._last_error:
            self.pill.set("오류 · 도움말에서 점검", C["bad"])
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _set_running(self, running):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        for path, w in self.controls.items():
            w.setEnabled(not running and path not in LOCKED_SETTINGS)
            if path in LOCKED_SETTINGS:
                self._set_checked_quiet(w, False)
        self.chk_dry.setEnabled(not running)
        self.chk_debug.setEnabled(not running)
        self.profile_bar.setEnabled(not running)
        self.btn_restore_settings.setEnabled(not running)
        self.btn_evade.setEnabled(running)
        if not running:
            self.pill.set("정지됨", C["idle"])
            self._set_pause_label(False)

    # ---------- 주기 갱신
    def _refresh(self):
        th = self.thread
        bot = th.bot if th else None
        boss_status = getattr(getattr(bot, "boss_hunt", None), "status", lambda: "")()
        nakhwa_status = getattr(getattr(bot, "nakhwa_boss", None), "status", lambda: "")()
        cfg = getattr(bot, "cfg", None) or self.base_config
        self.lbl_schedule.setText(dashboard_schedule(datetime.now(), cfg, boss_status, nakhwa_status))
        self._refresh_extra(bot)
        self._refresh_daily_picker(bot)
        mode_persist = getattr(bot, "_mode_persist_request", None) if bot else None
        if mode_persist:
            bot._mode_persist_request = None
            self._persist_mode_toggles(**mode_persist)
        if self._pending_potion and bot and getattr(bot, "potion_shop", None):
            bot.potion_shop.requested = True
            self._pending_potion = False
        if self._pending_farm and bot:
            bot.request_manual_farm(True)
            self._pending_farm = False
        if self._pending_normal and bot:
            bot.request_normal_mode(True)
            self._pending_normal = False
        if self._pending_daily and bot:
            bot.request_daily_mode(True)
            self._pending_daily = False
        if self._pending_story and bot:
            bot.request_story_mode(True)
            self._pending_story = False
        if self._pending_daily_scan and bot:
            bot.request_daily_quest_scan()
            self._pending_daily_scan = False
        if self._pending_daily_titles and bot:
            bot.request_selected_daily(self._pending_daily_titles)
            self._pending_daily_titles = None
        pending = bot._manual_farm_request if bot else None
        on = bool((bot and (pending if pending is not None else bot.farm_mode)) or self._pending_farm
                  or (not th and self.saved.get("farm/enabled", False)))
        self._set_checked_quiet(self.btn_farm, on)
        normal_pending = bot._normal_mode_request if bot else None
        normal_on = bool((bot and (normal_pending if normal_pending is not None else bot.normal_mode))
                         or self._pending_normal
                         or (not th and self.saved.get("normal_mode/enabled", True)))
        self._set_checked_quiet(self.btn_normal, normal_on)
        daily_pending = bot._daily_mode_request if bot else None
        daily_on = bool((bot and (daily_pending if daily_pending is not None else bot.daily_mode))
                        or self._pending_daily
                        or (not th and self.saved.get("daily_mode/enabled", False)))
        self._set_checked_quiet(self.btn_daily, daily_on)
        story_pending = getattr(bot, "_story_mode_request", None) if bot else None
        story_on = bool((bot and (story_pending if story_pending is not None else
                                  getattr(bot, "story_mode", False)))
                        or self._pending_story
                        or (not th and self.saved.get("story_mode/enabled", False)))
        self._set_checked_quiet(self.btn_story, story_on)
        self._sync_daily_picker_visibility(daily_on)
        self._refresh_flow_runtime(bot)
        if not th:
            self.lbl_state.setText("오류로 정지됨" if self._last_error else "정지됨")
            self.lbl_state.setStyleSheet(f"color:{C['idle']};")
            self.lbl_state_sub.setText(self._last_error or "시작을 누르면 게임 화면을 보고 자동으로 진행합니다")
            self.hp_bar.hide()
            for value, detail in self.live_lbl.values():
                value.setText("—")
                detail.setText("시작 후 확인합니다")
            for c in self.chips.values():
                c.on(False)
            self.stat_lbl["time"].setText(fmt_time(self.session_total))
        elif bot is None:
            self.pill.set("정지 중…" if th.stop_requested else "준비 중…", C["warn"])
        else:
            self._refresh_running(bot)
        self.lbl_recent.setText(self._recent_dashboard_text(self.recent))

    @staticmethod
    def _recent_dashboard_text(events):
        """긴 예외 JSON이 대시보드 너비를 밀지 않도록 요약해서 표시한다."""
        lines = []
        for _level, text in events[-9:]:
            compact = " ".join(str(text).split())
            lines.append(compact if len(compact) <= 180 else compact[:177] + "…")
        return "\n".join(lines) or "아직 이벤트가 없습니다"

    def _refresh_live(self, bot):
        """실시간 상황판 갱신 (봇의 LiveStatus가 읽어 둔 값을 그대로 보여 줌 — 여기서는 OCR을 안 한다)."""
        live = getattr(bot, "live", None)
        if live is None:
            return
        d, sy = live.data, live.sync()

        def put(key, big, small="", color=None):
            v, sm = self.live_lbl[key]
            v.setText(big)
            v.setToolTip(big)
            size = "font-size:13px;" if key in ("map", "mob", "quest") else ""
            v.setStyleSheet(size + (f"color:{color};" if color else ""))
            sm.setText(small)

        power = getattr(bot, "power", None) or getattr(getattr(bot, "farm", None), "power", None)
        margin = get_path(getattr(bot, "cfg", {}), ("farm", "margin"), 1.5)
        mode = ("비경 자동사냥" if getattr(bot, "_secret_realm", False)
                else "일일 전용" if getattr(bot, "daily_mode", False)
                else "서사 전용" if getattr(bot, "story_mode", False)
                else "일반" if getattr(bot, "normal_mode", False)
                else "앵벌" if getattr(bot, "farm_mode", False) else "퀘스트")
        put("power", f"{power:,}" if power else "읽는 중", f"최근 확인값 · {mode} · 허용 {margin:g}배")

        rate, age, why = sy["rate"], sy["age"], sy["why"]
        if why:
            put("sync", "대기 중", why, C["warn"])
        elif rate is None:
            put("sync", "확인 중", "", None)
        else:
            col = C["ok"] if rate >= 0.9 else C["warn"] if rate >= 0.6 else C["bad"]
            put("sync", f"{rate * 100:.0f}%", f"최근 화면 인식률 · 마지막 인식 {age:.0f}초 전" if age is not None else "화면 인식률", col)

        if not d or d.get("at") is None or time.monotonic() - d["at"] > 20:
            self.hp_bar.hide()
            for k in ("map", "mob", "quest", "potions"):
                put(k, "-", "읽는 중…" if k == "map" else "")
            return
        hp = d.get("hp") if d.get("field") else None
        count = d.get("potions") if d.get("field") else None
        threshold = get_path(getattr(bot, "cfg", {}), ("potion_shop", "buy_below"), 50)
        put("potions", f"{count}개" if count is not None else "읽는 중",
            f"최근 인식값 · {threshold}개 미만 자동 구매" if count is not None else "필드에서 확인합니다",
            C["warn"] if count is not None and count < threshold else None)
        self.hp_bar.setVisible(hp is not None)
        if hp is not None:
            self.hp_bar.setValue(max(0, min(100, round(hp * 100))))
            color = C["bad"] if hp < 0.3 else C["warn"] if hp < 0.5 else C["ok"]
            self.hp_bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
            self.hp_bar.setToolTip(f"마지막으로 읽은 HP {hp * 100:.0f}%")
        if not d.get("field"):
            put("map", "필드 아님", "메뉴·던전 화면·컷신 중일 수 있음", C["warn"])
        else:
            put("map", d.get("map") or "?", f"HP {d['hp'] * 100:.0f}%" if d.get("hp") is not None else "")
        t = d.get("target")
        if t:
            pw = f"{t['power'] // 1000}K" if t.get("power") else ""
            ratio = f" · 내 전투력의 {t['power'] / power:.2f}배" if t.get("power") and power else ""
            put("mob", t["name"], f"{pw} · 체력 {t['fill'] * 100:.0f}%{ratio}".strip(" ·"))
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
            self.lbl_window_sub.setText("게임 창이 없거나 최소화되어 있습니다")
        if getattr(bot, "_secret_realm", False):
            self.lbl_source.setText("비경 자동사냥")
            self.lbl_source_sub.setText("시간 종료까지 지도 이동 없이 T 자동사냥만 유지")
        elif getattr(bot, "farm", None):
            cur = bot.farm.current
            self.lbl_source.setText("사냥터 모드")
            self.lbl_source_sub.setText(bot.farm.describe() if cur or bot.farm.power else "사냥터 선택 대기")
        else:
            self.lbl_source.setText(bot._source or "-")
            blocked = [n for n in ("본편", "서사", "일일", "주간") if bot.mission and bot.mission.is_blocked(n)]
            self.lbl_source_sub.setText("중단 중: " + ", ".join(blocked) if blocked else "중단된 퀘스트 없음")

        if bot.paused:
            self.pill.set("일시정지", C["warn"])
            self._set_pause_label(True)
            msg = bot.alert_reason or "일시정지 중입니다"
            self._show_state("일시정지", msg, C["warn"], None)
            return
        self._set_pause_label(False)
        if bot.waiting_user:
            self.pill.set("사용자 조작 감지", C["accent"])
            self._show_state("양보 중", f"직접 조작을 감지했습니다 · {bot.user_resume}초간 조작이 없으면 재개", C["accent"], None)
            return
        if not win_ok or bot.window.is_minimized():
            self.pill.set("게임 창 대기", C["warn"])
            self._show_state("게임 창 대기", "게임 창을 복원하면 Cua가 계속 감시합니다", C["warn"], None)
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
            if hasattr(self, "treasure_guide"):
                self.treasure_guide.set_power(bot.power)
        if now - self._stats_at > 60 and self.tabs.currentWidget() is self.stats_tab:
            self._stats_at = now
            self.stats_tab.refresh()
        self.preview_tab.tick(self.tabs.currentWidget() is self.preview_tab and self.isVisible())
        # 예약 시작
        if self.saved.get("_sched_start_on", False) and not self.thread:
            t = QTime.fromString(self.saved.get("_sched_start", "02:00"), "HH:mm")
            nowt = datetime.now()
            if (nowt.hour, nowt.minute) == (t.hour(), t.minute()) and self._sched_day != nowt.date():
                self._sched_day = nowt.date()
                logging.info("예약 시작")
                self.start()
        # 예약 정지
        if (self.saved.get("_sched_stop_on", False) and self.thread and self.started_at
                and now - self.started_at > self.saved.get("_sched_stop_h", 6) * 3600):
            logging.info("예약 정지 (%.1f시간 경과)", self.saved.get("_sched_stop_h", 6))
            self.stop()

    # ---------- 트레이
    def _build_tray(self):
        self.tray = None
        self._quitting = False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icons.make("brand", C["accent"]), self)
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
                g = self.geometry() if not self.isMaximized() else self.normalGeometry()
                self.saved["_geometry"] = [g.x(), g.y(), g.width(), g.height(), self.isMaximized()]
                self._persist_saved(self.saved)
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, e):
        self._save_geometry_now()
        # 창 닫기는 숨김이 아니라 완전 종료다. 실행 중이면 입력·보조 프로세스를
        # 먼저 비동기로 정리한 뒤 _on_finished에서 다시 closeEvent로 들어온다.
        self._quitting = True
        if self.thread:
            self._closing = True
            self.stop()
            self._notify("자동 진행을 정리하고 있습니다. 작업이 멈추면 창이 자동으로 종료됩니다.")
            e.ignore()
            return
        self._stop_helper_children()
        from bot import actions
        actions.clear_input_backend()
        self._save()
        self.timer.stop()
        if self.tray:
            self.tray.hide()
        logging.getLogger().removeHandler(self.log_handler)
        file_handler = getattr(self, "file_handler", None)
        if file_handler:
            logging.getLogger().removeHandler(file_handler)
            file_handler.close()
        e.accept()

    def _stop_helper_children(self):
        """GUI가 직접 실행한 백그라운드 자식 프로세스를 종료한다."""
        process = getattr(self, "_skill_learning_process", None)
        if process is None:
            return
        try:
            if process.poll() is None:
                # A venv pythonw wrapper starts the actual Python process and its
                # ProcessPool workers. Stopping only the wrapper leaves orphans.
                pid = int(getattr(process, "pid", 0) or 0)
                tree_stopped = False
                if os.name == "nt" and pid:
                    result = subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    tree_stopped = result.returncode == 0
                if not tree_stopped:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass
        self._skill_learning_process = None


def main():
    if "--skill-recalculate" in sys.argv:
        from skill_simulator import simulate_saved_catalog
        simulate_saved_catalog(8000)
        return 0
    if "--skill-learn" in sys.argv:
        from skill_simulator import refresh_and_simulate
        # 배포 EXE에서도 같은 내장 모듈로 실행한다. CPU 제한은 도구 실행기와 동일하게 적용한다.
        if os.name == "nt":
            total = max(1, os.cpu_count() or 1)
            allowed = max(1, total // 2)
            ctypes.windll.kernel32.SetProcessAffinityMask(
                ctypes.windll.kernel32.GetCurrentProcess(), (1 << allowed) - 1)
        refresh_and_simulate(8000)
        return 0
    if "--self-check" in sys.argv:
        from app_support import environment_report
        with open(CONFIG_PATH, encoding="utf-8") as stream:
            cfg = yaml.safe_load(stream)
        report = environment_report(cfg, ROOT, include_game=False)
        if "--report" in sys.argv:
            save_json(sys.argv[sys.argv.index("--report") + 1], report)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if any(check["status"] == "error" for check in report["checks"]) else 0
    try:        # 작업표시줄에서 python 아이콘 대신 앱 아이콘이 보이도록 별도 앱 ID 지정
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GameAutoPlay.Dokkaebi.Panel")
    except Exception:  # noqa: BLE001
        pass
    app = QApplication(sys.argv)
    app.setWindowIcon(icons.make("brand", C["accent"]))
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    os.makedirs(ROOT, exist_ok=True)
    lock_path = os.path.join(ROOT, ".gui.lock")
    instance_lock = QLockFile(lock_path)
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        if "--smoke-test" in sys.argv:
            return 3
        if not _replace_running_gui(instance_lock, lock_path):
            QMessageBox.critical(None, "재실행 실패", "기존 도깨비헬퍼를 종료하고 다시 실행하지 못했습니다.")
            return 4
    try:
        win = Window()
    except Exception as error:
        QMessageBox.critical(None, "도깨비헬퍼 실행 실패", "설정과 필수 파일을 확인하세요.\n" + str(error))
        instance_lock.unlock()
        return
    win.restore_geometry()
    win.show()
    if "--smoke-test" in sys.argv:
        if not os.environ.get("DOKKAEBI_DATA_DIR"):
            raise RuntimeError("배포 검증은 DOKKAEBI_DATA_DIR로 별도 데이터 디렉터리를 지정해야 합니다")
        from app_support import environment_report, export_support
        win.timer.stop()
        win._geo_timer.stop()
        report = environment_report(win._build_config(), ROOT, include_game=False)
        report["pages"] = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        report["settings_saved"] = win._save()
        report["settings_roundtrip"] = load_json(SETTINGS_PATH)[0] == win.saved
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        save_json(os.path.join(ROOT, "logs", "smoke-report.json"), report)
        export_support(os.path.join(ROOT, "logs", "smoke-support.zip"), report, win.saved, [])
        win._quitting = True
        hold = max(500, min(10000, int(os.environ.get("DOKKAEBI_SMOKE_HOLD_MS", "500"))))
        QTimer.singleShot(hold, win.close)
        result = app.exec()
        instance_lock.unlock()
        return result
    logging.info("업데이트 내용은 왼쪽 '업데이트' 메뉴에서 볼 수 있습니다")
    logging.info("GUI 실행 권한: %s · 게임 입력: Cua Driver 서비스",
                 "관리자" if ctypes.windll.shell32.IsUserAnAdmin() else "일반 사용자")
    if "--autostart" in sys.argv:
        if "--stay" in sys.argv:                  # 앵벌 시작 때 사냥터를 새로 고르지 않고 지금 자리에서 시작 (관찰·시험용)
            os.environ["DOKKAEBI_STAY"] = "1"
        if "--farm" in sys.argv:                  # 시작하면서 바로 앵벌 모드 (제어판 버튼을 누르지 않고도 켜기)
            win._pending_farm = True
        if "--normal" in sys.argv:                # 현재 위치 일반 모드로 바로 시작
            win._pending_normal = True
            win._pending_farm = False
        if "--daily" in sys.argv:                 # 일일 임무 전용 모드로 바로 시작
            win._pending_daily = True
            win._pending_story = False
            win._pending_normal = False
            win._pending_farm = False
        if "--story" in sys.argv:                 # 적합 서사 임무 전용 모드로 바로 시작
            win._pending_story = True
            win._pending_daily = False
            win._pending_normal = False
            win._pending_farm = False
        QTimer.singleShot(1500, win.start)
    result = app.exec()
    instance_lock.unlock()
    sys.exit(result)


if __name__ == "__main__":
    sys.exit(main())
