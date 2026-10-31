"""매일 21시 필드 보스 준비·전투·앵벌 복귀 상태 흐름.

일반 이동은 무료만 쓴다는 원칙의 유일한 예외로, 이 작업 안에서 화면의 대상 보스와
대상 보스와 버튼을 다시 확인한 뒤 보스토벌 세계지도의 ``축지술``만 누른다.
"""
import logging
import os
import re
from datetime import datetime, time as wall_time

from app_paths import ROOT
from bot.vision import save_image
from .farm import _crop, _ocr

log = logging.getLogger(__name__)


class BossHuntTask:
    EVENT_REGION = [0.0, 0.75, 0.08, 0.20]
    EVENT_CARD = {0: (200, 215), 1: (200, 315)}  # 산군대호 / 해골장군
    EVENT_MAP = (1665, 1020)
    WALK_TRAVEL = (1630, 1020)
    INSTANT_TRAVEL = (1810, 1020)

    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.prepare = self._parse_time(cfg.get("prepare_at", "20:55"), wall_time(20, 55))
        self.starts = self._parse_time(cfg.get("starts_at", "21:00"), wall_time(21, 0))
        self.duration = float(cfg.get("duration_minutes", 5)) * 60
        self.finish_absent = float(cfg.get("finish_absent_seconds", 10))
        self.retry_seconds = float(cfg.get("retry_seconds", 30))
        self.travel_timeout = float(cfg.get("travel_timeout_seconds", 420))
        self.phase = "idle"
        self.phase_at = 0.0
        self.active_date = None
        self.attempted_date = None
        self.next_try = 0.0
        self.boss_seen = False
        self.boss_last_seen = 0.0
        self._input_at = -1e9
        self._old_source = None
        self.target_name = ""
        self._last_map_evidence = None
        self._last_evidence_log = -1e9
        self.free_walk_started = False
        self.paid_travel_started = False

    @staticmethod
    def _parse_time(value, fallback):
        try:
            h, m = (int(x) for x in str(value).split(":", 1))
            return wall_time(h, m)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def target_for(weekday):
        """월·수·금·일은 산군대호, 화·목·토는 해골장군."""
        return ("산신 산군대호", 0) if weekday in (0, 2, 4, 6) else ("해골장군", 1)

    def target_enabled(self, weekday):
        """요일에 해당하는 보스의 개별 참가 설정을 반환한다."""
        _, index = self.target_for(weekday)
        key = "mountain_tiger_enabled" if index == 0 else "skeleton_general_enabled"
        return bool(self.cfg.get(key, True))

    def due(self, wall):
        if (not self.enabled or not self.target_enabled(wall.weekday())
                or self.attempted_date == wall.date()):
            return False
        current = wall.time().replace(tzinfo=None)
        end_seconds = (self.starts.hour * 60 + self.starts.minute) * 60 + self.duration
        current_seconds = (current.hour * 60 + current.minute) * 60 + current.second
        prepare_seconds = (self.prepare.hour * 60 + self.prepare.minute) * 60
        return prepare_seconds <= current_seconds < end_seconds

    def status(self):
        labels = {
            "stop_hunt": "기존 사냥 정지", "open_event": "보스토벌 열기",
            "event_menu": "보스 선택", "world_map": "축지술 확인",
            "traveling": "보스 위치 이동", "waiting": "출현 대기",
            "fighting": "보스 전투", "stop_after": "전투 종료 확인",
        }
        return labels.get(self.phase, self.phase)

    def _set_phase(self, phase, now):
        self.phase, self.phase_at = phase, now
        log.info("보스토벌 상태 → %s", self.status())

    def _start(self, wall, now):
        if getattr(self.bot, "normal_mode", False):
            prepare_origin = getattr(self.bot, "_ensure_normal_origin", None)
            if prepare_origin and not prepare_origin():
                log.warning("일반 모드 원래 위치를 저장하지 못함: 보스토벌 종료 후 현재 위치에서 재개할 수 있음")
        self.active_date = wall.date()
        self.target_name, _ = self.target_for(wall.weekday())
        self._old_source = getattr(self.bot, "_source", None)
        self.bot._source = "보스토벌"
        self.boss_seen = False
        self.boss_last_seen = 0.0
        self.free_walk_started = False
        self.paid_travel_started = False
        self._set_phase("stop_hunt", now)
        log.warning("보스토벌 준비 시작: %s (%s 출현, 축지술 금액 제한 없이 참가)",
                    self.target_name, self.starts.strftime("%H:%M"))

    def _event_icon(self, frame):
        return self.bot.matcher.find(frame, self.cfg.get("icon_template", "boss_hunt_icon.png"),
                                     self.EVENT_REGION, float(self.cfg.get("icon_threshold", 0.88)))

    def _event_open(self, frame):
        return bool(self.bot.matcher.find(frame, self.cfg.get("title_template", "boss_hunt_title.png"),
                                          [0.04, 0.0, 0.12, 0.08], 0.86))

    def _at_boss_field(self, frame):
        """보스 전용 필드에 이미 도착했는지 좌상단 지역명으로 확인한다."""
        title = _ocr(_crop(frame, (0, 0, 430, 105)), 2).replace(" ", "")
        expected = "산군대호" if "산군" in self.target_name else "해골장군"
        return expected in title

    def on_user_resume(self, now):
        """사용자 조작 대기 시간 때문에 현재 단계가 시간 초과되지 않게 한다."""
        if self.phase == "idle":
            return
        self.phase_at = now
        self._input_at = -1e9
        log.info("보스토벌 사용자 조작 대기 종료 → 현재 화면에서 단계 재확인")

    def _resync(self, snap, now):
        """수동 조작으로 화면이 앞서 간 경우 실제 화면에 상태 흐름을 맞춘다."""
        if self.phase not in ("open_event", "event_menu", "world_map", "traveling"):
            return
        if getattr(snap, "in_field", False) and self._at_boss_field(snap.frame):
            if self.phase != "waiting":
                self._set_phase("waiting", now)
            return
        # traveling 중에는 클릭 직전의 세계지도 프레임이 잠시 남아도 이전 단계로 되돌리지 않는다.
        if self.phase in ("open_event", "event_menu", "world_map") and self._map_target_ready(snap.frame):
            if self.phase != "world_map":
                self._set_phase("world_map", now)
            return
        if self.phase in ("open_event", "world_map") and self._event_open(snap.frame):
            # 세계지도 버튼 입력이 Unity에서 무시됐으면 이벤트 화면이 그대로 남는다.
            # 예전에는 world_map 단계에서 25초를 허비했지만 이제 실제 화면으로 되돌린다.
            if self.phase != "event_menu":
                self._set_phase("event_menu", now)

    @staticmethod
    def _clean_ocr(value):
        return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()

    @classmethod
    def _has_fuzzy(cls, value, wanted, minimum=0.72):
        """짧은 게임 글자의 한 글자 OCR 오독을 허용한다."""
        text = cls._clean_ocr(value)
        needle = cls._clean_ocr(wanted)
        if needle in text:
            return True
        if not text or not needle:
            return False
        width = len(needle)
        for size in range(max(2, width - 1), width + 2):
            for start in range(max(1, len(text) - size + 1)):
                part = text[start:start + size]
                matches = sum(a == b for a, b in zip(part, needle))
                if matches / max(len(part), len(needle)) >= minimum:
                    return True
        return False

    def _map_evidence(self, frame):
        right = _ocr(_crop(frame, (1440, 70, 1910, 235)), 2).replace(" ", "")
        buttons = _ocr(_crop(frame, (1450, 940, 1915, 1075)), 2).replace(" ", "")
        price_text = _ocr(_crop(frame, (1710, 940, 1915, 995)), 2).replace(" ", "")
        expected = "산군대호" if "산군" in self.target_name else "해골장군"
        amounts = []
        for value in re.findall(r"\d[\d,.]*", price_text.replace("O", "0").replace("o", "0")):
            digits = re.sub(r"\D", "", value)
            if digits:
                amounts.append(int(digits))
        observed_price = max(amounts, default=None)
        evidence = {
            "target": self._has_fuzzy(right, expected),
            "walk": self._has_fuzzy(buttons, "걸어가기", 0.66),
            "travel": self._has_fuzzy(buttons, "축지술", 0.66),
            "price": True,
            "observed_price": observed_price,
            "right_text": re.sub(r"\s+", " ", right).strip()[:180],
            "button_text": re.sub(r"\s+", " ", buttons).strip()[:180],
        }
        return evidence

    def _map_target_ready(self, frame):
        evidence = self._map_evidence(frame)
        self._last_map_evidence = evidence
        return evidence["target"] and evidence["travel"]

    def _walk_target_ready(self, frame):
        evidence = self._map_evidence(frame)
        self._last_map_evidence = evidence
        return evidence["target"] and evidence["walk"]

    def _log_map_evidence(self, now, force=False):
        if not self._last_map_evidence or (not force and now - self._last_evidence_log < 5.0):
            return
        item = self._last_map_evidence
        log.info("보스토벌 지도 검증: 보스명=%s 걸어가기=%s 축지술=%s 금액=%s(제한 없음) · 우측='%s' · 버튼='%s'",
                 item["target"], item["walk"], item["travel"],
                 f'{item.get("observed_price"):,}' if item.get("observed_price") is not None else "인식 실패",
                 item["right_text"], item["button_text"])
        self._last_evidence_log = now

    def _save_failure(self, frame, reason):
        folder = os.path.join(ROOT, "runs", "boss-hunt")
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(folder, f"failure-{stamp}.png")
        if save_image(path, frame):
            log.warning("보스토벌 실패 화면 저장: %s (%s)", path, reason)
        return path

    def _boss_visible(self, snap):
        preset = getattr(self.bot, "preset", None)
        if preset:
            return bool(preset.boss_visible(snap.frame))
        return bool(self.bot.matcher.find(snap.frame, "combat_boss.png", [0.35, 0.04, 0.3, 0.06]))

    def _press_hunt(self, snap, want_on, now):
        if bool(snap.auto_on) == want_on:
            return False
        if now - self._input_at < 3.0:
            return True
        self.bot._press("보스토벌 자동 사냥 " + ("켜기" if want_on else "끄기"), "t")
        self._input_at = now
        return True

    def _restore(self, now, message, completed=True):
        farm = getattr(self.bot, "farm", None)
        farming = bool(farm and (getattr(self.bot, "farm_mode", False)
                                 or now < getattr(self.bot, "_farm_until", 0.0)))
        normal = bool(getattr(self.bot, "normal_mode", False))
        if farming:
            farm.need_travel = True
            farm.next_try = 0.0
            farm.next_eval = 0.0
        elif normal and getattr(self.bot, "_normal_origin", None):
            self.bot._return_normal_origin(now)
        self.bot._want_hunt = False
        self.bot._hunt_tries = 0
        self.bot._hunt_press_at = 0.0
        if self._old_source is not None:
            self.bot._source = self._old_source
        tail = " → 기존 앵벌 사냥터 복귀" if farming else " → 일반 모드 원래 지역 복귀" if normal else " → 기존 진행 재개"
        log.warning("%s%s", message, tail)
        if completed and self.active_date is not None:
            self.attempted_date = self.active_date
        self.phase = "idle"
        self.active_date = None

    def _abort(self, now, message):
        try:
            key = "m" if self.phase in ("world_map", "traveling") else "esc"
            self.bot._press("보스토벌 화면 닫기", key)
        except Exception:  # 입력 안전 검사가 중단시킨 경우 다음 루프가 처리한다
            pass
        self._restore(now, message, completed=False)
        self.next_try = now + self.retry_seconds
        log.info("보스토벌 입장 실패 → %.0f초 뒤 일정 시간 안에서 재시도", self.retry_seconds)
        return True

    def tick(self, snap, now, wall=None):
        """이 틱을 보스토벌이 독점하면 True. 사망은 기존 사망 처리에 맡겨 False."""
        wall = wall or datetime.now()
        if self.phase == "idle":
            if now < self.next_try or not self.due(wall) or not getattr(snap, "in_field", False):
                return False
            self._start(wall, now)

        if getattr(snap, "state", "") == "DEAD":
            return False

        self._resync(snap, now)

        if self.phase == "stop_hunt":
            if self._press_hunt(snap, False, now):
                return True
            self._set_phase("open_event", now)

        if self.phase == "open_event":
            # 사용자가 먼저 아이콘을 눌렀거나 이전 입력이 늦게 반영된 경우 중복 클릭하지 않는다.
            if self._event_open(snap.frame):
                self._set_phase("event_menu", now)
                return True
            match = self._event_icon(snap.frame)
            if not match:
                if now - self.phase_at > 30:
                    return self._abort(now, "보스토벌 아이콘을 찾지 못해 이번 회차 중단")
                return True
            self.bot._click_frame("보스토벌 아이콘", match.x, match.y)
            self._input_at = now
            self._set_phase("event_menu", now)
            return True

        if self.phase == "event_menu":
            if not self._event_open(snap.frame):
                if now - self.phase_at > 20:
                    return self._abort(now, "보스토벌 화면이 열리지 않아 이번 회차 중단")
                return True
            _, card = self.target_for(wall.weekday())
            if now - self._input_at >= 1.0 and now - self.phase_at < 3.0:
                self.bot._click_frame(self.target_name + " 선택", *self.EVENT_CARD[card])
                self._input_at = now
                return True
            if now - self.phase_at >= 3.0 and now - self._input_at >= 1.0:
                self.bot._click_frame("보스토벌 세계지도 이동", *self.EVENT_MAP)
                self._input_at = now
                self._set_phase("world_map", now)
            return True

        if self.phase == "world_map":
            start_seconds = (self.starts.hour * 60 + self.starts.minute) * 60
            current_seconds = (wall.hour * 60 + wall.minute) * 60 + wall.second
            if current_seconds < start_seconds and not self.free_walk_started:
                if self._walk_target_ready(snap.frame):
                    self.bot._click_frame("보스토벌 5분 전 무료 걸어가기", *self.WALK_TRAVEL)
                    self._input_at = now
                    self.free_walk_started = True
                    self._set_phase("traveling", now)
                    return True
                self._log_map_evidence(now)
                if now - self.phase_at > 25:
                    self._save_failure(snap.frame, "대상/걸어가기 검증 실패")
                    return self._abort(now, "대상 보스 또는 걸어가기 버튼을 확인하지 못해 이동 취소")
                return True
            if self._map_target_ready(snap.frame):
                # 0.5초 전 프레임을 믿고 유료 버튼을 누르지 않는다. 입력 직전에 다시 캡처해
                # 대상명·축지술·금액이 그대로인지 재확인한다.
                fresh = snap.frame
                window = getattr(self.bot, "window", None)
                if window is not None and not getattr(self.bot, "dry_run", False):
                    self.bot._check_user()
                    fresh = window.grab()
                if not self._map_target_ready(fresh):
                    log.info("보스토벌 유료 이동 입력 직전 화면이 바뀌어 클릭 취소")
                    return True
                # 프로젝트 전체에서 유료 이동은 금지. 이 한 줄만 사용자 승인 예외다.
                price = (self._last_map_evidence or {}).get("observed_price")
                amount = f"엽전 {price:,}" if price is not None else "표시 금액"
                self.bot._click_frame(f"보스토벌 축지술 {amount}", *self.INSTANT_TRAVEL)
                self._input_at = now
                self.paid_travel_started = True
                self._set_phase("traveling", now)
                return True
            self._log_map_evidence(now)
            if now - self.phase_at > 25:
                self._log_map_evidence(now, force=True)
                self._save_failure(snap.frame, "대상/축지술/금액 검증 실패")
                return self._abort(now, "대상 보스 또는 축지술 버튼을 확인하지 못해 유료 이동 취소")
            return True

        if self.phase == "traveling":
            if (getattr(snap, "in_field", False) and self._at_boss_field(snap.frame)
                    and now - self.phase_at >= 3.0):
                self._set_phase("waiting", now)
                return True
            start_seconds = (self.starts.hour * 60 + self.starts.minute) * 60
            current_seconds = (wall.hour * 60 + wall.minute) * 60 + wall.second
            if (self.free_walk_started and not self.paid_travel_started
                    and current_seconds >= start_seconds):
                log.warning("보스토벌 출현 시각까지 미도착 → 금액 제한 없이 축지술 전환")
                self._set_phase("open_event", now)
                return True
            if now - self.phase_at > self.travel_timeout:
                return self._abort(now, "보스 위치 이동을 확인하지 못해 이번 회차 중단")
            return True

        if self.phase in ("waiting", "fighting"):
            start_seconds = (self.starts.hour * 60 + self.starts.minute) * 60
            current_seconds = (wall.hour * 60 + wall.minute) * 60 + wall.second
            if current_seconds >= start_seconds:
                self._press_hunt(snap, True, now)
            visible = self._boss_visible(snap)
            if visible:
                if not self.boss_seen:
                    log.warning("보스토벌 보스 체력바 감지 → 전투 시작")
                self.boss_seen = True
                self.boss_last_seen = now
                self.phase = "fighting"
            elif self.boss_seen and now - self.boss_last_seen >= self.finish_absent:
                self._set_phase("stop_after", now)
            elif current_seconds > start_seconds + self.duration + 30:
                self._set_phase("stop_after", now)
            return True

        if self.phase == "stop_after":
            if self._press_hunt(snap, False, now):
                return True
            self._restore(now, "보스토벌 종료 확인")
            return True
        return False
