"""상태 머신 기반 메인 루프.

화면 상태(DEAD > DIALOG > OVERLAY > COMBAT > MOVING > IDLE)에 따라 한 가지 동작만 한다.
퀘스트 클릭은 'IDLE이 오래 지속될 때만' 하고, 클릭한 뒤에는 실제로 진행됐는지 확인해서
진행이 없으면 단계적으로 복구한다 (같은 클릭을 무한 반복하지 않는다).
"""
import logging
import os
import threading
import time
import winsound
from datetime import datetime

from bot import actions
from bot.vision import Matcher, save_image
from bot.window import GameWindow
from bot.yeonggi import YeonggiTask

from .farm import FarmTask
from .mission import MissionTask
from .perception import Perceiver
from .tracker import TrackerScanner

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(ROOT, "templates")
SCREENSHOT_DIR = os.path.join(ROOT, "screenshots")


class Bot:
    def __init__(self, config, dry_run=False):
        self.cfg = config
        self.dry_run = dry_run
        self.window = GameWindow(config["window_title"], config.get("process_name"))
        self.matcher = Matcher(TEMPLATE_DIR, config.get("match_threshold", 0.85))
        self.eyes = Perceiver(self.matcher, config)

        hk = config.get("hotkeys", {})
        self.stop_key, self.pause_key = hk.get("stop", "F12"), hk.get("pause", "F11")
        self.hotkeys = actions.HotkeyWatcher(self.stop_key, self.pause_key)
        self.activity = actions.UserActivity()
        self.user_resume = config.get("user_idle_resume", 120)

        self.quest_cfg = config["quest_tracker"]
        yg = config.get("yeonggi") or {}
        self.yeonggi = YeonggiTask(self, yg) if yg.get("enabled") else None

        scan_cfg = (config["quest_tracker"].get("scan") or {})
        self.tracker = TrackerScanner(self, scan_cfg) if scan_cfg.get("enabled") else None
        ms = config.get("mission") or {}
        self.mission = MissionTask(self, ms) if ms.get("enabled") else None
        self.mission_after_fails = ms.get("after_fails", 2)

        fm = config.get("farm") or {}
        self.farm_mode = bool(fm.get("enabled"))                     # 퀘스트 대신 사냥터만
        self.farm_fallback = bool(fm.get("fallback_when_blocked"))   # 퀘스트가 전부 막히면 안전 사냥터로
        self.farm = FarmTask(self, fm) if (self.farm_mode or self.farm_fallback) else None
        self._farm_until = 0.0
        self._hunt_check = 0.0
        self._last_map_close = 0.0

        idle = config["idle"]
        self.idle_seconds = idle.get("seconds", 6)
        self.motion_threshold = idle.get("motion_pct", 8.0)   # 크게 변한 픽셀 비율(%)

        self.paused = False
        self.waiting_user = False
        self.stop_event = threading.Event()     # GUI 등 외부에서 종료를 요청
        self.stats = {"deaths": 0, "missions": 0, "yeonggi": 0}
        self.alert_reason = ""
        self._last_state = None
        self._idle_since = None         # IDLE이 시작된 시각
        self._overlay_since = None
        self._overlay_esc = 0
        self._last_dialog_click = 0.0
        self._last_map_accept = 0.0
        self._last_death_click = 0.0
        self._want_hunt = False
        self._death_misses = 0
        self._deaths = []
        self._hunt_tries = 0
        self._hunt_press_at = 0.0
        # 퀘스트 클릭 검증
        self._click_at = 0.0
        self._verify = None             # 클릭 후 진행 확인 중이면 {'until': t, 'ok': bool}
        self._fails = 0
        self._last_size = None
        self._source = "본편"            # 지금 진행 중인 퀘스트의 출처(임무 탭 이름)
        self._need_switch = False       # 사망 후 다른 퀘스트로 전환해야 함
        self._moving_since = None       # 자동 이동이 시작된 시각 (이동 종료 감지용)
        self._last_moving = 0.0
        self._last_good = None
        self._clicked_xy = None

    # ---------- YeonggiTask가 쓰는 공통 인터페이스 ----------
    def _in_field(self, frame):
        return self.eyes.look(frame, time.monotonic()).in_field

    def _save_screen(self, frame, prefix):
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png")
        save_image(path, frame)
        log.warning("현재 화면 저장: %s", path)

    def _check_user(self):
        idle = self.activity.idle_seconds()
        if idle is not None and idle < self.user_resume:
            raise actions.UserInterrupt()

    def _click_frame(self, label, px, py):
        sx, sy = self.window.frame_to_screen(px, py)
        if self.dry_run:
            log.info("[dry-run] %s 클릭 예정 (%d, %d)", label, sx, sy)
            return
        self._check_user()
        actions.click(sx, sy)

    def _drag(self, label, x1, y1, x2, y2):
        sx1, sy1 = self.window.frame_to_screen(x1, y1)
        sx2, sy2 = self.window.frame_to_screen(x2, y2)
        if self.dry_run:
            log.info("[dry-run] %s 드래그 예정 (%d,%d → %d,%d)", label, sx1, sy1, sx2, sy2)
            return
        self._check_user()
        actions.drag(sx1, sy1, sx2, sy2)

    def _press(self, label, key):
        if self.dry_run:
            log.info("[dry-run] %s → %s 키 예정", label, key)
            return
        self._check_user()
        actions.press(key)

    def _alert(self, reason, frame=None):
        log.warning("일시정지: %s  (%s 키로 재개)", reason, self.pause_key)
        if frame is not None:
            self._save_screen(frame, "stuck")
        try:
            winsound.Beep(1000, 400)
        except RuntimeError:
            pass
        self.alert_reason = reason
        self.paused = True

    # ---------- 상태별 처리 ----------
    def _on_dialog(self, snap, now):
        """NPC 대화: '넘기기 ESC' 버튼이 보일 때만 ESC로 스킵한다 (필드에서 ESC는 메뉴를 열기 때문)."""
        dc = self.cfg.get("dialog") or {}
        if now - self._last_dialog_click < dc.get("gap", 1.0):
            return
        if dc.get("method", "key") == "key":
            log.info("대화 스킵: ESC (일치도 %.2f)", snap.dialog.score)
            self._press("대화 스킵", dc.get("key", "esc"))
        else:
            log.info("대화 스킵: 클릭 (일치도 %.2f)", snap.dialog.score)
            self._click_frame("대화 넘기기", snap.dialog.x, snap.dialog.y)
        self._last_dialog_click = now
        self._fails = 0

    def _on_dead(self, snap, now):
        """사망 화면. 오른쪽 '나가기'를 눌러 복귀한다. 유료 '즉시 부활'(가운데)은 절대 누르지 않는다."""
        d = self.cfg.get("death") or {}
        if now - self._last_death_click < d.get("retry", 3.0):
            return
        self._last_death_click = now
        # 지역마다 버튼 구성이 다르다: 무료 버튼(나가기 / 거점 이동 후 부활)만 템플릿으로 찾아 누른다
        for tpl in d.get("free_buttons", []):
            m = self.matcher.find(snap.frame, tpl, d.get("button_region"), d.get("button_threshold", 0.8))
            if m:
                log.info("사망 → %s 클릭 (일치도 %.2f)", tpl, m.score)
                self._click_frame("사망 복귀", m.x, m.y)
                self._want_hunt = True          # 복귀 후 자동 사냥으로 전환
                self._hunt_tries = 0
                self._hunt_press_at = 0.0
                self._death_misses = 0
                if self.farm:
                    self.farm.on_death(now)          # 더 낮은 사냥터로 (사냥터 모드/폴백 둘 다)
                if not self.farm_mode:
                    self._block_current_quest(now)
                self.stats["deaths"] += 1
                self._deaths = [t for t in self._deaths if now - t < d.get("streak_window", 600)] + [now]
                if len(self._deaths) >= d.get("streak_limit", 3):
                    self._alert(f"{d.get('streak_window', 600) // 60}분 안에 {len(self._deaths)}번 사망 "
                                "(레벨/장비에 비해 위험한 지역일 수 있음)")
                    self._deaths = []
                return
        self._death_misses += 1
        log.warning("사망 화면인데 무료 복귀 버튼을 찾지 못함 (%d회)", self._death_misses)
        if self._death_misses == 3:
            self._alert("사망 화면의 복귀 버튼을 모름 (유료 '즉시 부활'은 누르지 않음)", snap.frame)

    def _block_current_quest(self, now):
        """사망한 퀘스트(진행 중이던 임무 탭)를 한동안 멈추고, 복귀 후 다른 퀘스트로 전환한다."""
        if not self.mission:
            return
        secs = (self.cfg.get("death") or {}).get("block_seconds", 1800)
        self.mission.block(self._source, secs)
        self._need_switch = True
        log.info("사망 → '%s' 퀘스트를 %d분간 중단하고 다른 퀘스트로 전환", self._source, secs // 60)

    def _run_mission(self, now):
        ok = self.mission.run(now)
        self.stats["missions"] += ok
        if ok and self.mission.last_tab:
            self._source = self.mission.last_tab
        self._idle_since = None
        self._verify = None
        self._click_at = time.monotonic()
        return ok

    def _watch_move_end(self, snap, now):
        """자동 이동이 끝나면(충분히 이동한 뒤 이동 표시가 사라짐) 자동 사냥을 켜도록 요청한다."""
        ah = self.cfg.get("autohunt") or {}
        if not ah.get("after_move", True):
            return
        if snap.state == "MOVING":
            if self._moving_since is None:
                self._moving_since = now
            self._last_moving = now
            return
        if self._moving_since is None or now - self._last_moving < ah.get("end_delay", 1.5):
            return
        moved = self._last_moving - self._moving_since
        self._moving_since = None
        if moved >= ah.get("min_move", 3.0) and not snap.auto_on and not self._want_hunt:
            log.info("자동 이동 종료 (%.0f초 이동) → 자동 사냥 켜기", moved)
            self._want_hunt = True
            self._hunt_tries = 0
            self._hunt_press_at = 0.0

    def _ensure_hunt(self, snap, now):
        """사망 복귀 후 자동 사냥(T)이 꺼져 있으면 켠다. 켜졌는지 색으로 확인하고 최대 3번만 누른다."""
        if snap.auto_on:
            log.info("자동 사냥 켜짐 확인")
            self._want_hunt = False
            return
        if now - self._hunt_press_at < 3.0:
            return
        if self._hunt_tries >= 3:
            self._alert("자동 사냥(T)을 3번 눌러도 켜지지 않음", snap.frame)
            self._want_hunt = False
            return
        self._hunt_tries += 1
        log.info("자동 사냥 꺼짐 → T 키 (%d/3)", self._hunt_tries)
        self._press("자동 사냥", "t")
        self._hunt_press_at = now

    def _run_yeonggi_if_due(self, snap, now):
        """영기 수련은 전투 중이 아닌 필드에서만. 실행했으면 True."""
        if not (self.yeonggi and self.yeonggi.due(now) and snap.state in ("IDLE", "MOVING")):
            return False
        try:
            self.yeonggi.run(now)
            self.stats["yeonggi"] += 1
        finally:
            self._idle_since = None
            self._verify = None
        return True

    def _try_map_accept(self, snap, now):
        """세계지도의 '진행 가능한 임무' 수락 화면(트래커의 '서사 임무 수락 가능!'을 누르면 열림)이면 '수락'을 누른다.
        제목(세계지도)과 수락 버튼이 둘 다 보일 때만 누른다."""
        ac = self.cfg.get("map_accept") or {}
        if not ac:
            return False
        if now - self._last_map_accept < ac.get("gap", 3.0):
            return True
        title = self.matcher.find(snap.frame, ac["title"], ac.get("title_region"))
        if not title:
            return False
        button = self.matcher.find(snap.frame, ac["button"], ac.get("button_region"))
        what = "수락"
        if not button and ac.get("go_button"):          # 이미 수락된 퀘스트면 '자동 진행'으로 이동 시작
            button = self.matcher.find(snap.frame, ac["go_button"], ac.get("button_region"))
            what = "자동 진행"
        if not button:
            return False
        log.info("퀘스트 화면(세계지도) → %s 클릭 (일치도 %.2f)", what, button.score)
        self._click_frame("퀘스트 " + what, button.x, button.y)
        self._last_map_accept = now
        self._fails = 0
        return True

    def _on_overlay(self, snap, now):
        """필드가 아닌 화면. 컷신/메뉴가 스스로 끝날 시간을 주고, 길어지면 ESC로 닫아본다."""
        if self.farm and (self.farm_mode or now < self._farm_until) and now - self._last_map_close > 4.0 and self.farm.nav.map_open(snap.frame):
            # 사냥터 이동 도중 중단돼 세계지도가 열려 있으면 M으로 닫는다 (ESC는 지도를 못 닫음)
            log.info("사냥터 모드: 열려 있는 세계지도를 닫음")
            self._last_map_close = now
            self._press("지도 닫기", "m")
            self._overlay_since = None
            return
        if self._try_map_accept(snap, now):
            self._overlay_since = None          # 수락 화면은 ESC로 닫지 않는다 (수락 없이 닫히는 것 방지)
            return
        ov = self.cfg.get("overlay") or {}
        if self._overlay_since is None:
            self._overlay_since = now
        waited = now - self._overlay_since
        if waited < ov.get("grace", 8.0):
            return
        if self._overlay_esc >= ov.get("max_esc", 3):
            self._alert(f"필드가 아닌 화면이 {waited:.0f}초 지속 (알 수 없는 화면)", snap.frame)
            self._overlay_esc = 0
            self._overlay_since = None
            return
        log.info("필드가 아닌 화면 %.0f초 지속 → ESC (%d/%d)", waited,
                 self._overlay_esc + 1, ov.get("max_esc", 3))
        self._press("화면 닫기", "esc")
        self._overlay_esc += 1
        self._overlay_since = now - ov.get("grace", 8.0) + ov.get("esc_gap", 4.0)

    def _on_field(self, snap, now):
        """필드 화면: COMBAT/MOVING은 아무것도 하지 않고, IDLE이 이어질 때만 퀘스트를 누른다."""
        self._watch_move_end(snap, now)
        if self._want_hunt:
            self._ensure_hunt(snap, now)
            return
        if self._need_switch and snap.state in ("IDLE", "MOVING"):
            self._need_switch = False
            if self._run_mission(now):
                self._fails = 0
            else:
                log.warning("전환할 다른 퀘스트가 없음 → 차단 시간이 끝날 때까지 자동 사냥만 유지")
                if self.farm_fallback:
                    secs = (self.cfg.get("death") or {}).get("block_seconds", 1800)
                    self._farm_until = now + secs
                    self.farm.need_travel = True
                    self.farm.next_try = 0.0
                    log.info("폴백: 퀘스트가 모두 막힘 → %d분간 전투력에 맞는 안전 사냥터에서 사냥", secs // 60)
            return
        if self.farm_fallback and now < self._farm_until and self.mission and self.mission.is_blocked("본편"):
            self.farm.tick(snap, now)
        if self.farm_mode:
            self._need_switch = False
            if self._run_yeonggi_if_due(snap, now):
                return
            self.farm.tick(snap, now)
            if (not snap.auto_on and snap.state != "MOVING" and not self._want_hunt
                    and now - self._hunt_check > 15 and now >= self.farm.next_try):
                self._hunt_check = now
                log.info("사냥터 모드: 자동 사냥이 꺼져 있음 → 켜기")
                self._want_hunt = True
                self._hunt_tries = 0
                self._hunt_press_at = 0.0
            return
        # 영기 수련은 전투 중이 아닌 필드에서만
        if self._run_yeonggi_if_due(snap, now):
            return

        progressing = (snap.state in ("MOVING", "COMBAT") or snap.world_motion > self.motion_threshold)
        if progressing:
            self._idle_since = None
            if self._verify and not self._verify["ok"]:
                log.info("  진행 확인됨 (%s, 변화량 %.1f)", snap.state, snap.world_motion)
                self._verify["ok"] = True
                if snap.state in ("MOVING", "COMBAT"):
                    self._last_good = self._clicked_xy
            if snap.state in ("MOVING", "COMBAT"):
                self._fails = 0
            return

        if self._idle_since is None:
            self._idle_since = now
        if self._verify and snap.tracker_changed and not self._verify["ok"]:
            log.info("  진행 확인됨 (퀘스트 목록 변화)")
            self._verify["ok"] = True
            self._fails = 0

        # 클릭 후 확인 구간 판정
        if self._verify and now >= self._verify["until"]:
            if not self._verify["ok"]:
                self._fails += 1
                log.warning("  퀘스트 클릭 후 진행 없음 (연속 %d회)", self._fails)
            self._verify = None

        if self._verify:                # 아직 확인 중
            return
        if not self.quest_cfg.get("enabled", True):
            return
        if now - self._idle_since < self.idle_seconds:
            return
        if now - self._click_at < self.quest_cfg.get("cooldown", 8):
            return
        self._click_quest(snap, now)

    def _click_quest(self, snap, now):
        qt = self.quest_cfg
        limit = qt.get("max_retries", 4)
        # 사망으로 본편이 차단된 동안에는 트래커(=본편)를 누르지 않고 임무 화면의 다른 퀘스트만 시도한다
        if self.mission and self.mission.is_blocked("본편"):
            if now >= self.mission.next_ok:
                self._run_mission(now)
            else:
                self._click_at = now
            return
        # 첫 클릭이 통하지 않으면 트래커를 스크롤하며 읽어(OCR) 보이지 않던 퀘스트까지 파악하고, 본편 외의 진행 가능한 퀘스트를 누른다
        if self.tracker and 1 <= self._fails < self.mission_after_fails:
            if now - self.tracker.scanned_at > 120:
                self.tracker.scan()
            cands = self.tracker.candidates(skip_kinds=("main",))
            if cands:
                self.tracker.click(cands[(self._fails - 1) % len(cands)])
                self._click_at = time.monotonic()
                self._idle_since = None
                self._verify = {"until": self._click_at + qt.get("verify_seconds", 7.0), "ok": False}
                self._clicked_xy = None
                return
        # 트래커 클릭이 통하지 않으면 임무 화면(J)에서 본편 → 서사 → 일일 → 주간 순으로 진행 가능한 퀘스트를 찾는다
        if (self.mission and self._fails >= self.mission_after_fails and now >= self.mission.next_ok):
            ok = self._run_mission(now)
            if ok:
                self._fails = 0
            return
        if self._fails >= limit:
            self._alert(f"퀘스트를 눌러도 {self._fails}번 연속 진행되지 않음", snap.frame)
            self._fails = 0
            return

        # 후보 좌표를 실패할 때마다 바꿔 가며 누른다: 직전에 성공한 좌표 → 템플릿 → 설정의 후보들
        cands = []
        if self._last_good:
            cands.append(("직전 성공 좌표", self._last_good))
        match = self.matcher.find(snap.frame, qt.get("template"), qt.get("search_region"))
        if match:
            dx, dy = qt.get("click_offset", [0, 0])
            cands.append((f"템플릿 {match.score:.2f}", (match.x + dx, match.y + dy)))
        for rx, ry in qt.get("candidates", [qt["click"]]):
            cands.append(("후보 좌표", (rx * self.window.frame_w, ry * self.window.frame_h)))
        uniq = []
        for name, (x, y) in cands:
            if all(abs(x - ux) > 20 or abs(y - uy) > 12 for _, (ux, uy) in uniq):
                uniq.append((name, (x, y)))
        name, (x, y) = uniq[self._fails % len(uniq)]
        log.info("퀘스트 클릭 (%s %d,%d / 실패누적 %d)", name, x, y, self._fails)
        self._click_frame("퀘스트", x, y)
        self._clicked_xy = (x, y)

        self._click_at = now
        self._idle_since = None
        self._verify = {"until": now + qt.get("verify_seconds", 7.0), "ok": False}

    # ---------- 루프 ----------
    def _tick(self):
        frame = self.window.grab()
        now = time.monotonic()
        snap = self.eyes.look(frame, now)
        state = snap.state
        if state != self._last_state:
            log.info("상태: %s → %s", self._last_state or "-", state)
            self._last_state = state
        if state != "OVERLAY":
            self._overlay_since = None
            self._overlay_esc = 0

        if state == "DEAD":
            self._on_dead(snap, now)
        elif state == "DIALOG":
            self._on_dialog(snap, now)
        elif state == "OVERLAY":
            self._on_overlay(snap, now)
        else:
            self._on_field(snap, now)
        log.debug("%s moving=%s combat=%s motion=%.1f tracker=%s fails=%d", state,
                  snap.auto_moving, snap.combat, snap.world_motion, snap.tracker_changed, self._fails)

    def run(self):
        from version import VERSION
        log.info("버전 v%s", VERSION)
        log.info("시작 (v2 상태 머신): '%s' (%s 종료 / %s 일시정지)%s", self.cfg["window_title"],
                 self.stop_key, self.pause_key, "  [dry-run]" if self.dry_run else "")
        interval = self.cfg.get("loop_interval", 0.5)
        time.sleep(2.0)
        self.activity.ignore_until_now()   # 실행 직전의 입력(브리지 포커스 등)은 사용자 조작이 아님
        waiting_logged = False
        while True:
            if self.stop_event.is_set():
                log.info("종료 요청")
                return
            if self.hotkeys.pressed(self.stop_key):
                log.info("종료 키 입력")
                return
            if self.hotkeys.pressed(self.pause_key):
                self.paused = not self.paused
                log.info("일시정지" if self.paused else "재개")
                if not self.paused:
                    self.alert_reason = ""
                    self.activity.ignore_until_now()
                    self.waiting_user = False
                    self._idle_since = self._verify = None
            if self.paused:
                time.sleep(0.1)
                continue

            if self.window.locate():
                size = (self.window.width, self.window.height)
                if size != self._last_size:
                    ratio = size[0] / size[1]
                    log.info("게임 창 크기: %dx%d (배율 %.2f, 비율 %.3f)%s", size[0], size[1],
                             size[0] / 1920, ratio,
                             "" if abs(ratio - 16 / 9) < 0.02 else "  ⚠ 16:9가 아니라 UI 배치가 달라질 수 있음")
                    self._last_size = size
                    self.eyes.reset()
                    self.matcher.set_degrade(size[0] / 1920)
            if (not self.window.locate() or self.window.is_minimized()
                    or not self.window.is_foreground()):
                if not waiting_logged:
                    log.info("게임 창이 없거나 맨 앞에 있지 않아 대기 중...")
                    waiting_logged = True
                self._idle_since = self._verify = None
                time.sleep(0.5)
                continue
            if waiting_logged:
                log.info("게임 창 감지 (%dx%d)", self.window.width, self.window.height)
                waiting_logged = False
                self.activity.ignore_until_now()

            if not self.dry_run:
                idle = self.activity.idle_seconds()
                if idle is not None and idle < self.user_resume:
                    if not self.waiting_user:
                        log.info("사용자 조작 감지 → %d초 동안 조작이 없으면 재개", self.user_resume)
                        self.waiting_user = True
                    self._idle_since = self._verify = None
                    time.sleep(0.5)
                    continue
                if self.waiting_user:
                    log.info("조작 없음 → 재개")
                    self.waiting_user = False

            try:
                self._tick()
            except actions.UserInterrupt:
                log.info("동작 중 사용자 조작 감지 → 중단")
                self._idle_since = self._verify = None
            time.sleep(interval)
