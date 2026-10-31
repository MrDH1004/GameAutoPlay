"""상태 머신 기반 메인 루프.

화면 상태(DEAD > DIALOG > OVERLAY > COMBAT > MOVING > IDLE)에 따라 한 가지 동작만 한다.
퀘스트 클릭은 'IDLE이 오래 지속될 때만' 하고, 클릭한 뒤에는 실제로 진행됐는지 확인해서
진행이 없으면 단계적으로 복구한다 (같은 클릭을 무한 반복하지 않는다).
"""
import logging
import os
import re
import threading
import time
import winsound
from datetime import datetime

from bot import actions
from bot.cua_input import CuaInputError
from bot.vision import Matcher, save_image
from bot.window import GameWindow
from bot.yeonggi import YeonggiTask

from .farm import GUIDE_CLOSE, FarmTask, StopRequested, read_power
from .notify import Notifier
from .rewards import RewardTask
from .preset import PresetSwitcher
from .enhance import EnhanceTask
from .shop import PotionShop
from .collect import CollectTask
from .farm import BotDriver
from .safety import DeathBook, hp_ratio, target_fill
from . import selfcheck
from .mission import MissionTask
from .perception import Perceiver
from .tracker import TrackerScanner
from .boss_hunt import BossHuntTask
from .nakhwa_boss import NakhwaBossTask

log = logging.getLogger(__name__)

from app_paths import ROOT
TEMPLATE_DIR = os.path.join(ROOT, "templates")
SCREENSHOT_DIR = os.path.join(ROOT, "screenshots")


def _same_map_read(left, right):
    """지도명 뒤 대사 혼입·첫 글자 누락을 허용하되 짧은 잡음은 같은 지도로 보지 않는다."""
    a = re.sub(r"[^0-9A-Za-z가-힣]", "", str(left or ""))
    b = re.sub(r"[^0-9A-Za-z가-힣]", "", str(right or ""))
    shorter, longer = sorted((a, b), key=len)
    return bool(len(shorter) >= 3 and shorter in longer)


