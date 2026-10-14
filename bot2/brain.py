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
        self.user_detect = bool(config.get("user_detect", True))      # 제어판 토글: 사용자 조작 감지

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
        self._cfg_farm_mode = self.farm_mode                         # 설정에 적힌 값 (수동 앵벌 모드를 끄면 이 값으로 되돌림)
        self.manual_farm = False                                     # 제어판 버튼으로 켠 앵벌 모드
        self._manual_farm_request = None                             # 버튼이 남긴 요청: True 켜기 / False 끄기 / None 없음
        self.farm_fallback = bool(fm.get("fallback_when_blocked"))   # 퀘스트가 전부 막히면 안전 사냥터로
        self.farm = FarmTask(self, fm) if (self.farm_mode or self.farm_fallback) else None
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
        self.deathbook = DeathBook(config.get("death") or {})
        self.notifier = Notifier(config.get("notify"))
        st = config.get("stall") or {}
        self.stall_secs = float(st.get("minutes", 10)) * 60
        self._last_progress = time.monotonic()
        self._stalls = 0
        self.power = None               # 마지막으로 읽은 전투력
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
        from .livestatus import LiveStatus
        self.live = LiveStatus(self, float(config.get("live_interval", 3.0)))     # 대시보드 실시간 상황판
        self._rest = None               # HP 낮음 휴식: None / "finish"(잡던 몹 마무리) / "off"(자동 사냥 끄는 중) / "rest"(회복 대기) / "on"(다시 켜는 중)
        self._rest_at = 0.0
        from .evade import Evader
        self.evader = Evader(BotDriver(self), int(self.hp_cfg.get('evade_dist_px', 380)))   # HP 아주 낮을 때 자리 회피
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
        log.warning("사용자 조작 감지 %s (버튼)", "켬" if on else "끔 → 마우스·키보드를 써도 봇이 멈추지 않음")

    def _check_user(self):
        if not self.user_detect:
            return
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
        below = c.get("rest_resume", 0.98) if self._rest == "rest" else c.get("potion_below", 0.45)
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
                    and 0.0 < hp <= c.get("evade_below", 0.30) and now - self._evade_last > c.get("evade_cooldown", 60)
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
                    self._evade = self.evader.flee(self.window.grab())
                except actions.UserInterrupt:
                    raise
                except Exception as e:                       # noqa: BLE001
                    log.warning("자리 회피 중 오류: %s", e)
                    self._evade = None
            self._rest, self._rest_at = "rest", now
            log.info("자동 사냥 끔 → HP %.0f%%까지 회복 대기 (물약, 최대 %d초)", min(c.get("rest_resume", 0.98), 1.0) * 100, c.get("rest_max", 600))
            return True
        if self._rest == "rest":
            why = None
            if hp >= c.get("rest_resume", 0.98):
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
                if self._death_open:
                    log.info("같은 사망 화면이 아직 남아 있음 → 다시 클릭만 함 (사망 1회로 셈)")
                    return
                self._death_open = True
                self._want_hunt = True          # 복귀 후 자동 사냥으로 전환
                self._hunt_tries = 0
                self._hunt_press_at = 0.0
                self._death_misses = 0
                shot = self._save_screen(snap.frame, "death")
                farming = self.farm_mode or now < self._farm_until
                self._death_secs, n = self.deathbook.record("사냥터" if farming else self._source, self.power, shot)
                self.notifier.send(f"사망 ({self._source}, 최근 {n}회째) → 무료 복귀", snap.frame, key="death")
                retry_limit = int(d.get("retry_limit", 3))
                if farming:
                    if self.collect and self.collect.current:
                        self.collect.on_death(now)   # 수집 몬스터에서 죽음: 그 몬스터 제외하고 다음(더 약한) 후보로
                        self._collect_pending = True
                    self.farm.on_death(now)          # 사냥터에서 죽음: 한 단계 낮은 사냥터로
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

    def request_evade(self):
        """제어판 버튼/원격 명령에서 호출 (다른 스레드): 지금 자리를 회피했다가 회복 후 돌아온다."""
        self._evade_request = True

    def request_manual_farm(self, on):
        """제어판 버튼에서 호출 (다른 스레드). 실제 전환은 봇 루프에서 한다."""
        self._manual_farm_request = bool(on)

    def _apply_manual_farm(self, now):
        """수동 앵벌 모드 전환: 켜면 퀘스트는 멈추고 사냥터(수집 몬스터 → 일반 사냥터)에서만 자동 사냥,
        끄면 설정값으로 되돌리고 퀘스트를 다시 진행한다."""
        req, self._manual_farm_request = self._manual_farm_request, None
        if req is None or req == self.manual_farm:
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
            self.farm_mode = self._cfg_farm_mode
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
            st = startup.check(self)
            self.startup_state = st
            log.info("시작 점검 (%.1f초): %s", st["took"], startup.describe(st))
            for w in startup.warnings(st, int((self.cfg.get("potion_shop") or {}).get("buy_below", 50))):
                log.warning("시작 점검 주의: %s", w)
            if st.get("potions") is not None:
                self.potion_shop and setattr(self.potion_shop, "last_count", st["potions"])
        except Exception as e:  # noqa: BLE001
            log.warning("시작 점검 실패 (계속 진행): %s", e)

    def _focus_game_window(self):
        """제어판 버튼을 누르면 제어판이 게임 위로 올라와 봇이 '게임 창이 앞에 없음'으로 멈춘다 → 게임 창을 앞으로 가져온다."""
        if self.dry_run or not self.cfg.get("focus_on_start", True):
            return
        try:
            from tools.bridge_ops import focus_game
            if self.window.locate() and focus_game(self.window):
                self.activity.ignore_until_now()        # 포커스 이동은 사용자 조작이 아님
                log.info("게임 창을 맨 앞으로 가져옴")
        except Exception as e:  # noqa: BLE001
            log.debug("게임 창 포커스 실패: %s", e)

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

    def _maybe_prepare_evade(self, snap, now):
        """사냥터에 자리 잡아 안전할 때(HP 90% 이상, 휴식·회피 중 아님) 자리 회피에 쓸 지역 정보를 미리 읽어 둔다.
        사냥터가 바뀌었거나 마지막으로 읽은 지 25분이 지나면 다시 한다."""
        c = self.hp_cfg
        if (self.dry_run or not c.get("enabled", True) or not c.get("evade_enabled", True) or self._rest is not None
                or self._evade or self._evade_pending or snap.state not in ("COMBAT", "IDLE") or snap.walking
                or (self.hp or 0.0) < 0.9 or now - self._evade_prep_at < 60):
            return
        key = (self.farm.current or {}).get("id") if self.farm else None
        if key == self._evade_prep_key and now - self._evade_prep_at < 1500:
            return
        if not self.farm_mode:                                  # 앵벌 사냥 중에만 (퀘스트 이동 중에는 하지 않는다)
            return
        self._evade_prep_at, self._evade_prep_key = now, key
        try:
            self.evader.prepare()
        except actions.UserInterrupt:
            raise
        except Exception as e:                                # noqa: BLE001
            log.warning("자리 회피 준비 중 오류: %s", e)

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
        if self.farm and (self.farm_mode or now < self._farm_until) and now - self._last_map_close > 4.0 and self.farm.nav.map_open(snap.frame):
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
        if self._want_hunt:
            self._ensure_hunt(snap, now)
            return
        if self.potion_shop and not self.dry_run and not self._verify and self.potion_shop.due(snap, now):
            try:
                self.potion_shop.run(now)
            finally:
                self._idle_since = None
                self._moving_since = None
                if self.farm:
                    self.farm.need_travel = True      # 사냥터 모드면 다시 사냥터로
                    self.farm.next_try = 0.0
            return
        if (self.enhance and self.enhance.due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run):
            try:
                self.enhance.run(now)
            finally:
                self._idle_since = None
            return
        if (self.collect and self.collect.register_due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run and self._rest is None):
            try:
                self.collect.register(now)
            finally:
                self._idle_since = None
                self._moving_since = None
                if self.collect.current:            # 수집 사냥 중이었으면 화면을 닫고 그 자리에서 계속 (자동 사냥 다시 켬)
                    self._want_hunt = True
                    self._hunt_tries = 0
                    self._hunt_press_at = 0.0
            return
        if (self.rewards and self.rewards.due(now) and snap.state in ("IDLE", "COMBAT")
                and not self._verify and not self.dry_run):
            try:
                self.rewards.run(now, self.power)
            finally:
                self._idle_since = None
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

    # ---------- 루프 ----------
    def _tick(self):
        frame = self.window.grab()
        now = time.monotonic()
        snap = self.eyes.look(frame, now)
        state = snap.state
        self.live.note_frame(state != "OVERLAY")       # 동기화: 이번 화면을 아는 화면으로 분류했는지
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
        if state in ("MOVING", "COMBAT", "DIALOG", "DEAD") or snap.world_motion > self.motion_threshold:
            self._last_progress = now
            self._stalls = 0
        elif now - self._last_progress > self.stall_secs:
            self._on_stall(snap, now)
            return

        self._watch_hp(snap, now)
        if self.preset and not self.dry_run and self.preset.tick(snap, now):
            return
        if state not in ("DEAD", "OVERLAY"):
            self._death_open = False
            self._death_since = 0.0
            self._death_alerted = False
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
                    self.alert_reason = ""
                    self.activity.ignore_until_now()
                    self.waiting_user = False
                    self._idle_since = self._verify = None
            self._apply_manual_farm(time.monotonic())      # 제어판 버튼: 게임 창이 뒤에 있어도 바로 처리
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
                    if not self.window.locate():
                        why = "게임 창을 찾지 못함 (제목 '%s' / 프로세스 %s)" % (self.window.title_part, self.window.process_name or "-")
                    elif self.window.is_minimized():
                        why = "게임 창이 최소화되어 있음"
                    else:
                        fg = ""
                        try:
                            from bot.window import foreground_title
                            fg = foreground_title()
                        except Exception:  # noqa: BLE001
                            pass
                        why = "게임 창은 찾았지만 맨 앞이 아님" + (f" (지금 앞 창: '{fg}')" if fg else "")
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

            if not self.dry_run and self.user_detect:
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
