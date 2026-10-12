"""메인 루프: 팝업 처리 → 멈춤 감지 → 오른쪽 퀘스트 클릭."""
import logging
import os
import time
import winsound
from datetime import datetime

from . import actions
from .vision import Matcher, MotionIdleDetector, save_image
from .window import GameWindow
from .yeonggi import YeonggiTask

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "templates")
SCREENSHOT_DIR = os.path.join(ROOT, "screenshots")


class QuestBot:
    def __init__(self, config, dry_run=False):
        self.cfg = config
        self.dry_run = dry_run
        self.window = GameWindow(config["window_title"], config.get("process_name"))
        self.matcher = Matcher(TEMPLATE_DIR, config.get("match_threshold", 0.85))

        idle = config["idle"]
        self.idle_seconds = idle.get("seconds", 6)
        self.indicator = idle.get("auto_indicator")
        self.indicator_region = idle.get("indicator_region")
        self.motion = MotionIdleDetector(idle.get("region"), idle.get("diff_threshold", 2.5))

        hk = config.get("hotkeys", {})
        self.stop_key = hk.get("stop", "F12")
        self.pause_key = hk.get("pause", "F11")
        self.hotkeys = actions.HotkeyWatcher(self.stop_key, self.pause_key)

        self.quest_enabled = config["quest_tracker"].get("enabled", True)
        yg = config.get("yeonggi") or {}
        self.yeonggi = YeonggiTask(self, yg) if yg.get("enabled") else None

        self.activity = actions.UserActivity()
        self.user_resume_seconds = config.get("user_idle_resume", 120)
        self.waiting_for_user = False

        self.paused = False
        self.last_quest_click = 0.0
        self.last_seen_running = time.monotonic()
        self.quest_retries = 0

    # ---------- 공통 ----------
    def _save_screen(self, frame, prefix):
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png")
        save_image(path, frame)
        log.warning("현재 화면 저장: %s", path)

    def _alert(self, reason, frame=None):
        log.warning("일시정지: %s  (%s 키로 재개)", reason, self.pause_key)
        if frame is not None:
            self._save_screen(frame, "stuck")
        try:
            winsound.Beep(1000, 400)
        except RuntimeError:
            pass
        self.paused = True

    def _user_is_active(self):
        idle = self.activity.idle_seconds()
        return idle is not None and idle < self.user_resume_seconds

    def _check_user(self):
        """사용자가 조작 중이면 진행 중인 동작을 중단."""
        if self._user_is_active():
            raise actions.UserInterrupt()

    def _click_frame(self, label, px, py):
        sx, sy = self.window.frame_to_screen(px, py)
        if self.dry_run:
            log.info("[dry-run] %s 클릭 예정 (%d, %d)", label, sx, sy)
        else:
            self._check_user()
            actions.click(sx, sy)

    def _press(self, label, key):
        if self.dry_run:
            log.info("[dry-run] %s → %s 키 예정", label, key)
        else:
            self._check_user()
            actions.press(key)

    def _reset_timers(self):
        now = time.monotonic()
        self.last_seen_running = now
        self.motion.reset(now)

    # ---------- 단계별 처리 ----------
    def _handle_popups(self, frame):
        for popup in self.cfg.get("popups", []):
            match = self.matcher.find(frame, popup["template"],
                                      popup.get("region"), popup.get("threshold"))
            if not match:
                continue
            log.info("팝업 감지: %s (일치도 %.2f)", popup["name"], match.score)
            if popup.get("action", "click") == "key":
                self._press(popup["name"], popup["key"])
            else:
                self._click_frame(popup["name"], match.x, match.y)
            time.sleep(popup.get("after_delay", self.cfg.get("popup_after_delay", 1.0)))
            return True
        return False

    def _in_combat(self, frame):
        """상단 중앙에 대상 몬스터 체력 바가 보이면 전투 중."""
        ci = self.cfg.get("combat_indicator") or {}
        return any(self.matcher.find(frame, t, ci.get("region"))
                   for t in ci.get("templates", []))

    def _idle_for(self, frame, now):
        """자동 진행이 멈춘 지 몇 초인지. 전투 중이면 진행 중으로 본다."""
        if self._in_combat(frame):
            self.last_seen_running = now
            self.motion.reset(now)
            return 0.0
        if self.indicator and self.matcher.has(self.indicator):
            if self.matcher.find(frame, self.indicator, self.indicator_region):
                self.last_seen_running = now
            return now - self.last_seen_running
        return self.motion.update(frame, now)

    def _in_field(self, frame):
        """필드 HUD가 보이는지. field_indicator 템플릿이 없으면 항상 True."""
        fi = self.cfg.get("field_indicator") or {}
        if not self.matcher.has(fi.get("template")):
            return True
        return self.matcher.find(frame, fi["template"], fi.get("region")) is not None

    def _click_quest(self, frame, now):
        qt = self.cfg["quest_tracker"]
        if now - self.last_quest_click < qt.get("cooldown", 8):
            return
        if self.quest_retries >= qt.get("max_retries", 5):
            self._alert(f"퀘스트를 {self.quest_retries}번 눌러도 진행되지 않음", frame)
            self.quest_retries = 0
            return

        match = self.matcher.find(frame, qt.get("template"), qt.get("search_region"))
        if match:
            dx, dy = qt.get("click_offset", [0, 0])
            log.info("오른쪽 퀘스트 클릭 (템플릿, 일치도 %.2f)", match.score)
            self._click_frame("퀘스트", match.x + dx, match.y + dy)
        else:
            rx, ry = qt["click"]
            log.info("오른쪽 퀘스트 클릭 (고정 좌표 %.2f, %.2f)", rx, ry)
            self._click_frame("퀘스트", rx * self.window.frame_w, ry * self.window.frame_h)

        self.last_quest_click = now
        self.last_seen_running = now
        self.motion.reset(now)
        self.quest_retries += 1

    # ---------- 루프 ----------
    def run(self):
        log.info("시작: 창 제목 '%s' 검색 중... (%s 종료 / %s 일시정지)%s",
                 self.cfg["window_title"], self.stop_key, self.pause_key,
                 "  [dry-run: 클릭하지 않음]" if self.dry_run else "")
        interval = self.cfg.get("loop_interval", 0.5)
        waiting_logged = False

        while True:
            if self.hotkeys.pressed(self.stop_key):
                log.info("종료 키 입력. 매크로를 끝냅니다.")
                return
            if self.hotkeys.pressed(self.pause_key):
                self.paused = not self.paused
                log.info("일시정지" if self.paused else "재개")
                if not self.paused:
                    self.activity.ignore_until_now()   # 재개 키 입력은 사용자 조작으로 치지 않음
                    self.waiting_for_user = False
                    self._reset_timers()
            if self.paused:
                time.sleep(0.1)
                continue

            # 게임 창이 맨 앞에 있을 때만 동작 (다른 창에 잘못 클릭하는 것 방지)
            if not self.window.locate() or self.window.is_minimized() or not self.window.is_foreground():
                if not waiting_logged:
                    log.info("게임 창이 없거나 맨 앞에 있지 않아 대기 중...")
                    waiting_logged = True
                self._reset_timers()
                time.sleep(0.5)
                continue
            if waiting_logged:
                log.info("게임 창 감지: '%s' (%dx%d)", self.window.title,
                         self.window.width, self.window.height)
                waiting_logged = False
                self.activity.ignore_until_now()       # 게임 창을 앞으로 가져온 클릭은 무시

            # 사용자가 직접 조작 중이면 user_idle_resume초 동안 손대지 않음
            if not self.dry_run and self._user_is_active():
                if not self.waiting_for_user:
                    log.info("사용자 조작 감지 → %d초 동안 조작이 없으면 다시 진행",
                             self.user_resume_seconds)
                    self.waiting_for_user = True
                self._reset_timers()
                time.sleep(0.5)
                continue
            if self.waiting_for_user:
                log.info("%d초 동안 조작 없음 → 다시 진행", self.user_resume_seconds)
                self.waiting_for_user = False

            try:
                self._tick(interval)
            except actions.UserInterrupt:
                log.info("동작 중 사용자 조작 감지 → 중단")
                self._reset_timers()

    def _tick(self, interval):
        frame = self.window.grab()
        now = time.monotonic()

        if self._handle_popups(frame):
            self._reset_timers()
            self.quest_retries = 0
            return

        # 전투 중에는 게임이 메뉴 화면을 닫아버리므로 영기 수련은 전투가 없을 때만
        if (self.yeonggi and self.yeonggi.due(now) and self._in_field(frame)
                and not self._in_combat(frame)):
            try:
                self.yeonggi.run(now)
            finally:
                self._reset_timers()
            return

        if not self.quest_enabled:
            time.sleep(interval)
            return

        idle = self._idle_for(frame, now)
        if idle < 1.0:
            self.quest_retries = 0      # 다시 움직이기 시작함
        if self.indicator and self.matcher.has(self.indicator):
            log.debug("멈춤 %.1fs (자동 이동 표시/전투 기준)", idle)
        else:
            log.debug("멈춤 %.1fs / 변화량 %.2f", idle, self.motion.last_diff)

        if idle >= self.idle_seconds:
            if self._in_field(frame):
                self._click_quest(frame, now)
            else:
                log.debug("필드 화면이 아님 (메뉴/컷신 등) → 퀘스트 클릭 보류")

        time.sleep(interval)