class Bot:
    def __init__(self, config, dry_run=False):
        self.cfg = config
        self.dry_run = dry_run
        self.window = GameWindow(config["window_title"], config.get("process_name"))
        cua_cfg = config.get("cua_input") or {}
        if cua_cfg.get("enabled", False) and not dry_run:
            actions.configure_cua(self.window, cua_cfg)
            capture_mode = "가려진 창 Cua 캡처" if cua_cfg.get("capture_enabled", False) else "화면 캡처 · 입력할 때만 Cua"
            log.info("입력 방식: Cua Driver (%s · 클릭 시 전면 전달 후 마우스 즉시 원위치 복원)", capture_mode)
        self.matcher = Matcher(TEMPLATE_DIR, config.get("match_threshold", 0.85))
        self.eyes = Perceiver(self.matcher, config)

        hk = config.get("hotkeys", {})
        self.stop_key, self.pause_key = hk.get("stop", "F12"), hk.get("pause", "F11")
        self.hotkeys = actions.HotkeyWatcher(self.stop_key, self.pause_key)
        self.activity = actions.UserActivity()
        self.user_resume = config.get("user_idle_resume", 120)
        self.user_detect = bool(config.get("user_detect", True))      # 제어판 토글: 사용자 조작 감지

        self.quest_cfg = config["quest_tracker"]
        yg = config.get("yeonggi") or {}
        self.yeonggi = YeonggiTask(self, yg) if yg.get("enabled") else None

        scan_cfg = (config["quest_tracker"].get("scan") or {})
        self.tracker = TrackerScanner(self, scan_cfg) if scan_cfg.get("enabled") else None
        ms = config.get("mission") or {}
        self.mission = MissionTask(self, ms) if ms.get("enabled") else None
        self.mission_after_fails = ms.get("after_fails", 2)
        self._mission_priority = (self.mission.priority[0] if self.mission else "본편")
        self._mission_priority_pending = bool(self.mission and ms.get("start_priority", False)
                                              and self._mission_priority != "본편")

        fm = config.get("farm") or {}
        self.farm_mode = bool(fm.get("enabled"))                     # 퀘스트 대신 사냥터만
        self._cfg_farm_mode = self.farm_mode                         # 설정에 적힌 값 (수동 앵벌 모드를 끄면 이 값으로 되돌림)
        self.manual_farm = False                                     # 제어판 버튼으로 켠 앵벌 모드
        self._manual_farm_request = None                             # 버튼이 남긴 요청: True 켜기 / False 끄기 / None 없음
        nm = config.get("normal_mode") or {}
        self.normal_mode = bool(nm.get("enabled", False))            # 현재 위치를 유지하는 일반 앵벌
        self._cfg_normal_mode = self.normal_mode
        self._normal_mode_request = None
        dm = config.get("daily_mode") or {}
        self.daily_mode = bool(dm.get("enabled", False))             # 일일 임무만 진행, 본편·서사·주간 제외
        if self.mission and dm.get("selected_titles"):
            self.mission.set_selected_daily_titles(dm.get("selected_titles"))
        self._cfg_daily_mode = self.daily_mode
        self._daily_mode_request = None
        sm = config.get("story_mode") or {}
        self.story_mode = bool(sm.get("enabled", False))             # 현재 전투력에 맞는 서사 임무만 진행
        self._cfg_story_mode = self.story_mode
        self._story_mode_request = None
        self._daily_missing_count = 0                              # 메인 트래커 전체에서 [일일]이 연속으로 안 보인 횟수
        self._daily_missing_at = 0.0
        self._daily_absence_confirmed = False
        self._daily_picker_request = None
        self.daily_picker_state = {"revision": 0, "status": "idle", "items": [], "message": ""}
        self._mode_persist_request = None                            # 봇 자동 모드 전환을 GUI JSON과 동기화
        self._normal_origin = None                                   # 추천 사냥터 피신 뒤 돌아올 DB 지점
        self._normal_returning = False
        self._normal_return_at = 0.0
        self._normal_return_quiet = 0
        if getattr(self, "daily_mode", False):
            self.farm_mode = False
            self.normal_mode = False
            self.story_mode = False
            self._mission_priority = "일일"
            self._mission_priority_pending = bool(self.mission)
        elif self.story_mode:
            self.farm_mode = False
            self.normal_mode = False
            self._mission_priority = "서사"
            self._mission_priority_pending = bool(self.mission)
        elif self.normal_mode:
            self.farm_mode = False                                  # 두 모드는 동시에 움직이지 않는다
            self._mission_priority_pending = False                  # 일반 모드는 임무 창을 열지 않는다
        self.farm_fallback = bool(fm.get("fallback_when_blocked"))   # 퀘스트가 전부 막히면 안전 사냥터로
        self.farm = FarmTask(self, fm) if (self.farm_mode or self.normal_mode or self.farm_fallback) else None
        self._farm_until = 0.0
        self._gate_check = 0.0
        self._retry_quest = None        # 사망 후 재도전할 퀘스트 {'source', 'at'}
        self._quest_name = None         # 마지막으로 읽은 진행 중 퀘스트 제목
        self._tracker_at = 0.0
        rw = config.get("rewards") or {}
        self.rewards = RewardTask(BotDriver(self), rw) if rw.get("enabled", True) else None
        if self.rewards:
            self.rewards.next_at = time.monotonic() + float(rw.get("first_delay", 120))
            self.rewards.on_new_skill = lambda a, b: self.notifier.send(
                f"새 도술 {b - a}개 습득 ({a}→{b}). 도술 조합/프리셋을 다시 보세요.", key="skill")
        cl = config.get("collect") or {}
        self.collect = CollectTask(self, cl) if (cl.get("enabled", True) and self.farm) else None
        self._collect_pending = False   # 앵벌이 시작되면 수집 몬스터로 먼저 가 본다
        self._collect_check = 0.0       # 수집 사냥 교대 조건을 마지막으로 본 시각
        self._walk_started = 0.0        # 수집 이동을 시작한 시각 (도착할 때까지 자동 사냥 T 금지, 0이면 이동 중 아님)
        self._walk_last = 0.0           # 이동 바를 마지막으로 본 시각
        ps = config.get("potion_shop") or {}
        self.potion_shop = PotionShop(self, ps) if ps.get("enabled", True) else None
        en = config.get("enhance") or {}
        self.enhance = EnhanceTask(self, en) if en.get("enabled") else None
        if self.enhance:
            self.enhance.next_at = time.monotonic() + 300
        self._gone_since = None
        pc = config.get("preset") or {}
        self.preset = PresetSwitcher(self, pc) if pc.get("enabled") else None
        self.secret_realm_combat_cfg = config.get("secret_realm_combat") or {}
        self.secret_realm_preset = None
        if self.secret_realm_combat_cfg.get("enabled", True):
            self.secret_realm_preset = PresetSwitcher(self, {
                "normal": int(self.secret_realm_combat_cfg.get("aoe_preset", 4)),
                "boss": int(self.secret_realm_combat_cfg.get("single_preset", 2)),
            })
        self._secret_preset_current = None
        self._secret_preset_switched_at = -1e9
        self._daily_secret_started_wall = self._load_daily_secret_started()
        self._daily_secret_exit_pending = 0.0
        self.deathbook = DeathBook(config.get("death") or {})
        self.notifier = Notifier(config.get("notify"))
        st = config.get("stall") or {}
        self.stall_secs = float(st.get("minutes", 10)) * 60
        self._last_progress = time.monotonic()
        self._stalls = 0
        self.power = None               # 마지막으로 읽은 전투력
        from .risk import RiskGuard
        self.risk_guard = RiskGuard(float((config.get("farm") or {}).get("margin", 1.5)),
                                    enabled=not dry_run and (config.get("hp") or {}).get("startup_risk_check", True))
        self._power_at = 0.0
        self.started_wall = time.time()
        self.selfcheck = ""
        hp = config.get("hp") or {}
        self.hp_cfg = hp
        self._potion_at = 0.0
        self._potion_idx = 0
        self._potion_check = None       # (확인 시각, 누를 때 HP, 키)
        self._potion_miss = 0
        self._potion_warned = 0.0
        self._low_hp_warned = 0.0
        self.hp = None
        self.startup_state = None       # 시작 점검 결과 (bot2/startup.py)
        self._map_name = ""             # 마지막으로 확인한 지도 이름
        self._map_observed_at = -1.0    # 같은 LiveStatus 프레임을 중복 판정하지 않음
        self._map_ocr_at = -1e9         # 봇 루프의 지도명 보조 OCR 시각
        self._secret_realm = False      # [비경]: 지도 이동 없이 일반 자동사냥만 유지
        self._secret_realm_base = ""   # 접두어만 OCR에서 빠진 경우를 일반 맵 복귀로 오인하지 않음
        self._normal_map_candidate = ""
        self._normal_map_confirm = 0    # 비경 종료는 같은 일반 지도명을 두 화면에서 확인
        self._nakhwa_check_at = 0.0     # 낙화 일일 처치 수를 다음에 확인할 시각
        self._nakhwa_progress = None
        self._nakhwa_recenter_pending = None  # 낙화 사망 뒤 거점 부활 완료 후 중앙 재진입
        self._nakhwa_scoreboard_opened = False
        self._nakhwa_daily_positioned = False  # 재시작해도 낙화 일일 사냥 지점 이동을 1회 보장
        self._daily_return_hunt_pending = False  # 전투 콘텐츠 퇴장 뒤 일반 맵 AUTO 복구 우선
        from .livestatus import LiveStatus
        self.live = LiveStatus(self, float(config.get("live_interval", 3.0)))     # 대시보드 실시간 상황판
        self._rest = None               # HP 낮음 휴식: None / "finish"(잡던 몹 마무리) / "off"(자동 사냥 끄는 중) / "rest"(회복 대기) / "on"(다시 켜는 중)
        self._rest_at = 0.0
        from .evade import Evader
        self.evader = Evader(BotDriver(self), int(self.hp_cfg.get('evade_dist_px', 900)),
                             int(self.hp_cfg.get('evade_min_dist_px', 560)),
                             int(self.hp_cfg.get('evade_min_meters', 40)))
        self._evade = None              # 회피로 이동한 지도 오프셋 (돌아올 때 반대로 이동). None = 회피 중 아님
        self._evade_pending = False     # T를 끈 뒤 회피 이동을 시작해야 함
        self._evade_request = False     # 제어판/명령의 수동 회피 요청
        self._evade_last = -1e9
        self._evade_prep_at = 0.0       # 자리 회피용 지역 정보를 마지막으로 미리 읽은 시각
        self._evade_prep_key = None     # 그때의 사냥터
        self._rest_empty_since = None   # 타겟 체력바가 완전히 비어 보이기 시작한 시각 (잠깐 깜빡임 무시용)
        self._rest_had_target = False   # 휴식 시작 때 타겟 체력바가 있었는지 (바가 사라지면 그 몹을 잡은 것)
        self._rest_press_at = 0.0
        self._rest_tries = 0
        self._seen_known = False        # 필드/대화/전투 등 아는 화면을 한 번이라도 봤는지
        self._ui_warned = False
        self._run_t0 = time.monotonic()
        self._hunt_check = 0.0
        self._last_map_close = 0.0
        self._clan_help_at = -1e9       # 문파 도움 아이콘 마지막 클릭 시각
        self._common_event_exit_pending = False
        self._common_event_exit_at = -1e9
        self.boss_hunt = BossHuntTask(self, config.get("boss_hunt") or {})
        self.nakhwa_boss = NakhwaBossTask(self, config.get("nakhwa_boss") or {})

        idle = config["idle"]
        self.idle_seconds = idle.get("seconds", 6)
        self.motion_threshold = idle.get("motion_pct", 8.0)   # 크게 변한 픽셀 비율(%)

        self.paused = False
        self.waiting_user = False
        self._user_wait_until = 0.0
        self._manual_focus_requested = False
        self._manual_focus_try_at = -1e9
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
        self._death_since = 0.0         # 사망 화면에서 버튼을 기다리기 시작한 시각
        self._death_alerted = False
        self._death_open = False        # 이번 사망 화면을 이미 기록했는지 (화면이 남아 다시 누를 때 중복 기록 방지)
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
        return path

    def set_user_detect(self, on):
        """제어판 토글: 사용자 조작 감지 켜기/끄기. 끄면 마우스·키보드를 써도 봇이 양보하지 않는다."""
        on = bool(on)
        if on == self.user_detect:
            return
        self.user_detect = on
        if on:
            self.activity.ignore_until_now()            # 켜는 순간의 조작(버튼 클릭)은 사용자 조작으로 치지 않음
        else:
            self.waiting_user = False                   # 조작 대기 중이었어도 바로 풀림
            self._user_wait_until = 0.0
        log.warning("사용자 조작 감지 %s (버튼)", "켬" if on else "끔 → 마우스·키보드를 써도 봇이 멈추지 않음")

    def _check_user(self):
        watcher = getattr(self, "hotkeys", None)
        if watcher and watcher.pressed(self.stop_key):
            self.stop_event.set()
            raise StopRequested()
        if watcher and watcher.pressed(self.pause_key):
            self.paused = True
            raise actions.UserInterrupt()
        if getattr(self, "stop_event", None) and self.stop_event.is_set():
            raise StopRequested()
        if getattr(self, "paused", False):
            raise actions.UserInterrupt()
        window = getattr(self, "window", None)
        if window is not None and (not window.locate() or window.is_minimized()
                                   or (not getattr(window, "background_capture", False)
                                       and not getattr(window, "cua_input_enabled", False)
                                       and not window.is_foreground())):
            raise actions.UserInterrupt()
        if not self.user_detect:
            return
        idle = self.activity.idle_seconds()
        if idle is not None and idle < self.user_resume:
            raise actions.UserInterrupt()

    def _wait_for_user_control(self, now):
        """마지막 사용자 입력부터 설정 시간 동안 자동 조작을 완전히 양보한다."""
        if self.dry_run or not self.user_detect:
            return False
        if self.cfg.get("yield_only_game_input", True) and not self.window.is_foreground():
            self.activity.ignore_until_now(0.3)
            return False
        idle = self.activity.idle_seconds()
        if idle is not None and idle < self.user_resume:
            # 매 루프마다 같은 입력으로 시간이 늘어나지 않도록 이미 지난 idle만큼 뺀다.
            deadline = now + self.user_resume - idle
            self._user_wait_until = max(self._user_wait_until, deadline)
            if not self.waiting_user:
                log.info("사용자 조작 감지 → 마지막 조작부터 %d초 대기", self.user_resume)
                self.waiting_user = True
        if self.waiting_user and now < self._user_wait_until:
            self._idle_since = self._verify = None
            return True
        if self.waiting_user:
            log.info("사용자 조작 후 %d초 경과 → 자동 진행 재개", self.user_resume)
            self.waiting_user = False
            self._user_wait_until = 0.0
            boss_hunt = getattr(self, "boss_hunt", None)
            if boss_hunt:
                boss_hunt.on_user_resume(now)
        return False

    def _click_frame(self, label, px, py):
        if not self.dry_run:
            self._check_user()
        sx, sy = self.window.frame_to_screen(px, py)
        if self.dry_run:
            log.info("[dry-run] %s 클릭 예정 (%d, %d)", label, sx, sy)
            return
        actions.click(sx, sy)

    def _clan_help_tick(self, snap, now):
        """왼쪽 아래 문파 도움 아이콘이 보이면 한 번 누른다.

        오래된 프레임으로 잘못 누르지 않도록 입력 직전에 새 화면에서 필드 HUD와
        도움 아이콘을 모두 다시 확인한다. 클릭했으면 같은 틱의 다른 입력을 막는다.
        """
        cfg = self.cfg.get("clan_help") or {}
        if (not cfg.get("enabled", True) or not snap.in_field
                or getattr(snap, "state", "IDLE") not in ("IDLE", "COMBAT", "MOVING")):
            return False
        if now - self._clan_help_at < float(cfg.get("cooldown", 30)):
            return False
        template = cfg.get("template", "clan_help.png")
        region = cfg.get("region", [0.0, 0.68, 0.07, 0.20])
        threshold = float(cfg.get("threshold", 0.90))
        match = self.matcher.find(snap.frame, template, region, threshold)
        if not match:
            return False
        if self.dry_run:
            log.info("[dry-run] 문파 도움 클릭 예정 (일치도 %.2f)", match.score)
            self._clan_help_at = now
            return True

        self._check_user()
        fresh = self.window.grab()
        field = self.cfg.get("field_indicator") or {}
        if not self.matcher.find(fresh, field.get("template"), field.get("region")):
            log.debug("문파 도움 입력 직전 필드 HUD가 사라져 클릭 취소")
            return False
        match = self.matcher.find(fresh, template, region, threshold)
        if not match:
            log.debug("문파 도움 입력 직전 아이콘이 사라져 클릭 취소")
            return False
        log.info("문파 도움 요청 감지 → 클릭 (일치도 %.2f)", match.score)
        self._click_frame("문파 도움", match.x, match.y)
        self._clan_help_at = now
        return True

    def _drag(self, label, x1, y1, x2, y2):
        if not self.dry_run:
            self._check_user()
        sx1, sy1 = self.window.frame_to_screen(x1, y1)
        sx2, sy2 = self.window.frame_to_screen(x2, y2)
        if self.dry_run:
            log.info("[dry-run] %s 드래그 예정 (%d,%d → %d,%d)", label, sx1, sy1, sx2, sy2)
            return
        actions.drag(sx1, sy1, sx2, sy2)

    def _hotkey(self, label, combo):
        if self.dry_run:
            log.info("[dry-run] %s → %s 예정", label, combo)
            return
        self._check_user()
        actions.hotkey(combo)

    def _potion_key(self, frame, keys):
        """물약을 누를 키. 물약은 하단 Ctrl+1 칸에만 있으므로 potion_keys의 첫 키(기본 Ctrl+1)를 쓴다."""
        return keys[self._potion_idx % len(keys)]

    def _watch_hp(self, snap, now):
        """전투 중 HP가 낮으면 물약 키를 누른다. 아주 낮으면 알림."""
        c = self.hp_cfg
        if not c.get("enabled", True) or snap.state not in ("COMBAT", "IDLE", "MOVING"):
            return
        self.hp = hp_ratio(snap.frame)
        if self.hp <= 0.0:              # 바를 못 찾음(가려짐 등) → 판단하지 않음
            return
        if self._potion_check and now >= self._potion_check[0]:
            _, before, key = self._potion_check
            self._potion_check = None
            if self.hp > before + 0.03:
                self._potion_miss = 0
            else:
                self._potion_miss += 1
                if self._potion_miss >= 3 and now - self._potion_warned > 600:
                    self._potion_warned = now
                    log.warning("물약을 %d번 눌러도 HP가 오르지 않음 → 물약이 없거나 다른 키일 수 있음", self._potion_miss)
                    self.notifier.send("물약 부족 가능 (눌러도 HP가 오르지 않음)", snap.frame, key="potion")
        keys = c.get("potion_keys") or []
        # 평소 사냥 중 물약은 게임 자체 자동 물약에 맡기고, 헬퍼는 휴식·자리 회피로 쉬는 동안에만 누른다 (potion_normal: true면 예전처럼 평소에도 누름)
        if self._rest != "rest" and not c.get("potion_normal", False):
            keys = []
        below = c.get("rest_resume", 0.80) if self._rest == "rest" else c.get("potion_below", 0.45)
        if keys and self.hp < below and now - self._potion_at > c.get("potion_gap", 6.0):
            key = self._potion_key(snap.frame, keys)
            log.info("HP %.0f%% → 물약 %s", self.hp * 100, key)
            self._hotkey("물약", key)
            self._potion_at = now
            self._potion_idx += 1
            self._potion_check = (now + 2.5, self.hp, key)
        if self.hp < c.get("danger_below", 0.2) and now - self._low_hp_warned > 60:
            self._low_hp_warned = now
            log.warning("HP 위험 %.0f%%", self.hp * 100)
            self.notifier.send(f"HP 위험 {self.hp * 100:.0f}% (출처: {self._source})", snap.frame, key="hp")

    def _rest_tick(self, snap, now):
        """보스가 아닌 자동 사냥 중 HP가 rest_below(50%) 이하면: 잡던 몹까지 잡고 → 자동 사냥 끄기 →
        물약/자연 회복으로 rest_resume(100%) 이상 차면 → 자동 사냥 다시 켜기. 휴식 중이면 True (다른 행동 안 함)."""
        c = self.hp_cfg
        if not c.get("enabled", True) or not c.get("rest_enabled", True) or self.dry_run:
            self._rest = None
            return False
        hp = self.hp or 0.0
        # 자리 회피: HP가 evade_below(30%) 이하면 잡던 몹도 두고 바로 T를 끄고 몹이 적은 쪽으로 빠진다 (수동 요청도 같음)
        manual, self._evade_request = self._evade_request, False
        if self._rest in (None, "finish") and self._evade is None and not self._evade_pending:
            auto = (c.get("evade_enabled", True) and snap.auto_on and not snap.walking
                    and 0.0 < hp <= c.get("evade_below", 0.20) and now - self._evade_last > c.get("evade_cooldown", 60)
                    and not self._boss_near(snap, now))
            if manual or auto:
                log.warning("%s → 자동 사냥 끄고 자리 회피 (HP %.0f%%)", "수동 요청" if manual else "HP가 매우 낮음", hp * 100)
                self._rest, self._rest_at, self._rest_tries, self._rest_press_at = "off", now, 0, 0.0
                self._evade_pending, self._evade_last = True, now
                return True
        if self._rest is None:
            if snap.auto_on and 0.0 < hp <= c.get("rest_below", 0.5) and not self._boss_near(snap, now):
                self._rest, self._rest_at, self._rest_empty_since = "finish", now, None
                self._rest_had_target = target_fill(snap.frame) > 0.02 if getattr(snap, "frame", None) is not None else False
                log.info("HP %.0f%% → 잡던 몹까지만 잡고 자동 사냥 끄고 회복 (타겟 체력바 %s)", hp * 100,
                         "있음" if self._rest_had_target else "없음")
            return False
        self._last_progress = now               # 휴식은 멈춤이 아님
        if self._rest != "on" and self._boss_near(snap, now):
            log.info("보스 등장 → 휴식 취소, 자동 사냥 유지")
            self._rest, self._rest_tries, self._rest_press_at = "on", 0, 0.0
        if self._rest == "finish":
            # 잡던 몹을 다 잡았는지: 화면 상단 타겟 체력바가 비면(=그 몹이 죽음) 자동 사냥을 끈다.
            # 센 지역에서는 몹이 계속 붙어 '전투 상태'가 안 끝나므로 상태 대신 이 바를 본다.
            # 처음부터 타겟이 없거나(바 없음) 너무 오래 걸리면(rest_finish_max) 그냥 끈다.
            fill = target_fill(snap.frame) if getattr(snap, "frame", None) is not None else 0.0
            if fill > 0.02:
                self._rest_had_target = True
            waited = now - self._rest_at
            # 바가 '완전히' 사라진 상태가 0.8초 이어져야 처치로 본다. (예전에는 2% 이하면 처치로 봐서, 체력이
            # 조금 남은 몹을 두고 자동 사냥을 꺼 버렸다. 타겟 전환 순간의 깜빡임도 걸러낸다.)
            if fill > 0.0:
                self._rest_empty_since = None
            elif self._rest_empty_since is None:
                self._rest_empty_since = now
            gone = self._rest_empty_since is not None and now - self._rest_empty_since >= 0.8
            if self._rest_had_target:
                done = gone
            else:
                done = snap.state != "COMBAT"                    # 처음부터 타겟이 없었으면 전투 상태가 끝났을 때
            # 사용자 지시: 잡던 몹은 죽일 때까지 끈다(HP나 시간 때문에 도중에 끄지 않음).
            # 다만 타겟 바가 안 줄고 멈춘 비정상 상태(타겟을 못 때림 등)로 무한 대기하지 않게 아주 긴 상한만 둔다.
            if waited >= float(c.get("rest_finish_max", 300)):
                done = True
            if not done:
                return True
            log.info("잡던 몹 마무리 %s → 자동 사냥 끔 (%d초)", "완료" if gone else "시간 초과", waited)
            self._rest, self._rest_tries, self._rest_press_at = "off", 0, 0.0
        if self._rest == "off":
            if self._rest_toggle(snap, now, want_on=False):
                return True
            if self._evade_pending:
                self._evade_pending = False
                try:
                    # 자리 회피는 모드와 무관하게 현재 실제 지도 도로만 사용한다.
                    self._evade = self.evader.flee(self.window.grab())
                except StopRequested:
                    raise
                except actions.UserInterrupt:
                    raise
                except Exception as e:                       # noqa: BLE001
                    log.warning("자리 회피 중 오류: %s", e)
                    self._evade = None
            self._rest, self._rest_at = "rest", now
            resume = c.get("evade_resume", 1.0) if self._evade else c.get("rest_resume", .80)
            log.info("자동 사냥 끔 → HP %.0f%%까지 회복 대기 (물약, 최대 %d초)",
                     min(resume, 1.0) * 100, c.get("rest_max", 600))
            return True
        if self._rest == "rest":
            why = None
            resume = c.get("evade_resume", 1.0) if self._evade else c.get("rest_resume", .80)
            if hp >= min(resume, 1.0) - .005:
                why = f"HP {hp * 100:.0f}% 회복"
            elif now - self._rest_at > c.get("rest_max", 600):
                why = f"{c.get('rest_max', 600):.0f}초 동안 회복이 덜 됨(HP {hp * 100:.0f}%)"
            elif snap.state == "COMBAT" and 0.0 < hp < c.get("rest_fight_below", 0.15):
                why = f"쉬는 중 공격받아 HP {hp * 100:.0f}%"
            if why is None:
                return True
            if self._evade:
                off, self._evade = self._evade, None
                if "공격받아" in why:
                    log.info("회복 중 공격받음 → 원래 자리로 돌아가지 않고 이 자리에서 사냥")
                elif isinstance(off, dict) and off.get("teleported") and off.get("return_normal"):
                    self._return_normal_origin(now)
                elif isinstance(off, dict) and off.get("teleported"):
                    log.info("무료 순간이동으로 회피했으므로 위험한 원래 자리로 복귀하지 않음")
                else:
                    try:
                        self.evader.back(off)
                    except actions.UserInterrupt:
                        raise
                    except Exception as e:                   # noqa: BLE001
                        log.warning("자리 복귀 중 오류: %s", e)
            log.info("%s → 자동 사냥 다시 켬", why)
            self._rest, self._rest_tries, self._rest_press_at = "on", 0, 0.0
        if self._rest == "on":
            if getattr(self, "_normal_returning", False):
                if snap.walking or snap.state == "MOVING" or now - self._normal_return_at < 4.0:
                    self._normal_return_quiet = 0
                    return True
                self._normal_return_quiet += 1
                if self._normal_return_quiet < 2:
                    return True
                self._normal_returning = False
                self._normal_return_quiet = 0
                log.info("일반 모드 원래 지역 복귀 완료 → 자동 사냥 재개")
            if self._rest_toggle(snap, now, want_on=True):
                return True
            log.info("자동 사냥 다시 켜짐")
            self._rest = None
        return False

    def _rest_toggle(self, snap, now, want_on):
        """T로 자동 사냥을 want_on 상태로 만든다. 아직 바꾸는 중이면 True, 끝났으면(또는 3번 실패) False."""
        if snap.auto_on == want_on:
            return False
        if now - self._rest_press_at < 3.0:
            return True
        what = "켜기" if want_on else "끄기"
        if self._rest_tries >= 3:
            log.warning("자동 사냥 %s: T를 3번 눌러도 안 바뀜 → 포기", what)
            return False
        self._rest_tries += 1
        log.info("자동 사냥 %s → T 키 (%d/3)", what, self._rest_tries)
        self._press("자동 사냥 " + what, "t")
        self._rest_press_at = now
        return True

    def _boss_near(self, snap, now):
        """보스 체력바가 보이거나 최근 10초 안에 보였으면 True."""
        if self.preset:
            if now - getattr(self.preset, "_last_boss", 0.0) < 10:
                return True
            return snap.state == "COMBAT" and self.preset.boss_visible(snap.frame)
        return snap.state == "COMBAT" and bool(self.matcher.find(snap.frame, "combat_boss.png", [0.35, 0.04, 0.3, 0.06]))

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
        try:
            self.notifier.send(f"일시정지: {reason}", frame if frame is not None else self.window.grab(), key="alert")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 상태별 처리 ----------
    def _on_dialog(self, snap, now):
        """실제로 확인한 대화/컷신 버튼 종류에 맞는 입력만 보낸다."""
        dc = self.cfg.get("dialog") or {}
        if now - self._last_dialog_click < dc.get("gap", 1.0):
            return
        name = getattr(snap, "dialog_name", "") or "대화 넘기기"
        if name == "대화 다음":
            log.info("대화 다음: 클릭 (일치도 %.2f)", snap.dialog.score)
            self._click_frame("대화 다음", snap.dialog.x, snap.dialog.y)
        elif name == "컷신 스킵" or dc.get("method", "key") == "key":
            log.info("%s: ESC (일치도 %.2f)", name, snap.dialog.score)
            self._press(name, dc.get("key", "esc"))
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
        if self._evade or self._evade_pending:
            # 회피 중 죽었으면 원래 지역으로 돌아갈 일은 없다 (거점에서 부활한다) → 복귀 기록을 지운다
            log.info("자리 회피 중 사망 → 복귀 기록 삭제")
            self._evade, self._evade_pending = None, False
        nakhwa_boss_death = getattr(self, "_source", "") == "낙화 요화선녀"
        nakhwa_death = ("낙화" in str(getattr(self, "_map_name", "")) or nakhwa_boss_death)
        if nakhwa_death:
            self._nakhwa_scoreboard_opened = False
        # 낙화에서는 전장을 나가지 않고 반드시 무료 '거점 이동 후 부활'만 사용한다.
        free_buttons = list(d.get("free_buttons", []))
        if nakhwa_death:
            free_buttons = [tpl for tpl in free_buttons if "base_revive" in tpl]
        # 지역마다 버튼 구성이 다르다: 무료 버튼(나가기 / 거점 이동 후 부활)만 템플릿으로 찾아 누른다
        for tpl in free_buttons:
            m = self.matcher.find(snap.frame, tpl, d.get("button_region"), d.get("button_threshold", 0.8))

            if m:
                log.info("사망 → %s 클릭 (일치도 %.2f)", tpl, m.score)
                self._click_frame("사망 복귀", m.x, m.y)
                if self._death_open:
                    log.info("같은 사망 화면이 아직 남아 있음 → 다시 클릭만 함 (사망 1회로 셈)")
                    return
                self._death_open = True
                self._want_hunt = True          # 복귀 후 자동 사냥으로 전환
                self._hunt_tries = 0
                self._hunt_press_at = 0.0
                self._death_misses = 0
                shot = self._save_screen(snap.frame, "death")
                farming = self.farm_mode or self.normal_mode or now < self._farm_until
                self._death_secs, n = self.deathbook.record("사냥터" if farming else self._source, self.power, shot)
                self.notifier.send(f"사망 ({self._source}, 최근 {n}회째) → 무료 복귀", snap.frame, key="death")
                retry_limit = int(d.get("retry_limit", 3))
                if nakhwa_boss_death:
                    self._nakhwa_recenter_pending = None
                    self._retry_quest = None
                    self._want_hunt = False
                    self.nakhwa_boss.retire_after_death()
                    log.warning("요화선녀 1회 사망 → 무료 거점 부활만 수행, 이번 이벤트 자동 재진입 중단")
                elif nakhwa_death:
                    self._nakhwa_recenter_pending = {"at": now + float(d.get("retry_delay", 6)), "tries": 0}
                    self._retry_quest = None
                    self._want_hunt = False
                    log.warning("낙화의 전장 사망 → 거점 부활 후 중앙 재진입 예약")
                elif farming:
                    if self.normal_mode:
                        self._normal_origin = None
                        self._evade_prep_at = -1e9
                        log.info("일반 모드 사망 → 무료 복귀 위치를 새 현재 위치로 사용")
                    elif self.collect and self.collect.current:
                        self.collect.on_death(now)   # 수집 몬스터에서 죽음: 그 몬스터 제외하고 다음(더 약한) 후보로
                        self._collect_pending = True
                    if not self.normal_mode:
                        self.farm.on_death(now)      # 사냥터에서 죽음: 한 단계 낮은 사냥터로
                elif d.get("policy", "retry") == "retry" and n <= retry_limit:
                    # 하던 퀘스트를 기억해 두고, 거점에서 부활한 뒤 같은 퀘스트를 다시 누른다
                    self._quest_name = self._read_quest_name(snap.frame) or self._quest_name
                    self._retry_quest = {"source": self._source, "at": now + float(d.get("retry_delay", 6))}
                    self._want_hunt = False
                    log.info("사망 → 거점 부활 후 같은 퀘스트 재도전 (%s '%s', %d/%d회)", self._source,
                             self._quest_name or "?", n, retry_limit)
                else:
                    if d.get("policy", "retry") == "retry":     # 중단 시간은 재도전 횟수를 넘긴 뒤부터 30분, 60분…
                        self._death_secs = min(float(d.get("block_seconds", 1800)) * 2 ** max(0, n - retry_limit - 1),
                                               float(d.get("max_block_seconds", 14400)))
                    if d.get("after_retry", "farm") == "farm" and self.farm:
                        self._start_farming(now, f"'{self._quest_name or self._source}' 재도전 실패")
                    else:
                        log.info("같은 퀘스트에서 %d번 사망 → 다른 퀘스트로 전환", n)
                        self._block_current_quest(now)
                self.stats["deaths"] += 1
                self._deaths = [t for t in self._deaths if now - t < d.get("streak_window", 600)] + [now]
                if len(self._deaths) >= d.get("streak_limit", 3):
                    if nakhwa_boss_death:
                        log.warning("요화선녀 사망 누적 → 이번 이벤트 재입장 중단 상태 유지")
                    elif nakhwa_death:
                        log.warning("낙화의 전장 연속 사망 %d회 → 중단하지 않고 거점 복귀 계속", len(self._deaths))
                    else:
                        self._alert(f"{d.get('streak_window', 600) // 60}분 안에 {len(self._deaths)}번 사망 "
                                    "(레벨/장비에 비해 위험한 지역일 수 있음)")
                    self._deaths = []
                return
        # 사망 직후에는 화면이 서서히 나타나 '거점 이동 후 부활' 버튼이 몇 초 늦게 뜬다 → 포기하지 말고 기다린다.
        # (버튼을 못 찾았다고 일시정지하면 봇이 멈춰 버린다)
        if not self._death_since:
            self._death_since = now
        waited = now - self._death_since
        self._death_misses += 1
        if self._death_misses <= 3 or self._death_misses % 10 == 0:
            log.info("사망 화면: 복귀 버튼이 아직 안 보임 (%d초째 기다리는 중)", waited)
        if waited > float(d.get("button_wait", 45)) and not self._death_alerted:
            self._death_alerted = True
            log.warning("사망 화면인데 %d초가 지나도 무료 복귀 버튼을 못 찾음 (유료 '즉시 부활'은 누르지 않음)", waited)
            self._alert("사망 화면의 복귀 버튼을 찾지 못함 (유료 '즉시 부활'은 누르지 않음)", snap.frame)

    def _try_scene_skip_confirm(self, frame, now):
        """'장면 넘기기' 확인창은 오늘 하루 안 보기를 체크한 뒤 확인한다."""
        if now - getattr(self, "_last_scene_skip_confirm", 0.0) < 3.0:
            return False
        try:
            from tools.ocr import read_text
            lines = read_text(frame, scale=2)
        except Exception as exc:  # noqa: BLE001
            log.debug("장면 넘기기 확인창 OCR 실패: %s", exc)
            return False

        compact_all = "".join(re.sub(r"[^0-9A-Za-z가-힣]", "", str(line.get("text", "")))
                              for line in lines)
        if "장면넘기기" not in compact_all or "오늘하루안보기" not in compact_all:
            return False

        today = None
        confirm = None
        for line in lines:
            compact = re.sub(r"[^0-9A-Za-z가-힣]", "", str(line.get("text", "")))
            if "오늘" in compact and "하루" in compact and "안보기" in compact:
                today = line
            if compact == "확인":
                confirm = line
        if today is None or confirm is None:
            log.info("장면 넘기기 확인창 문구는 찾았지만 체크박스/확인 좌표를 못 찾음")
            return False

        checkbox_x = max(0, int(float(today["x"]) - 32))
        checkbox_y = int(float(today["y"]) + float(today["h"]) / 2)
        confirm_x = int(float(confirm["x"]) + float(confirm["w"]) / 2)
        confirm_y = int(float(confirm["y"]) + float(confirm["h"]) / 2)
        log.info("장면 넘기기: '오늘 하루 안 보기' 체크 시도")
        checked = False
        for attempt in range(2):
            self._click_frame("오늘 하루 안 보기", checkbox_x, checkbox_y)
            time.sleep(0.5)
            checked = self._scene_checkbox_checked(self.window.grab(), checkbox_x, checkbox_y)
            if checked:
                break
            log.info("장면 넘기기: 체크 표시 미확인 (%d/2)", attempt + 1)
        if not checked:
            log.warning("장면 넘기기: '오늘 하루 안 보기'가 체크되지 않아 확인 버튼을 누르지 않음")
            self._last_scene_skip_confirm = now
            return True
        log.info("장면 넘기기: '오늘 하루 안 보기' 체크 확인 → 확인")
        self._click_frame("장면 넘기기 확인", confirm_x, confirm_y)
        self._last_scene_skip_confirm = now
        return True

    @staticmethod
    def _scene_checkbox_checked(frame, x, y):
        """체크박스 내부에 밝은 체크 표시가 실제로 생겼는지 판별한다."""
        import numpy as np

        h, w = frame.shape[:2]
        area = frame[max(0, y - 10):min(h, y + 11), max(0, x - 10):min(w, x + 11)]
        if not area.size:
            return False
        lo = area.min(axis=2)
        hi = area.max(axis=2)
        white = (lo > 155) & ((hi.astype(np.int16) - lo.astype(np.int16)) < 55)
        gold = (area[..., 2] > 170) & (area[..., 1] > 130) & (area[..., 0] < 150)
        return int(np.count_nonzero(white | gold)) >= 12

    def _read_quest_name(self, frame):
        """오른쪽 퀘스트 목록 맨 위 퀘스트 제목 (예: '[5장] 길목을 막는 요귀'). 못 읽으면 None."""
        try:
            from .farm import _crop, _ocr
            txt = _ocr(_crop(frame, (1540, 105, 1830, 240)), 2)
        except Exception:  # noqa: BLE001
            return None
        m = re.search(r"\[[^\]]{1,8}\][^\[\n]{1,30}", txt)
        return m.group(0).strip() if m else None

    def _retry_after_revive(self, snap, now):
        """거점 부활 뒤: 같은 출처의 퀘스트를 다시 누른다 (본편=트래커 클릭, 그 외=임무 화면에서 그 탭)."""
        r = self._retry_quest
        if now < r["at"] or snap.state not in ("IDLE", "MOVING", "COMBAT"):
            return
        self._retry_quest = None
        src = r["source"]
        log.info("퀘스트 재도전: %s '%s'", src, self._quest_name or "?")
        self.notifier.send(f"부활 후 퀘스트 재도전: {self._quest_name or src}", key="retry")
        self._fails = 0
        self._verify = None
        if src == "본편" or not self.mission:
            self._click_quest(snap, now)
        else:
            ok = self.mission.run(now, prefer=src)
            if ok and self.mission.last_tab:
                self._source = self.mission.last_tab
            self._click_at = time.monotonic()

    def _recover_nakhwa_after_revive(self, snap, now):
        """낙화 거점 부활 뒤 현재 안전 위치에서 점수표와 AUTO를 복구한다."""
        pending = self._nakhwa_recenter_pending
        if not pending:
            return False
        if now < pending["at"] or not snap.in_field:
            return True
        if not self.mission:
            log.warning("낙화 부활 복구: 임무 자동화가 꺼져 점수표·AUTO 복구를 수행할 수 없음")
            pending["at"] = now + 10.0
            return True
        pending["tries"] += 1
        log.warning("낙화 거점 부활 확인 → 현재 위치 점수표 및 AUTO 복구 (%d회차)", pending["tries"])
        if self.mission.recover_nakhwa_center("거점 부활"):
            self._nakhwa_recenter_pending = None
            self._want_hunt = False
            self._hunt_tries = 0
            self._hunt_press_at = 0.0
            log.warning("낙화 거점 부활 복구 완료 → 현재 위치 AUTO 사냥 재개")
        else:
            pending["at"] = time.monotonic() + 8.0
            log.warning("낙화 점수표·AUTO 복구 확인 실패 → 8초 뒤 재시도")
        return True

    def request_evade(self):
        """제어판 버튼/원격 명령에서 호출 (다른 스레드): 지금 자리를 회피했다가 회복 후 돌아온다."""
        Bot.request_manual_focus(self)
        self._evade_request = True

    def _escape_via_guide(self, now):
        farm = getattr(self, "farm", None)
        if not farm or not self.hp_cfg.get("evade_guide_first", True):
            return False
        if getattr(self, "normal_mode", False) and not getattr(self, "_normal_origin", None):
            log.info("일반 모드: 원래 지역 복귀 지점이 아직 없어 추천 사냥터 피신 대신 근거리 회피")
            return False
        power = getattr(self, "power", None) or farm.power
        if not power:
            return False
        return farm.escape(power, now)

    def _risk_tick(self, snap, now):
        if getattr(self, "normal_mode", False):
            return False                         # 현재 위치를 사용자가 선택한 모드: HP 회피만 적용
        guard = self.risk_guard
        if not guard.due(now) or snap.state not in ("COMBAT", "IDLE") or snap.walking or self._boss_near(snap, now):
            return False
        from .livestatus import read_target
        power = self.power or read_power(snap.frame)
        target = read_target(snap.frame)
        if not guard.observe(power, target, now):
            return False
        log.warning("시작/재개 위험 확인: 내 전투력 %d, 몬스터 %d → 무료 순간이동 회피", power, target['power'])
        self.power = power
        if self._escape_via_guide(now):
            self._rest, self._rest_at, self._rest_tries, self._rest_press_at = "rest", time.monotonic(), 0, 0.0
            self._evade = {"teleported": True}
            self._last_progress = time.monotonic()
        else:
            self._alert("현재 몬스터가 허용 전투력보다 높고 무료 순간이동에 실패했습니다. 안전한 장소에서 재개하세요.", snap.frame)
        return True

    def request_normal_mode(self, on, force=False):
        """현재 위치에서만 사냥하는 일반 모드 전환 요청(실제 변경은 봇 루프)."""
        Bot.request_manual_focus(self)
        self._normal_mode_request = bool(on)
        self._normal_mode_force = bool(force)

    def request_daily_mode(self, on, force=False):
        """일일 임무만 수행하는 모드 전환 요청."""
        Bot.request_manual_focus(self)
        self._daily_mode_request = bool(on)
        self._daily_mode_force = bool(force)

    def request_story_mode(self, on, force=False):
        """현재 전투력에 맞는 서사 임무만 수행하는 모드 전환 요청."""
        Bot.request_manual_focus(self)
        self._story_mode_request = bool(on)
        self._story_mode_force = bool(force)

    def request_daily_quest_scan(self):
        """헬퍼에서 실제 일일 임무 목록을 읽도록 요청한다."""
        Bot.request_manual_focus(self)
        self._daily_picker_request = {"action": "scan"}
        self._set_daily_picker_state("loading", message="게임에서 일일 목록을 읽는 중입니다")

    def request_selected_daily(self, titles):
        """헬퍼에서 체크한 일일만 수락하고 진행하도록 요청한다."""
        Bot.request_manual_focus(self)
        self._daily_picker_request = {"action": "start", "titles": list(titles or [])}
        self._set_daily_picker_state("starting", items=list(titles or []),
                                     message="선택한 일일을 준비 중입니다")

    def _set_daily_picker_state(self, status, items=None, message=""):
        previous = getattr(self, "daily_picker_state", {})
        self.daily_picker_state = {
            "revision": int(previous.get("revision", 0)) + 1,
            "status": status,
            "items": list(previous.get("items", []) if items is None else items),
            "message": message,
        }

    def _process_daily_picker(self, now):
        request, self._daily_picker_request = self._daily_picker_request, None
        if not request:
            return False
        if not self.mission:
            self._set_daily_picker_state("error", message="설정에서 임무 화면 자동 진행을 켜 주세요")
            return True
        if request["action"] == "scan":
            titles = self.mission.scan_daily_quests()
            status = "ready" if titles else "empty"
            message = (f"자동 수행 가능한 일일 {len(titles)}개를 찾았습니다"
                       if titles else "자동 수행 가능한 일일을 찾지 못했습니다")
            self._set_daily_picker_state(status, titles, message)
            return True

        titles = request.get("titles") or []
        if not titles:
            self._set_daily_picker_state("error", message="진행할 일일을 하나 이상 체크해 주세요")
            return True
        self.mission.set_selected_daily_titles(titles)
        intake = self.mission.accept_selected_daily_batch()
        self._daily_mode_request = True
        self._daily_mode_force = True
        self._apply_daily_mode(now)
        self.mission.next_ok = 0.0
        accepted = int(intake.get("accepted", 0))
        already = int(intake.get("already", 0))
        suffix = " · 5/5 제한 도달" if intake.get("quota_full") else ""
        self._set_daily_picker_state(
            "running", titles,
            f"선택한 일일 일괄 확인 완료: 신규 {accepted}개, 기존 {already}개{suffix} · 자동 진행 중")
        log.warning("헬퍼 선택 일일 시작: %s", " → ".join(titles))
        return True

    def _apply_daily_mode(self, now):
        req, self._daily_mode_request = self._daily_mode_request, None
        force = getattr(self, "_daily_mode_force", False)
        self._daily_mode_force = False
        if req is None or (req == self.daily_mode and not force):
            return
        self.daily_mode = req
        self._retry_quest = None
        self._need_switch = False
        self._fails = 0
        self._click_at = 0.0
        self._idle_since = None
        self._verify = None
        if req:
            self.farm_mode = False
            self.manual_farm = False
            self.normal_mode = False
            self.story_mode = False
            self._manual_farm_request = None
            self._normal_mode_request = None
            self._story_mode_request = None
            self._normal_origin = None
            self._farm_until = 0.0
            self._mission_priority = "일일"
            self._mission_priority_pending = bool(self.mission)
            if self.mission:
                self.mission.next_ok = 0.0
            if getattr(self, "tracker", None):
                # 직전에 외전 등이 선택돼 있었더라도 일일 모드 진입 즉시 전체 목록을 다시 읽는다.
                self.tracker.scanned_at = 0.0
                self.tracker.entries = []
            self._want_hunt = False
            self._daily_return_hunt_pending = False
            self._daily_missing_count = 0
            self._daily_missing_at = 0.0
            self._daily_absence_confirmed = False
            log.warning("일일 전용 모드 켬 → 본편·서사·주간 제외, [일일] 전체 소멸 확인 후 앵벌 모드")
            self._focus_game_window()
            self.activity.ignore_until_now()
            self.waiting_user = False
            self.notifier.send("일일 전용 모드 켬", key="daily_mode")
        else:
            self.daily_mode = False if force else self._cfg_daily_mode
            self._daily_return_hunt_pending = False
            self._mission_priority = self.mission.priority[0] if self.mission else "본편"
            self._mission_priority_pending = bool(self.mission and self._mission_priority != "본편")
            log.warning("일일 전용 모드 끔 → 일반 퀘스트 진행")
            self.notifier.send("일일 전용 모드 끔", key="daily_mode")

    def _apply_story_mode(self, now):
        req, self._story_mode_request = self._story_mode_request, None
        force = getattr(self, "_story_mode_force", False)
        self._story_mode_force = False
        if req is None or (req == self.story_mode and not force):
            return
        self.story_mode = req
        self._retry_quest = None
        self._need_switch = False
        self._fails = 0
        self._click_at = 0.0
        self._idle_since = None
        self._verify = None
        if req:
            self.farm_mode = False
            self.manual_farm = False
            self.normal_mode = False
            self.daily_mode = False
            self._manual_farm_request = None
            self._normal_mode_request = None
            self._daily_mode_request = None
            self._normal_origin = None
            self._farm_until = 0.0
            self._mission_priority = "서사"
            self._mission_priority_pending = bool(self.mission)
            if self.mission:
                self.mission.next_ok = 0.0
                self.mission.tab_blocked_until.pop("서사", None)
            self._want_hunt = False
            log.warning("서사 전용 모드 켬 → 현재 전투력에 맞는 '적합' 서사 임무만 자동 진행")
            self._focus_game_window()
            self.activity.ignore_until_now()
            self.waiting_user = False
            self.notifier.send("서사 전용 모드 켬", key="story_mode")
        else:
            self.story_mode = False if force else self._cfg_story_mode
            if not getattr(self, "daily_mode", False):
                self._mission_priority = self.mission.priority[0] if self.mission else "본편"
                self._mission_priority_pending = bool(self.mission and self._mission_priority != "본편")
            log.warning("서사 전용 모드 끔 → 일반 퀘스트 진행")
            self.notifier.send("서사 전용 모드 끔", key="story_mode")

    def _apply_normal_mode(self, now):
        req, self._normal_mode_request = self._normal_mode_request, None
        force = getattr(self, "_normal_mode_force", False)
        self._normal_mode_force = False
        if req is None or (req == self.normal_mode and not force):
            return
        self.normal_mode = req
        self._retry_quest = None
        self._need_switch = False
        if req:
            if self.farm is None:
                self.farm = FarmTask(self, self.cfg.get("farm") or {})
            self.manual_farm = False
            self.farm_mode = False
            self.daily_mode = False
            self.story_mode = False
            self._daily_mode_request = None
            self._story_mode_request = None
            self._manual_farm_request = None
            self._farm_until = 0.0
            self._mission_priority_pending = False
            self._collect_pending = False
            if self.collect:
                self.collect.stop()
            self._normal_origin = None
            self._normal_returning = False
            self._evade_prep_at = -1e9
            self._evade_prep_key = None
            self._want_hunt = True
            self._hunt_tries = 0
            self._hunt_press_at = 0.0
            if self.mission and self.mission._on_screen():
                self.mission._close()
            log.warning("일반 모드 켬 → 현재 위치에서 앵벌, 저체력 시 추천 사냥터 피신 후 원래 지역 복귀")
            self._focus_game_window()
            self.activity.ignore_until_now()
            self.waiting_user = False
            self.notifier.send("일반 모드 켬 (현재 위치 앵벌)", key="normal_mode")
        else:
            self.normal_mode = False if force else self._cfg_normal_mode
            self._normal_origin = None
            self._normal_returning = False
            self._fails = 0
            self._click_at = 0.0
            self._idle_since = None
            self._verify = None
            log.warning("일반 모드 끔 → 퀘스트 다시 진행")
            self.notifier.send("일반 모드 끔 → 퀘스트 재개", key="normal_mode")

    def request_manual_farm(self, on, force=False):
        """제어판 버튼에서 호출 (다른 스레드). 실제 전환은 봇 루프에서 한다."""
        Bot.request_manual_focus(self)
        self._manual_farm_request = bool(on)
        self._manual_farm_force = bool(force)

    def _apply_manual_farm(self, now):
        """수동 앵벌 모드 전환: 켜면 퀘스트는 멈추고 사냥터(수집 몬스터 → 일반 사냥터)에서만 자동 사냥,
        끄면 설정값으로 되돌리고 퀘스트를 다시 진행한다."""
        req, self._manual_farm_request = self._manual_farm_request, None
        force = getattr(self, "_manual_farm_force", False)
        self._manual_farm_force = False
        if req is None or (req == self.manual_farm and not force):
            return
        self.manual_farm = req
        self._retry_quest = None
        self._need_switch = False
        if req:
            if self.farm is None:
                self.farm = FarmTask(self, self.cfg.get("farm") or {})
            if self.collect is None and (self.cfg.get("collect") or {}).get("enabled", True):
                self.collect = CollectTask(self, self.cfg.get("collect") or {})
            self.farm_mode = True
            self.normal_mode = False
            self.daily_mode = False
            self.story_mode = False
            self._daily_mode_request = None
            self._normal_mode_request = None
            self._story_mode_request = None
            self._normal_origin = None
            self.farm.need_travel = True
            self.farm.next_try = 0.0
            self.farm.next_eval = 0.0
            if os.environ.pop("DOKKAEBI_STAY", ""):
                # --stay: 지금 서 있는 곳에서 시작한다 (사냥터를 새로 고르지 않음). 시험·관찰용 — 사망/HP 낮음 처리를 보려고
                # 직접 센 곳으로 간 뒤 켜는 경우. 재선택 주기가 오면 평소 규칙(허용 비율 초과면 내려감)을 따른다.
                self.farm.current = {"id": "stay", "name": "현재 위치(유지)", "power": int(os.environ.get("DOKKAEBI_STAY_POWER", "200000"))}
                self.farm.need_travel = False
                self.farm.next_eval = now + self.farm.reselect
                log.warning("--stay: 지금 자리에서 앵벌 시작 (사냥터 선택 건너뜀, %d분 뒤 재평가)", self.farm.reselect // 60)
            self._collect_pending = bool(self.collect)
            self._want_hunt = True
            self._hunt_tries = 0
            self._hunt_press_at = 0.0
            log.warning("앵벌 모드 켬 (수동 버튼) → 퀘스트는 멈추고 사냥터에서 자동 사냥 (끄면 퀘스트 재개)")
            self._focus_game_window()
            self.activity.ignore_until_now()        # 버튼을 누르느라 쓴 마우스 조작은 사용자 조작으로 치지 않음
            self.waiting_user = False
            self.notifier.send("앵벌 모드 켬 (수동)", key="manual_farm")
        else:
            self.farm_mode = False if force else self._cfg_farm_mode
            self._farm_until = 0.0
            self._collect_pending = False
            self._walk_started = 0.0
            if self.collect:
                self.collect.stop()
            self._fails = 0
            self._click_at = 0.0
            self._idle_since = None
            self._verify = None
            log.warning("앵벌 모드 끔 (수동 버튼) → 퀘스트 다시 진행")
            self.notifier.send("앵벌 모드 끔 → 퀘스트 재개", key="manual_farm")

    def _startup_check(self):
        """시작하면 먼저 화면을 한 번 찍어 상태(지도·HP·전투력·물약·퀘스트 수·자동 사냥)부터 파악한다.
        읽기만 하고 아무것도 누르지 않는다. 결과는 self.startup_state에 두어 제어판이 보여 준다."""
        self.startup_state = None
        if self.dry_run or not (self.cfg.get("startup_check", True) and self.window.locate()):
            return
        try:
            from . import startup
            from .farm import seed_power_reference
            from .rewards import _load_state, _save_state
            saved_state = _load_state()
            cached_power = saved_state.get("last_power")
            seed_power_reference(cached_power)
            st = startup.check(self)
            if not st.get("power") and cached_power:
                st["power"] = int(cached_power)
                log.info("시작 전투력 OCR 오독 → 직전 확정값 복원: %s", f"{int(cached_power):,}")
            if st.get("map"):
                self._observe_map(st["map"], time.monotonic())
            if st.get("power"):
                self.power = st["power"]
                if saved_state.get("last_power") != self.power:
                    saved_state["last_power"] = self.power
                    _save_state(saved_state)
            self.startup_state = st
            log.info("시작 점검 (%.1f초): %s", st["took"], startup.describe(st))
            for w in startup.warnings(st, int((self.cfg.get("potion_shop") or {}).get("buy_below", 50))):
                log.warning("시작 점검 주의: %s", w)
            if st.get("potions") is not None:
                self.potion_shop and setattr(self.potion_shop, "last_count", st["potions"])
        except Exception as e:  # noqa: BLE001
            log.warning("시작 점검 실패 (계속 진행): %s", e)

    def _observe_map(self, name, observed_at=None):
        """지도 OCR 결과로 비경 잠금을 갱신한다.

        비경 진입은 즉시 적용한다. 비경 종료는 OCR 한 번의 누락 때문에 앵벌 이동을 시작하지
        않도록 같은 일반 지도명을 서로 다른 두 화면에서 확인한 뒤 확정한다.
        """
        from .startup import clean_map_name, is_hunt_only_content_name
        name = clean_map_name(str(name or ""))
        if not name:
            return self._secret_realm
        observed_at = time.monotonic() if observed_at is None else float(observed_at)
        if observed_at <= self._map_observed_at:
            return self._secret_realm
        self._map_observed_at = observed_at
        self._map_name = name
        if is_hunt_only_content_name(name):
            self._secret_realm_base = re.sub(r"[^0-9A-Za-z가-힣]", "", name).replace("비경", "")
            self._normal_map_candidate = ""
            self._normal_map_confirm = 0
            if not self._secret_realm:
                self._secret_realm = True
                if "낙화" in name:
                    self._nakhwa_daily_positioned = False
                self._secret_preset_current = None
                self._rest = None
                self._evade = None
                self._evade_pending = False
                self._evade_request = False
                self._want_hunt = True
                self._hunt_tries = 0
                self._hunt_press_at = 0.0
                if self.farm:
                    self.farm.need_travel = True       # 전투 콘텐츠 종료 뒤 원래 앵벌 선택을 시작
                label = "낙화의 전장" if "낙화" in name else "비경"
                log.warning("%s 감지: '%s' → 지도 이동·부가 자동화 중지, 일반 자동사냥만 유지", label, name)
            if self._is_daily_mangja_realm(name) and not self._daily_secret_started_wall:
                self._daily_secret_started_wall = time.time()
                self._save_daily_secret_started(self._daily_secret_started_wall)
                log.warning("일일 망자의 마을 체류 시작 → %d분 뒤 나가기",
                            int(self.secret_realm_combat_cfg.get("daily_duration_minutes", 60)))
            return True
        if not self._secret_realm:
            return False
        if ("낙화" in self._secret_realm_base
                and ("낙화" in name or "전장" in name or "결장" in name)):
            self._normal_map_candidate = ""
            self._normal_map_confirm = 0
            log.debug("낙화 지도명 OCR 흔들림으로 판단: '%s'", name)
            return True
        normal_key = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
        if (self._secret_realm_base and len(normal_key) >= 4
                and (normal_key == self._secret_realm_base
                     or normal_key in self._secret_realm_base
                     or self._secret_realm_base in normal_key)):
            self._normal_map_candidate = ""
            self._normal_map_confirm = 0
            log.debug("전투 콘텐츠 지도명과 같은 OCR로 판단: '%s'", name)
            return True
        if _same_map_read(name, self._normal_map_candidate):
            self._normal_map_confirm += 1
        else:
            self._normal_map_candidate = name
            self._normal_map_confirm = 1
        if self._normal_map_confirm < 2:
            log.info("전투 콘텐츠 종료 확인 중: 일반 지도 '%s' (%d/2)", name, self._normal_map_confirm)
            return True
        self._secret_realm = False
        self._secret_realm_base = ""
        self._normal_map_candidate = ""
        self._normal_map_confirm = 0
        if self._daily_secret_started_wall:
            self._daily_secret_started_wall = 0.0
            self._daily_secret_exit_pending = 0.0
            self._save_daily_secret_started(0.0)
            if self.mission:
                self.mission.next_ok = 0.0
            log.info("일일 망자의 마을 퇴장 완료 → 다음 일일 퀘스트 확인")
        self._want_hunt = bool(getattr(self, "daily_mode", False))
        self._hunt_tries = 0
        self._hunt_press_at = 0.0
        if getattr(self, "daily_mode", False):
            self._daily_return_hunt_pending = True
        if self.farm and (self.farm_mode or time.monotonic() < self._farm_until):
            # 인스턴스 입장 전 사냥터를 그대로 재사용하지 않고, 종료 시점의
            # 전투력을 기준으로 길라잡이 추천 사냥터를 다시 고른다.
            self.farm.current = None
            self.farm.need_travel = True
            self.farm.next_try = 0.0
            self.farm.next_eval = 0.0
            log.info("앵벌 모드: 비경 완전 종료 → 추천 사냥터 재선택 예약")
        self.risk_guard.arm(observed_at)
        if getattr(self, "daily_mode", False):
            log.warning("전투 콘텐츠 종료 확인: '%s' → 일반 맵 자동 사냥 우선 복구", name)
        else:
            log.warning("전투 콘텐츠 종료 확인: '%s' → 일반 맵 앵벌 선택·이동 재개", name)
        return False

    def _refresh_map_mode(self, snap, now):
        """실시간 지도명과 현재 프레임 보조 OCR로 전투 콘텐츠 여부를 안전하게 갱신한다."""
        if not getattr(snap, "in_field", False):
            return self._secret_realm
        live = getattr(self, "live", None)
        data = getattr(live, "data", {}) if live else {}
        live_at = data.get("at")
        if data.get("field") and data.get("map") and live_at is not None and now - live_at <= 12:
            self._observe_map(data["map"], live_at)
        # LiveStatus가 늦거나 OCR 스레드가 멈춰도 비경 종료를 놓치지 않는 보조 확인.
        if now - self._map_ocr_at >= 12:
            self._map_ocr_at = now
            try:
                from .startup import read_map_name
                self._observe_map(read_map_name(snap.frame), now)
            except Exception as e:  # noqa: BLE001
                log.debug("지도 이름 보조 확인 실패: %s", e)
        return self._secret_realm

    def _on_secret_realm(self, snap, now):
        """비경은 자동 사냥만, 낙화는 자동 사냥과 주기적 일일 진행 확인만 수행한다."""
        self._last_progress = now                    # 의도적인 대기라 멈춤 복구도 실행하지 않음
        self._idle_since = None
        self._verify = None
        self._evade_request = False
        if self._daily_secret_exit_tick(now):
            return
        self._secret_realm_preset_tick(snap, now)
        if ("낙화" in self._map_name and self._source != "낙화 요화선녀"
                and getattr(self, "daily_mode", False)
                and not getattr(self, "_nakhwa_daily_positioned", False)
                and getattr(self, "mission", None) and not snap.walking):
            if self.mission.recover_nakhwa_center("일일 시작·재실행 위치 확인"):
                self._nakhwa_daily_positioned = True
                self._nakhwa_check_at = now + 20.0
                log.info("낙화 일일 사냥 지점 이동·점수표·AUTO 복구 완료")
            else:
                self._nakhwa_check_at = now + 10.0
                log.warning("낙화 일일 사냥 지점 복구 실패 → 다음 화면에서 재시도")
            return
        if ("낙화" in self._map_name and self.nakhwa_boss.retired_during_event()):
            self._want_hunt = False
            self._hunt_tries = 0
            if snap.auto_on and not snap.walking:
                self._press("요화선녀 1회 사망 후 AUTO 중단", "t")
            return
        if ("낙화" in self._map_name and self._source != "낙화 요화선녀"
                and getattr(self, "mission", None) and now >= self._nakhwa_check_at
                and not snap.walking):
            self._nakhwa_check_at = now + 90.0
            progress = self.mission.read_nakhwa_progress()
            if progress is not None:
                self._nakhwa_progress = progress
            if progress is not None and progress >= 100:
                if self.mission.leave_nakhwa_battlefield():
                    self._mission_priority_pending = True
                    self.mission.next_ok = 0.0
                return
            # 임무 화면을 여닫으면 켜짐 표시는 남아도 공격이 멈출 수 있어 T를 다시 건다.
            current = self.eyes.look(self.window.grab(), time.monotonic())
            if current.auto_on:
                self._press("낙화 진행도 확인 후 자동 사냥 재시작(끄기)", "t")
                time.sleep(0.8)
            self._press("낙화 진행도 확인 후 자동 사냥 켜기", "t")
            self._want_hunt = False
            self._hunt_tries = 0
            return
        if snap.auto_on:
            self._want_hunt = False
            self._hunt_tries = 0
            return
        if snap.walking:
            return
        if not self._want_hunt and now - self._hunt_check >= 5:
            self._hunt_check = now
            self._want_hunt = True
            self._hunt_tries = 0
            self._hunt_press_at = 0.0
            log.info("전투 콘텐츠: 자동 사냥이 꺼져 있음 → T로 켜기")
        if self._want_hunt:
            # 비경에서는 자동사냥 버튼이 장면 전환/탑승 중 입력을 잠시 무시할 수 있다.
            # 일반 맵처럼 3회 실패로 멈추지 말고 간격을 두고 다음 묶음을 재시도한다.
            if self._hunt_tries >= 3 and now - self._hunt_press_at >= 3.0:
                log.warning("전투 콘텐츠: 자동 사냥 3회 입력 무응답 → 다시 시도")
                self._hunt_tries = 0
            self._ensure_hunt(snap, now)

    def _is_mangja_realm(self, name=None):
        compact = re.sub(r"[^0-9A-Za-z가-힣]", "", str(name or self._map_name))
        return "망자" in compact and "마을" in compact

    def _is_daily_mangja_realm(self, name=None):
        return self._is_mangja_realm(name) and bool(
            getattr(self, "daily_mode", False) or getattr(self, "_source", "") == "일일")

    def _load_daily_secret_started(self):
        try:
            from .rewards import _load_state
            value = float((_load_state().get("daily_secret_realm") or {}).get("started_at", 0.0))
        except (AttributeError, TypeError, ValueError, OSError):
            return 0.0
        # 오래된 회차는 다음 날 비경에 이어 붙이지 않는다.
        return value if 0 < time.time() - value < 6 * 3600 else 0.0

    def _save_daily_secret_started(self, value):
        try:
            from .rewards import _load_state, _save_state
            state = _load_state()
            if value:
                state["daily_secret_realm"] = {"started_at": float(value)}
            else:
                state.pop("daily_secret_realm", None)
            _save_state(state)
        except OSError as exc:
            log.warning("일일 망자의 마을 체류 시간 저장 실패: %s", exc)

    def _daily_secret_exit_tick(self, now):
        if not self._is_daily_mangja_realm() or not self._daily_secret_started_wall:
            return False
        duration = max(60.0, float(self.secret_realm_combat_cfg.get(
            "daily_duration_minutes", 60)) * 60.0)
        if time.time() - self._daily_secret_started_wall < duration:
            return False
        if self._daily_secret_exit_pending and now - self._daily_secret_exit_pending < 20.0:
            return True
        if not self.mission:
            log.warning("일일 망자의 마을 60분 완료지만 임무 자동화가 꺼져 나가기를 누를 수 없음")
            return False
        self._daily_secret_exit_pending = now
        log.warning("일일 망자의 마을 %d분 완료 → 나가기 후 다음 일일 진행", int(duration // 60))
        return bool(self.mission.leave_daily_secret_realm())

    def _secret_realm_preset_tick(self, snap, now):
        """망자의 마을에서 캐릭터 HP에 따라 광역/단일 프리셋을 전환한다."""
        cfg = self.secret_realm_combat_cfg
        switcher = self.secret_realm_preset
        if not switcher or not self._is_mangja_realm() or snap.state not in ("IDLE", "COMBAT"):
            return False
        hp = hp_ratio(snap.frame)
        if hp <= 0:
            return False
        aoe = max(1, min(5, int(cfg.get("aoe_preset", 4))))
        single = max(1, min(5, int(cfg.get("single_preset", 2))))
        low = float(cfg.get("single_below_hp_percent", 65)) / 100.0
        recover = max(low + 0.05, float(cfg.get("aoe_resume_hp_percent", 90)) / 100.0)
        target = None
        if self._secret_preset_current is None:
            target = single if hp <= low else aoe
        elif self._secret_preset_current != single and hp <= low:
            target = single
        elif self._secret_preset_current == single and hp >= recover:
            target = aoe
        if target is None or target == self._secret_preset_current:
            return False
        cooldown = max(5.0, float(cfg.get("switch_cooldown_seconds", 15)))
        if now - self._secret_preset_switched_at < cooldown:
            return False
        self._secret_preset_switched_at = now
        if not switcher.switch(target):
            return False
        self._secret_preset_current = target
        label = "단일" if target == single else "광역"
        log.info("망자의 마을 HP %.0f%% → %s 프리셋 %s 적용",
                 hp * 100, label, chr(64 + target))
        return True

    def _due_common_event(self, wall=None):
        """현재 참가 준비 시간인 공통 이벤트 이름을 우선순위대로 반환한다."""
        wall = wall or datetime.now()
        if self.boss_hunt.due(wall):
            return "보스토벌"
        if self.nakhwa_boss.due(wall):
            return "낙화의 전장"
        return ""

    def _common_event_instance_exit_tick(self, snap, now, wall=None):
        """공통 이벤트 시간이면 인스턴스를 먼저 나가 일반 지도에서 참가시킨다."""
        event_name = self._due_common_event(wall)
        if not event_name:
            self._common_event_exit_pending = False
            return False
        if not self._secret_realm:
            if self._common_event_exit_pending:
                log.info("공통 이벤트 우선: 일반 지도 복귀 확인 → %s 참가 진행", event_name)
                self._common_event_exit_pending = False
            return False
        # 낙화 이벤트 시간에 이미 낙화의 전장 안이라면 목표 인스턴스에 도착한 상태다.
        if event_name == "낙화의 전장" and "낙화" in self._map_name:
            self._common_event_exit_pending = False
            return False

        self._last_progress = now
        self._want_hunt = False
        self._hunt_tries = 0
        if not getattr(snap, "in_field", False):
            return True
        if self._common_event_exit_pending and now - self._common_event_exit_at < 15.0:
            return True
        mission = getattr(self, "mission", None)
        if mission is None:
            log.warning("공통 이벤트 우선 나가기 실패: 임무 자동화가 꺼져 있음")
            self._common_event_exit_at = now
            return True
        self._common_event_exit_pending = True
        self._common_event_exit_at = now
        try:
            mission.leave_instance_for_common_event(event_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("공통 이벤트 우선 인스턴스 나가기 오류: %s", exc)
        return True

    def request_manual_focus(self):
        """수동 명령은 대기 상태와 무관하게 게임 창을 찾아 전면으로 가져오도록 예약한다."""
        if getattr(self, "dry_run", False):
            return
        self._manual_focus_requested = True
        self.waiting_user = False
        self._user_wait_until = 0.0

    def _focus_game_window(self, force=False):
        """제어판 버튼을 누르면 제어판이 게임 위로 올라와 봇이 '게임 창이 앞에 없음'으로 멈춘다 → 게임 창을 앞으로 가져온다."""
        if self.dry_run or (not force and not self.cfg.get("focus_on_start", True)):
            return False
        try:
            from tools.bridge_ops import focus_game
            if self.window.locate() and focus_game(self.window):
                self.activity.ignore_until_now()        # 포커스 이동은 사용자 조작이 아님
                log.info("게임 창을 맨 앞으로 가져옴")
                return True
        except Exception as e:  # noqa: BLE001
            log.debug("게임 창 포커스 실패: %s", e)
        return False

    def _focus_manual_command(self, now):
        """대기 중에도 수동 명령이 있으면 창을 복원·전면화한다. 성공 전에는 명령을 소비하지 않는다."""
        if not self._manual_focus_requested:
            return True
        if now - self._manual_focus_try_at < 0.8:
            return False
        self._manual_focus_try_at = now
        if not self._focus_game_window(force=True):
            log.info("수동 명령 대기: 게임 창을 찾거나 전면으로 가져오는 중")
            return False
        self._manual_focus_requested = False
        self.activity.ignore_until_now()
        log.info("수동 명령: 게임 창 활성화 완료 → 즉시 실행")
        return True

    def _go_collect(self, now):
        """수집 몬스터(화첩/유물)로 걸어간다. 성공하면 일반 사냥터 이동을 앵벌 끝까지 막는다."""
        power = self.power or self.farm.power
        if not power:
            from .farm import read_power
            power = read_power(self.window.grab())
        if not power:
            log.warning("수집: 전투력을 몰라 일반 사냥터로")
            return False
        try:
            ok = self.collect.go(power, now)
        except StopRequested:
            self._collect_pending = True        # 사용자 조작으로 중단 → 다음에 다시 시도
            return True
        finally:
            self._idle_since = None
            self._moving_since = None
        if ok:
            self._walk_started = now                # 도착 신호가 올 때까지 자동 사냥(T)을 막는다
            self._walk_last = now
            self.farm.need_travel = False
            self.farm.current = None
            self.farm.next_eval = max(self._farm_until, now + 60) if not self.farm_mode else now + self.farm.reselect * 4
            self.farm.status = "수집 사냥 중"
            self._want_hunt = False              # 도착(이동 종료) 감지가 T를 켠다
        return ok

    def _start_farming(self, now, why):
        """퀘스트를 중단하고 그동안 전투력에 맞는 사냥터(앵벌 존)로 가서 자동 사냥."""
        secs = getattr(self, "_death_secs", None) or (self.cfg.get("death") or {}).get("block_seconds", 1800)
        if self.mission:
            self.mission.block(self._source, secs)
        self._farm_until = now + secs
        self._need_switch = False
        self._retry_quest = None
        self.farm.need_travel = True
        self.farm.next_try = now + 5
        self._collect_pending = bool(self.collect)
        log.warning("%s → %d분간 전투력에 맞는 사냥터에서 자동 사냥, 이후 퀘스트 다시 시도", why, secs // 60)
        self.notifier.send(f"{why} → {secs // 60:.0f}분 앵벌 후 퀘스트 재시도", key="farm_fallback")

    def _block_current_quest(self, now):
        """사망한 퀘스트(진행 중이던 임무 탭)를 한동안 멈추고, 복귀 후 다른 퀘스트로 전환한다."""
        if not self.mission:
            return
        secs = getattr(self, "_death_secs", None) or (self.cfg.get("death") or {}).get("block_seconds", 1800)
        self.mission.block(self._source, secs)
        self._need_switch = True
        log.info("사망 → '%s' 퀘스트를 %d분간 중단하고 다른 퀘스트로 전환", self._source, secs // 60)

    def _run_mission(self, now):
        if getattr(self, "normal_mode", False):
            log.debug("일반 모드: 임무 창 실행 차단")
            return False
        if getattr(self, "story_mode", False):
            ok = self.mission.run(now, prefer="서사", only=True, safe_only=True)
        else:
            ok = self.mission.run(now)
        self.stats["missions"] += ok
        if ok and self.mission.last_tab:
            self._source = self.mission.last_tab
        self._idle_since = None
        self._verify = None
        self._click_at = time.monotonic()
        return ok

    def _run_priority_mission(self, now, force_daily=False):
        if getattr(self, "normal_mode", False):
            log.debug("일반 모드: 우선 임무 확인 차단")
            return False
        """본편보다 우선하도록 설정한 임무 탭 하나만 확인하고 진행한다."""
        if not self.mission or self._mission_priority == "본편":
            return False
        story_safe = bool(getattr(self, "story_mode", False)
                          and self._mission_priority == "서사")
        ok = self.mission.run(now, prefer=self._mission_priority, only=True,
                              safe_only=story_safe, force_daily=force_daily)
        self.stats["missions"] += ok
        if ok and self.mission.last_tab:
            self._source = self.mission.last_tab
            if self.mission.last_tab == "일일":
                # 자동 진행이 짧게 끝나도 다음 90초까지 방치하지 않고 오른쪽 [일일] 행을 누른다.
                self.mission.next_ok = time.monotonic() + 5.0
        return ok

    def _run_accepted_daily(self, now):
        """일일 모드에서 메인 추적 목록을 끝까지 내려 수락된 일일을 계속 진행한다."""
        tracker = getattr(self, "tracker", None)
        if not tracker:
            return False
        expected = getattr(self.mission, "last_daily_title", "")
        selected = getattr(self.mission, "selected_daily_titles", None)
        strict = selected is not None and bool(expected)
        if selected is not None and not expected:
            return False
        if hasattr(tracker, "click_daily"):
            if strict:
                daily_title = tracker.click_daily(expected, strict=True)
            elif selected is not None:
                daily_title = tracker.click_daily(expected, allowed_titles=selected)
            else:
                daily_title = tracker.click_daily(expected)
            # 방금 진행하던 임무가 끝났다면 임무창을 열기 전에 메인 목록의 다른
            # 체크·수락된 일일을 끝까지 한 번 더 찾아 곧바로 이어서 진행한다.
            if not daily_title and strict and selected:
                daily_title = tracker.click_daily("", allowed_titles=selected)
                if daily_title:
                    self.mission.last_daily_title = daily_title
        else:
            daily_title = tracker.click_text("[일일]")
        if not daily_title:
            if strict:
                # 방금 수행하던 선택 항목이 트래커에서 사라졌으면 완료로 보고,
                # 임무 창에서 선택 목록의 다음 항목을 찾는다.
                self.mission.last_daily_title = ""
                self.mission._daily_intake_checked = False
                self._daily_missing_count = 0
                self._daily_missing_at = 0.0
                self._daily_absence_confirmed = False
                return False
            confirm_seconds = float((self.cfg.get("daily_mode") or {}).get(
                "absence_confirm_seconds", 8.0))
            if expected:
                confirm_seconds = float((self.cfg.get("daily_mode") or {}).get(
                    "active_absence_confirm_seconds", 180.0))
            missing = int(getattr(self, "_daily_missing_count", 0))
            first_at = float(getattr(self, "_daily_missing_at", 0.0))
            if not missing:
                self._daily_missing_count = 1
                self._daily_missing_at = now
                self._daily_absence_confirmed = False
                self.mission.next_ok = now + confirm_seconds
                log.info("일일 전용 모드: 메인 목록 전체에서 [일일] 없음 (1/2) → %.0f초 뒤 재확인",
                         confirm_seconds)
            elif now - first_at >= confirm_seconds * 0.8:
                self._daily_missing_count = 2
                self._daily_absence_confirmed = True
                self.mission.next_ok = now
                log.warning("일일 전용 모드: 메인 목록 전체에서 [일일] 연속 2회 없음 → 임무 일일 탭 교차 확인")
            else:
                self.mission.next_ok = first_at + confirm_seconds
            return False
        self._daily_missing_count = 0
        self._daily_missing_at = 0.0
        self._daily_absence_confirmed = False
        compact = re.sub(r"\s+", "", daily_title)
        if "낙화" in compact and "전장" in compact:
            log.info("일일 전용 모드: 낙화의 전장 일일 확인 → 전용 입장 및 중앙 진입")
            if not self.mission._enter_nakhwa_battlefield():
                self.mission.next_ok = now + 30
                return False
        elif "비경" in compact and "탐험" in compact:
            log.info("일일 전용 모드: 비경 탐험 일일 확인 → 망자의 마을 3단계 전용 입장")
            if not self.mission.enter_accepted_daily_secret_realm():
                self.mission.next_ok = now + 30
                return False
        log.info("일일 전용 모드: 현재 화면에서 [일일] 문구를 재확인하고 해당 행 선택")
        self._source = "일일"
        self._click_at = time.monotonic()
        self._idle_since = None
        self._verify = {"until": self._click_at + self.quest_cfg.get("verify_seconds", 7.0), "ok": False}
        self._clicked_xy = None
        monitor_seconds = float((getattr(self, "cfg", {}).get("daily_mode") or {}).get(
            "tracker_monitor_seconds", 5.0))
        self.mission.next_ok = now + max(2.0, monitor_seconds)
        return True

    def _finish_daily_to_farm(self, reason):
        """메인 트래커에서 모든 일일이 사라지면 앵벌 모드로 안전하게 전환한다."""
        if not getattr(self, "daily_mode", False):
            return
        self._daily_mode_request = False
        self._daily_mode_force = True
        self._normal_mode_request = False
        self._normal_mode_force = True
        self._manual_farm_request = True
        self._manual_farm_force = True
        self._mode_persist_request = {"farm": True, "normal": False, "daily": False}
        if getattr(getattr(self, "mission", None), "selected_daily_titles", None) is not None:
            self._set_daily_picker_state("done", message="선택한 일일 퀘스트 진행을 마쳤습니다")
        log.warning("일일 전용 종료: %s → 앵벌 모드 자동 전환", reason)

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

    def _walk_guard(self, snap, now):
        """수집 이동을 시작한 뒤 '도착'할 때까지 True. 이동 바가 깜빡여도 그 틈에 T를 누르지 않게 하는 안전망.
        풀리는 조건: 이동 바가 walk_end 초 넘게 안 보임(도착/취소) 또는 최대 walk_max 초 경과."""
        t0 = getattr(self, "_walk_started", 0.0)
        if not t0:
            return False
        if snap.walking:
            self._walk_last = now
            if now - t0 > float(self.cfg.get("walk_max", 600)):          # 10분이 넘으면 비정상 → 풀어 준다
                self._walk_started = 0.0
                return False
            return True
        if now - getattr(self, "_walk_last", t0) > float(self.cfg.get("walk_end", 6.0)):
            self._walk_started = 0.0                                      # 바가 한참 안 보임 = 도착(또는 취소)
            log.info("수집 이동 종료 감지 → 자동 사냥 허용")
            return False
        return True

    def _ensure_hunt(self, snap, now):
        """사망 복귀 후 자동 사냥(T)이 꺼져 있으면 켠다. 켜졌는지 색으로 확인하고 최대 3번만 누른다."""
        if snap.auto_on:
            log.info("자동 사냥 켜짐 확인")
            self._want_hunt = False
            return
        if snap.walking or self._walk_guard(snap, now):
            return                      # 웨이포인트 이동 중에 켜면 주변 몹을 공격하느라 이동이 취소된다 → 도착한 뒤에 켠다
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
        if snap.walking or snap.combat:
            return False                # 이동·전투 중에 메뉴를 열면 클릭이 안 먹거나 이동이 끊긴다 → 다음 틱에 다시
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

    def _remember_normal_origin(self):
        """Evader가 읽은 현재 지역 이름을 사냥터 DB의 실제 이동 지점과 연결한다."""
        cache = getattr(self.evader, "cache", None) or {}
        here = cache.get("here") or {}
        name = str(here.get("name") or "").strip()
        if not name:
            self._normal_origin = None
            log.warning("일반 모드: 현재 지역 이름을 읽지 못해 추천 사냥터 피신은 보류")
            return False
        from difflib import SequenceMatcher
        from .farm import load_spots
        db = load_spots()
        key = re.sub(r"\s", "", name)
        candidates = []
        for spot in db.get("spots", []):
            other = re.sub(r"\s", "", str(spot.get("name") or ""))
            score = SequenceMatcher(None, key, other).ratio() if other else 0.0
            if key == other:
                score = 1.0
            if score >= 0.72:
                candidates.append((score, spot))
        if not candidates:
            self._normal_origin = None
            log.warning("일반 모드: 현재 지역 '%s'의 복귀 지점을 사냥터 DB에서 찾지 못함", name)
            return False
        _, spot = max(candidates, key=lambda x: x[0])
        self._normal_origin = {"db": db, "spot": spot, "name": name}
        log.info("일반 모드 복귀 지점 저장: %s → %s", name, spot.get("name") or spot.get("id"))
        return True

    def _ensure_normal_origin(self):
        """일반 모드가 외부 작업을 마친 뒤 사용자 위치로 돌아올 수 있게 저장한다."""
        if not self.normal_mode or self._normal_origin:
            return True
        try:
            if not (getattr(self.evader, "cache", None) or {}).get("here"):
                self.evader.prepare()
            return self._remember_normal_origin()
        except (StopRequested, actions.UserInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("일반 모드 복귀 지점 준비 중 오류: %s", e)
            return False

    def _return_normal_origin(self, now):
        origin = self._normal_origin
        if not origin or not self.farm:
            return False
        try:
            ok = self.farm.nav.travel(origin["db"], origin["spot"], red_check=False)
        except (StopRequested, actions.UserInterrupt):
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("일반 모드 원래 지역 복귀 중 오류: %s", e)
            return False
        if ok:
            self._normal_returning = True
            self._normal_return_at = now
            self._normal_return_quiet = 0
            log.warning("추천 사냥터에서 원래 지역 '%s'로 복귀 이동 시작", origin["name"])
            return True
        log.warning("일반 모드 원래 지역 '%s'로 복귀하지 못함 → 현재 위치에서 자동 사냥 재개", origin["name"])
        return False

    def _maybe_prepare_evade(self, snap, now):
        """사냥터에 자리 잡아 안전할 때(HP 90% 이상, 휴식·회피 중 아님) 자리 회피에 쓸 지역 정보를 미리 읽어 둔다.
        사냥터가 바뀌었거나 마지막으로 읽은 지 25분이 지나면 다시 한다."""
        return  # 실제 지도 도로를 즉시 분석하므로 지역 목록 사전 선택은 사용하지 않는다.
        c = self.hp_cfg
        if (self.dry_run or not c.get("enabled", True) or not c.get("evade_enabled", True) or self._rest is not None
                or self._evade or self._evade_pending or self._normal_returning
                or snap.state not in ("COMBAT", "IDLE") or snap.walking
                or (self.hp or 0.0) < 0.9 or now - self._evade_prep_at < 60):
            return
        key = "normal-current" if self.normal_mode else ((self.farm.current or {}).get("id") if self.farm else None)
        if key == self._evade_prep_key and now - self._evade_prep_at < 1500:
            return
        if not (self.farm_mode or self.normal_mode):             # 앵벌 사냥 중에만 (퀘스트 이동 중에는 하지 않는다)
            return
        self._evade_prep_at, self._evade_prep_key = now, key
        try:
            if self.evader.prepare() and self.normal_mode:
                self._remember_normal_origin()
        except actions.UserInterrupt:
            raise
        except Exception as e:                                # noqa: BLE001
            log.warning("자리 회피 준비 중 오류: %s", e)

    def _run_common_field_tasks(self, snap, now):
        """사냥 위치를 정하는 모드와 무관하게 수행하는 필드 공통 작업."""
        if self.potion_shop and not self.dry_run and not self._verify and self.potion_shop.due(snap, now):
            if self.normal_mode and not self._ensure_normal_origin():
                log.warning("일반 모드: 현재 위치를 저장하지 못해 물약 구매를 이번에는 보류")
                return False
            try:
                self.potion_shop.run(now)
            finally:
                self._idle_since = None
                self._moving_since = None
                if self.normal_mode:
                    self._return_normal_origin(time.monotonic())
                elif self.farm:
                    self.farm.need_travel = True
                    self.farm.next_try = 0.0
            return True
        if (self.enhance and self.enhance.due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run):
            try:
                self.enhance.run(now)
            finally:
                self._idle_since = None
            return True
        if (self.collect and self.collect.register_due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run and self._rest is None):
            try:
                self.collect.register(now)
            finally:
                self._idle_since = None
                self._moving_since = None
                if self.collect.current:
                    self._want_hunt = True
                    self._hunt_tries = 0
                    self._hunt_press_at = 0.0
            return True
        if (self.rewards and self.rewards.due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run):
            try:
                self.rewards.run(now, self.power)
            finally:
                self._idle_since = None
            return True
        return False

    @staticmethod
    def _main_menu_open(frame):
        """우측 ☰ 메뉴 패널이 열려 있는가 (수집 쪽 템플릿 재사용)."""
        try:
            from .collect import _matches, _tpl
            return bool(_matches(frame, _tpl("collect_menu.png"), (1300, 340, 1520, 440), 0.8))
        except Exception:                            # noqa: BLE001
            return False

    def _on_overlay(self, snap, now):
        """필드가 아닌 화면. 컷신/메뉴가 스스로 끝날 시간을 주고, 길어지면 ESC로 닫아본다."""
        if self._try_scene_skip_confirm(snap.frame, now):
            self._overlay_since = None
            return
        if self.farm and (self.farm_mode or self.normal_mode or now < self._farm_until) and now - self._last_map_close > 4.0 and self.farm.nav.map_open(snap.frame):
            # 사냥터 이동 도중 중단돼 세계지도가 열려 있으면 M으로 닫는다 (ESC는 지도를 못 닫음)
            log.info("사냥터 모드: 열려 있는 세계지도를 닫음")
            self._last_map_close = now
            self._press("지도 닫기", "m")
            self._overlay_since = None
            return
        if self.farm and now - self._last_map_close > 4.0 and self.farm.guide.is_open(snap.frame):
            log.info("열려 있는 길라잡이 화면을 닫음")
            self._last_map_close = now
            self._click_frame("길라잡이 닫기", *GUIDE_CLOSE)
            self._overlay_since = None
            return
        if now - self._last_map_close > 4.0 and self._main_menu_open(snap.frame):
            # ☰ 메뉴가 열려 있으면 ESC로는 안 닫힌다 → 같은 자리의 닫기 X(☰)를 누른다
            log.info("열려 있는 메뉴를 닫음")
            self._last_map_close = now
            self._click_frame("메뉴 닫기", 1870, 44)
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
        tracker = getattr(self, "tracker", None)
        if (tracker and hasattr(tracker, "ensure_main_tab")
                and tracker.ensure_main_tab(snap.frame)):
            return
        self._maybe_prepare_evade(snap, now)
        if self.farm and now - self._last_map_close > 4.0 and self.farm.guide.is_open(snap.frame):
            # 길라잡이 창은 필드 표시(오른쪽 아래 HUD)를 가리지 않아 '필드'로 보이지만 키 입력(T 등)을 막는다 → 닫는다
            log.info("열려 있는 길라잡이 화면을 닫음 (필드 화면에서)")
            self._last_map_close = now
            self._click_frame("길라잡이 닫기", *GUIDE_CLOSE)
            return
        if self._rest_tick(snap, now):
            return
        self._watch_move_end(snap, now)
        if now - self._power_at > 300 and snap.state != "MOVING":     # 전투력은 5분마다 읽는다 (OCR 1~2초)
            self._power_at = now
            p = read_power(snap.frame)
            if p:
                if self.power and p != self.power:
                    log.info("전투력 %s → %s", f"{self.power:,}", f"{p:,}")
                self.power = p
                try:
                    from .rewards import _load_state, _save_state
                    state = _load_state()
                    if state.get("last_power") != p:
                        state["last_power"] = p
                        _save_state(state)
                except Exception as e:  # noqa: BLE001
                    log.debug("전투력 기준 저장 실패: %s", e)
        if getattr(self, "daily_mode", False):
            self._last_progress = now
            self._retry_quest = None
            self._need_switch = False
            if getattr(self, "_daily_return_hunt_pending", False):
                if snap.walking or snap.state == "MOVING":
                    return
                if snap.auto_on:
                    self._daily_return_hunt_pending = False
                    self._want_hunt = False
                    self._hunt_tries = 0
                    if self.mission:
                        self.mission.next_ok = max(self.mission.next_ok, now + 30.0)
                    log.info("일일 전용 모드: 일반 맵 복귀 후 자동 사냥 재개 완료")
                    return
                self._want_hunt = True
                self._ensure_hunt(snap, now)
                return
            # 일일 이동이 끝난 직후에는 영기·문파·트래커 OCR보다 AUTO 복구를
            # 먼저 처리한다. 공통 작업이 겹쳐 도착 후 수 초간 서 있는 현상을 막는다.
            if not snap.auto_on and snap.state != "MOVING" and not snap.walking:
                if not self._want_hunt:
                    self._hunt_check = now
                    self._want_hunt = True
                    self._hunt_tries = 0
                    self._hunt_press_at = 0.0
                    log.info("일일 전용 모드: 도착 즉시 자동 사냥 켜기")
                self._ensure_hunt(snap, now)
                return
            if self._want_hunt:
                self._ensure_hunt(snap, now)
                return
            # 5분 주기 문파지원은 다음 일일 임무를 잡기 전에 처리한다.
            if self._run_yeonggi_if_due(snap, now):
                return
            if (self.mission and now >= self.mission.next_ok
                    and snap.state in ("IDLE", "COMBAT") and not snap.walking
                    and not self._secret_realm):
                # 수락된 일일은 임무창보다 오른쪽 트래커를 먼저 끝까지 스크롤해 선택한다.
                if self._run_accepted_daily(now):
                    return
                if getattr(self, "_daily_absence_confirmed", False):
                    # 트래커는 자동 이동·접힘·표시 개수 제한으로 수락된 일일을 숨길 수 있다.
                    # 완료 처리 전에 임무 일일 탭의 우측 수락 아이콘을 반드시 교차 확인한다.
                    self._daily_absence_confirmed = False
                    self._daily_missing_count = 0
                    self._daily_missing_at = 0.0
                    # 한 일일을 끝낸 뒤에도 같은 날 수락해 둔 다른 일일이 남아 있을 수 있다.
                    # 이전 5/5 확인 캐시와 탭 실패 차단을 지우고 실제 일일 목록을 강제 재확인한다.
                    self.mission._daily_intake_checked = False
                    self.mission.tab_blocked_until.pop("일일", None)
                    if self._run_priority_mission(now, force_daily=True):
                        log.info("트래커에 없던 수락 일일을 임무 탭에서 찾아 계속 진행")
                        return
                    self._finish_daily_to_farm("메인 미션 목록에서 [일일] 항목 완전 소멸")
                    return
                if getattr(self, "_daily_missing_count", 0):
                    return                              # 첫 부재는 OCR 오판 방지용 재확인까지 기다린다
                quota_reached = (self.mission.daily_quota_reached()
                                   if hasattr(self.mission, "daily_quota_reached") else False)
                if (quota_reached
                        and getattr(self.mission, "selected_daily_titles", None) is None):
                    self._finish_daily_to_farm("오늘 일일 5/5 완료 및 수행 항목 없음")
                    return
                if not self._run_priority_mission(now):
                    self._finish_daily_to_farm("추가 수락 또는 진행 가능한 일일 없음")
                return
            if self._run_common_field_tasks(snap, now):
                return
            return
        if getattr(self, "story_mode", False):
            self._last_progress = now
            self._retry_quest = None
            self._need_switch = False
            # 5분 주기 문파지원은 다음 서사 임무를 잡기 전에 처리한다.
            if self._run_yeonggi_if_due(snap, now):
                return
            if (self.mission and now >= self.mission.next_ok
                    and snap.state in ("IDLE", "COMBAT") and not snap.walking
                    and not self._secret_realm):
                # 필드에 초록 '서사 임무 수락 가능!'이 있으면 이 항목이 게임이
                # 판정한 적합 임무의 직접 진입점이다. 임무창의 서사 탭보다 우선한다.
                if (self.tracker and self.tracker.click_suitable_story_accept()
                        and self.mission.accept_selected_suitable_story()):
                    self.mission.tab_blocked_until.pop("서사", None)
                    self.mission.next_ok = time.monotonic() + 5.0
                    self.mission.last_tab = "서사"
                    self._source = "서사"
                    self.stats["missions"] += 1
                    return
                self._run_priority_mission(now)
                return
            if self._run_common_field_tasks(snap, now):
                return
            return
        if self.normal_mode:
            self._last_progress = now                    # 현재 자리 사냥은 의도적인 퀘스트 중단
            self._retry_quest = None
            self._need_switch = False
            if (not snap.auto_on and snap.state != "MOVING" and not snap.walking
                    and not self._normal_returning):
                if not self._want_hunt:
                    self._hunt_check = now
                    self._want_hunt = True
                    self._hunt_tries = 0
                    self._hunt_press_at = 0.0
                    log.info("일반 모드: 도착 즉시 자동 사냥 켜기")
                self._ensure_hunt(snap, now)
                return
            if self._want_hunt and not self._normal_returning:
                self._ensure_hunt(snap, now)
                return
            if self._run_yeonggi_if_due(snap, now):
                return
            if self._run_common_field_tasks(snap, now):
                return
            if self._normal_returning:
                if snap.walking or snap.state == "MOVING" or now - self._normal_return_at < 4.0:
                    self._normal_return_quiet = 0
                    return
                self._normal_return_quiet += 1
                if self._normal_return_quiet < 2:
                    return
                self._normal_returning = False
                self._normal_return_quiet = 0
                log.info("일반 모드 원래 지역 복귀 완료 → 자동 사냥 재개")
            return
        if self.mission and now - self._gate_check > 60:
            self._gate_check = now
            for src in list(self.deathbook.gates):
                if not self.mission.is_blocked(src) and self.deathbook.gate_closed(src, self.power):
                    self.mission.block(src, 600)
                    log.info("전투력 게이트: '%s'는 전투력이 오를 때까지 보류 (10분 뒤 재확인)", src)
                    if self.farm_fallback and src == "본편":
                        self._farm_until = max(self._farm_until, now + 600)
        if self._retry_quest:
            self._retry_after_revive(snap, now)
            return
        if (self._mission_priority_pending and self.mission and now >= self.mission.next_ok
                and snap.state in ("IDLE", "COMBAT") and not self._secret_realm):
            self._mission_priority_pending = False
            if self._run_priority_mission(now):
                self._fails = 0
            return
        if self._want_hunt:
            self._ensure_hunt(snap, now)
            return
        if self._run_common_field_tasks(snap, now):
            return
        if self._need_switch and snap.state in ("IDLE", "MOVING"):
            self._need_switch = False
            if self._run_mission(now):
                self._fails = 0
            else:
                log.warning("전환할 다른 퀘스트가 없음 → 차단 시간이 끝날 때까지 자동 사냥만 유지")
                if self.farm_fallback and self.farm:
                    self._start_farming(now, "진행할 퀘스트 없음")
            return
        if self._farm_until and now >= self._farm_until and not self.farm_mode:
            self._farm_until = 0.0
            self._collect_pending = False
            if self.collect:
                self.collect.stop()
            log.info("앵벌 시간 끝 → 퀘스트 다시 진행")
            self._fails = 0
            self._idle_since = None
            self._click_at = 0.0
        if self.farm_mode or (self.farm and now < self._farm_until):
            self._need_switch = False
            if self._run_yeonggi_if_due(snap, now):
                return
            if (self.collect and self.collect.current and not self._collect_pending and snap.state in ("IDLE", "COMBAT")
                    and now - self._collect_check >= 20):
                self._collect_check = now                # 채팅 OCR이 느리니 20초에 한 번만 본다
                if self.collect.should_switch(snap, now):
                    self.collect.next_target()           # 조각이 들어왔거나 오래 걸렸으면 다음 후보로
                    self._collect_pending = True
            if self._collect_pending and snap.state in ("IDLE", "COMBAT", "MOVING") and now >= self.farm.next_try:
                self._collect_pending = False
                if self._go_collect(now):
                    return
            self.farm.tick(snap, now)
            if (not snap.auto_on and snap.state != "MOVING" and not snap.walking and not self._walk_guard(snap, now)
                    and not self._want_hunt
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

        if snap.tracker_changed:
            self._tracker_at = now
        progressing = (snap.state in ("MOVING", "COMBAT") or snap.world_motion > self.motion_threshold)
        nudge = float(self.quest_cfg.get("nudge_minutes", 5)) * 60
        if (progressing and snap.state == "COMBAT" and nudge > 0 and self.quest_cfg.get("enabled", True)
                and not self._verify and now - max(self._click_at, self._tracker_at, self._run_t0) > nudge):
            # 자동 사냥이 계속 몹을 잡으면 정지 상태가 안 생겨 퀘스트를 영영 안 누르게 된다 → 주기적으로 다시 누른다 (퀘스트 목록 글자가 바뀌는 중이면 진행 중으로 보고 누르지 않음)
            log.info("퀘스트 진행 없이 전투만 %.0f분째 → 퀘스트 다시 누름", nudge / 60)
            self._click_quest(snap, now)
            return
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
        # 일일 같은 우선 임무가 남아 있으면 본편 트래커보다 먼저 다음 항목을 찾는다.
        if (self.mission and self._mission_priority != "본편"
                and not self.mission.is_blocked(self._mission_priority)
                and now >= self.mission.next_ok):
            if self._run_priority_mission(now):
                self._fails = 0
                return
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

    def _on_stall(self, snap, now):
        """stall.minutes 동안 아무 진행(이동/전투/대화)이 없을 때의 단계별 복구."""
        self._stalls += 1
        self._last_progress = now
        mins = self.stall_secs / 60
        shot = self._save_screen(snap.frame, "stall")
        log.warning("멈춤 감지: %.0f분 동안 진행 없음 (%d회째, 상태 %s)", mins, self._stalls, snap.state)
        if self._stalls >= 3:
            self._alert(f"{mins:.0f}분씩 {self._stalls}번 연속 진행 없음 (복구 실패)", snap.frame)
            self._stalls = 0
            return
        self.notifier.send(f"{mins:.0f}분 동안 진행이 없어 복구 시도 ({self._stalls}회째)", snap.frame, key="stall")
        if self.farm and self.farm.nav.map_open(snap.frame):
            self._press("멈춤 복구: 지도 닫기", "m")
        elif snap.state == "OVERLAY":
            self._press("멈춤 복구: 화면 닫기", "esc")
        elif getattr(self, "daily_mode", False) or self.normal_mode:
            self._want_hunt = True
            self._hunt_tries = 0
            self._hunt_press_at = 0.0
        elif self.farm_mode:
            self.farm.need_travel = True
            self.farm.next_try = 0.0
        elif self.mission and not self.dry_run:
            log.info("멈춤 복구: 임무 화면에서 다른 퀘스트 찾기")
            self._run_mission(now)
        else:
            self._want_hunt = True
            self._hunt_tries = 0

    # ---------- 원격 명령 / 리포트 ----------
    def status_text(self):
        up = time.time() - self.started_wall
        st = "일시정지" if self.paused else ("사용자 조작 대기" if self.waiting_user else (self._last_state or "-"))
        lines = [f"상태: {st}" + (f" ({self.alert_reason})" if self.alert_reason else ""),
                 f"가동: {int(up // 3600)}시간 {int(up % 3600 // 60)}분",
                 f"전투력: {self.power:,}" if self.power else "전투력: ?",
                 f"퀘스트 출처: {self._source}",
                 f"사망 {self.stats['deaths']} · 임무 전환 {self.stats['missions']} · 영기 {self.stats['yeonggi']}"]
        if self._secret_realm:
            label = "낙화의 전장" if "낙화" in self._map_name else "비경"
            lines.append(f"{label}: {self._map_name or '?'} · 지도 이동 없이 일반 자동사냥만")
            if "낙화" in self._map_name and self._nakhwa_progress is not None:
                lines.append(f"낙화 일일: {self._nakhwa_progress}/100")
        if self.boss_hunt.phase != "idle":
            lines.append(f"보스토벌: {self.boss_hunt.target_name} · {self.boss_hunt.status()}")
        if self.nakhwa_boss.phase != "idle":
            lines.append(f"요화선녀: {self.nakhwa_boss.status()}")
        if self._nakhwa_recenter_pending:
            lines.append("낙화 사망 복구: 거점 부활 후 중앙 재진입 대기")
        if self.normal_mode:
            origin = (self._normal_origin or {}).get("name") or "확인 중"
            lines.append(f"일반 모드: 현재 위치 앵벌 · 복귀 지점 {origin}")
        if getattr(self, "daily_mode", False):
            lines.append("일일 전용 모드: 본편·서사·주간 제외")
        if getattr(self, "story_mode", False):
            lines.append("서사 전용 모드: 현재 전투력 적합 임무만 진행")
        if self.farm and (self.farm_mode or time.monotonic() < self._farm_until):
            lines.append("사냥터: " + self.farm.describe())
        return "\n".join(lines)

    def handle_command(self, cmd):
        """텔레그램 명령. (답장, 화면 또는 None)"""
        if cmd in ("상태", "status"):
            return self.status_text(), None
        if cmd in ("화면", "screen"):
            return self.status_text(), (self.window.grab() if self.window.locate() else None)
        if cmd in ("일시정지", "pause"):
            self.paused = True
            self.alert_reason = "원격 일시정지"
            return "일시정지했습니다.", None
        if cmd in ("재개", "resume"):
            self.paused = False
            self.risk_guard.arm()
            self.alert_reason = ""
            self.activity.ignore_until_now()
            self._idle_since = self._verify = None
            return "재개했습니다.", None
        if cmd in ("회피", "evade"):
            self.request_evade()
            return "자리 회피를 요청했습니다 (지금 자리에서 빠졌다가 HP가 차면 돌아와 자동 사냥).", None
        if cmd in ("정지", "stop"):
            self.stop_event.set()
            return "매크로를 종료합니다.", None
        return "명령: /상태 /화면 /일시정지 /재개 /회피 /정지", None

    def _daily_report(self):
        rc = self.cfg.get("notify") or {}
        hour = rc.get("report_hour")
        if hour is None:
            return
        today = datetime.now()
        if today.hour == int(hour) and getattr(self, "_reported_day", None) != today.date():
            self._reported_day = today.date()
            self.notifier.send("일일 리포트\n" + self.status_text(), key="report")

    def _retry_cua_input(self, exc):
        """일시적인 Cua 전달 실패를 기록하고 다음 루프에서 안전하게 재시도한다."""
        detail = str(exc).replace("\r", " ").replace("\n", " ")
        log.warning("Cua Driver 일시 입력 실패 → 자동 진행 유지·재시도: %s", detail[:500])
        self._idle_since = self._verify = None
        time.sleep(1.0)

    # ---------- 루프 ----------
    def _tick(self):
        frame = self.window.grab()
        now = time.monotonic()
        snap = self.eyes.look(frame, now)
        state = snap.state
        in_secret_realm = self._refresh_map_mode(snap, now)
        self.live.note_frame(state != "OVERLAY", frame)  # 상황판도 같은 프레임을 재사용
        if state != self._last_state:
            log.info("상태: %s → %s", self._last_state or "-", state)
            self._last_state = state
        if state != "OVERLAY":
            self._overlay_since = None
            self._overlay_esc = 0
        if state != "OVERLAY":
            self._seen_known = True
        elif not self._seen_known and not self._ui_warned and now - self._run_t0 > 180:
            self._ui_warned = True
            log.warning("시작 후 3분 동안 아는 화면(필드 HUD 등)을 한 번도 인식하지 못함 → 게임 업데이트로 UI가 바뀌었거나 해상도 문제일 수 있음")
            self._save_screen(snap.frame, "unknown_ui")
            self.notifier.send("아는 화면을 인식하지 못함 (게임 UI 변경 가능성)", snap.frame, key="ui")
        if state != "DEAD" and self._common_event_instance_exit_tick(snap, now):
            return
        # 보스 이벤트가 틱을 선점해도 화면에 표시된 문파지원 아이콘은 먼저 처리한다.
        # 템플릿 검색 결과 좌표를 사용하므로 보스필드 아이콘 추가로 세로 위치가 밀려도 안전하다.
        if snap.in_field and self._clan_help_tick(snap, now):
            return
        if self.boss_hunt.tick(snap, now):
            return
        if self.nakhwa_boss.tick(snap, now):
            return
        if state != "DEAD" and self._recover_nakhwa_after_revive(snap, now):
            return
        if in_secret_realm and state != "DEAD":
            if snap.in_field:
                self._on_secret_realm(snap, now)
            return
        if state in ("MOVING", "COMBAT", "DIALOG", "DEAD") or snap.world_motion > self.motion_threshold:
            self._last_progress = now
            self._stalls = 0
        elif now - self._last_progress > self.stall_secs:
            self._on_stall(snap, now)
            return

        self._watch_hp(snap, now)
        if self._risk_tick(snap, now):
            return
        if self.preset and not self.dry_run:
            # 낙화의 전장은 강한 몬스터를 상대하므로 일반 광역 프리셋으로 되돌리지 않는다.
            if "낙화" in self._map_name:
                nakhwa_preset = int((self.cfg.get("nakhwa_boss") or {}).get("hunt_preset", 2))
                if (self.preset.current != nakhwa_preset
                        and now - self.preset._last_switch >= 20):
                    self.preset._last_switch = now
                    if self.preset.switch(nakhwa_preset):
                        log.info("낙화의 전장: 단일 저격 프리셋 B 유지")
                        return
            elif self.preset.tick(snap, now):
                return
        if state not in ("DEAD", "OVERLAY"):
            self._death_open = False
            self._death_since = 0.0
            self._death_alerted = False
        if state == "DEAD":
            self._on_dead(snap, now)
        elif state == "DIALOG":
            if not self._try_scene_skip_confirm(snap.frame, now):
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
        interval = float(self.cfg.get("loop_interval", 0.5))
        stable_interval = max(interval, float(self.cfg.get("stable_loop_interval", 2.0)))
        _, self.selfcheck = selfcheck.run_all(self.cfg)
        self._run_t0 = time.monotonic()
        self.notifier.start_commands(self.handle_command)
        self.notifier.send("매크로 시작 (" + self.selfcheck + ")", key="start")
        time.sleep(2.0)
        if self.cfg.get("focus_on_start", True) and not self.dry_run and self.window.locate():
            try:            # 제어판 창이 앞에 뜨므로 시작할 때 한 번만 게임 창을 앞으로 가져온다
                from tools.bridge_ops import focus_game
                if focus_game(self.window):
                    log.info("게임 창을 맨 앞으로 가져옴")
            except Exception as e:  # noqa: BLE001
                log.debug("게임 창 포커스 실패: %s", e)
        self.activity.ignore_until_now()   # 실행 직전의 입력(브리지 포커스 등)은 사용자 조작이 아님
        self._startup_check()
        self.risk_guard.arm()
        self.live.start()                      # 대시보드용 실시간 상황판 읽기(별도 스레드)
        waiting_logged = False
        while True:
            if self.stop_event.is_set():
                log.info("종료 요청")
                self.notifier.stop()
                return
            self._daily_report()
            if self.hotkeys.pressed(self.stop_key):
                log.info("종료 키 입력")
                return
            if self.hotkeys.pressed(self.pause_key):
                self.paused = not self.paused
                log.info("일시정지" if self.paused else "재개")
                if not self.paused:
                    self.risk_guard.arm()
                    self.alert_reason = ""
                    self.activity.ignore_until_now()
                    self.waiting_user = False
                    self._idle_since = self._verify = None
            now = time.monotonic()
            try:
                if not self._focus_manual_command(now):   # 수동 버튼은 창 대기보다 우선해 전면 활성화
                    time.sleep(0.1)
                    continue
                self._apply_daily_mode(now)                # 제어판 버튼: 게임 창이 뒤에 있어도 바로 처리
                self._apply_story_mode(now)
                self._apply_normal_mode(now)
                self._apply_manual_farm(now)
                if self._process_daily_picker(now):
                    time.sleep(0.1)
                    continue
            except actions.UserInterrupt:
                log.info("수동 명령 중 사용자 조작 감지 → 다음 루프에서 재시도")
                self._idle_since = self._verify = None
                continue
            except CuaInputError as exc:
                # 일일 목록 불러오기·일괄 수락은 메인 화면 틱보다 먼저 실행된다.
                # 여기서 순간 입력 실패가 나도 GUI 오류 정지로 전파하지 않는다.
                self._retry_cua_input(exc)
                continue
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
            if not self.window.locate() or self.window.is_minimized():
                if not waiting_logged:
                    if not self.window.locate():
                        why = "게임 창을 찾지 못함 (제목 '%s' / 프로세스 %s)" % (self.window.title_part, self.window.process_name or "-")
                    else:
                        why = "게임 창이 최소화되어 있음"
                    log.info("대기 중: %s", why)
                    waiting_logged = True
                if not self.window.locate():
                    self._gone_since = self._gone_since or time.monotonic()
                    if time.monotonic() - self._gone_since > 60 and not getattr(self, "_gone_warned", False):
                        self._gone_warned = True
                        log.warning("게임 창이 1분 넘게 없음 (게임이 꺼졌거나 튕겼을 수 있음)")
                        self.notifier.send("게임 창이 1분 넘게 없음 (튕김/종료?)", key="gone")
                else:
                    self._gone_since = None
                    self._gone_warned = False
                if self.cfg.get("yield_only_game_input", True):
                    self.activity.ignore_until_now(0.3)   # 다른 창에서 한 조작은 게임 조작으로 치지 않는다
                self._idle_since = self._verify = None
                time.sleep(0.5)
                continue
            if waiting_logged:
                log.info("게임 창 감지 (%dx%d)", self.window.width, self.window.height)
                waiting_logged = False
                self.activity.ignore_until_now()

            if self._wait_for_user_control(time.monotonic()):
                time.sleep(0.5)
                continue

            try:
                self._tick()
            except actions.UserInterrupt:
                log.info("동작 중 사용자 조작 감지 → 중단")
                self._idle_since = self._verify = None
            except CuaInputError as exc:
                # 전면 창 전환이나 Cua 캡처 갱신 순간의 일시 실패는 매크로 전체를
                # 종료할 이유가 아니다. 다음 틱에서 새 화면을 읽고 안전하게 재판단한다.
                self._retry_cua_input(exc)
            # 전투/대기 화면이 안정적이고 최근 자동 입력도 없으면 2초 주기로 낮춘다.
            # 사망·대화·이동·메뉴 및 입력 직후에는 짧은 주기를 유지해 반응성을 보존한다.
            stable = (self._last_state in ("IDLE", "COMBAT")
                      and actions.seconds_since_self_input() >= 3.0
                      and self.boss_hunt.phase == "idle"
                      and self.nakhwa_boss.phase == "idle"
                      and not self._common_event_exit_pending
                      and not self._nakhwa_recenter_pending)
            time.sleep(stable_interval if stable else interval)
