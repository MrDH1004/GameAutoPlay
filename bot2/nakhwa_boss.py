"""매일 22시 낙화의 전장 요화선녀 자동 입장 흐름."""
import logging
from datetime import datetime, time as wall_time


log = logging.getLogger(__name__)


class NakhwaBossTask:
    RETIRED_STATE_KEY = "nakhwa_boss_retired_day"

    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.prepare = self._parse_time(cfg.get("prepare_at", "21:58"), wall_time(21, 58))
        self.starts = self._parse_time(cfg.get("starts_at", "22:00"), wall_time(22, 0))
        self.entry_minutes = float(cfg.get("entry_window_minutes", 12))
        self.retry_seconds = float(cfg.get("retry_seconds", 15))
        self.attempted_date = None
        self.retired_date = self._load_retired_date()
        self.next_try = 0.0
        self.phase = "idle"

    @staticmethod
    def _parse_time(value, fallback):
        try:
            hour, minute = (int(part) for part in str(value).split(":", 1))
            return wall_time(hour, minute)
        except (TypeError, ValueError):
            return fallback

    @classmethod
    def _load_retired_date(cls):
        try:
            from .rewards import _load_state
            value = _load_state().get(cls.RETIRED_STATE_KEY)
            return datetime.strptime(str(value), "%Y-%m-%d").date() if value else None
        except (TypeError, ValueError):
            return None

    def retire_after_death(self, wall=None):
        """요화선녀에서 한 번 사망한 날은 재시작 후에도 재입장하지 않는다."""
        wall = wall or datetime.now()
        self.retired_date = wall.date()
        self.attempted_date = wall.date()
        self.phase = "retired"
        try:
            from .rewards import _load_state, _save_state
            state = _load_state()
            state[self.RETIRED_STATE_KEY] = self.retired_date.isoformat()
            _save_state(state)
        except Exception as exc:  # noqa: BLE001
            log.warning("요화선녀 사망 중단 상태 저장 실패: %s", exc)

    def retired_today(self, wall=None):
        wall = wall or datetime.now()
        return self.retired_date == wall.date()

    def retired_during_event(self, wall=None):
        wall = wall or datetime.now()
        if not self.retired_today(wall):
            return False
        current = wall.hour * 3600 + wall.minute * 60 + wall.second
        prepare = self.prepare.hour * 3600 + self.prepare.minute * 60
        end = self.starts.hour * 3600 + self.starts.minute * 60 + int(self.entry_minutes * 60)
        return prepare <= current < end

    def due(self, wall):
        if not self.enabled or self.attempted_date == wall.date() or self.retired_today(wall):
            return False
        current = wall.hour * 3600 + wall.minute * 60 + wall.second
        prepare = self.prepare.hour * 3600 + self.prepare.minute * 60
        end = self.starts.hour * 3600 + self.starts.minute * 60 + int(self.entry_minutes * 60)
        return prepare <= current < end

    def status(self):
        return {"idle": "대기", "entering": "입장 중", "retry": "입장 재시도 대기",
                "retired": "1회 사망 · 재입장 중단"}.get(
            self.phase, self.phase)

    def tick(self, snap, now, wall=None):
        """입장 시도 중인 틱만 독점한다. 성공 뒤에는 전투 콘텐츠 AUTO 흐름에 맡긴다."""
        wall = wall or datetime.now()
        if not self.due(wall) or now < self.next_try:
            return False
        if not getattr(snap, "in_field", False):
            return False

        mission = getattr(self.bot, "mission", None)
        map_name = str(getattr(self.bot, "_map_name", ""))
        if "낙화" in map_name:
            self.bot._source = "낙화 요화선녀"
            if mission is not None and mission.recover_nakhwa_center("요화선녀 위치 재확인"):
                self.attempted_date = wall.date()
                self.bot._want_hunt = False
                log.info("요화선녀 일정: 전장 내부 위치 재확인 → 점수표 및 현재 위치 AUTO 완료")
            else:
                self.phase = "retry"
                self.next_try = now + self.retry_seconds
                log.warning("요화선녀 일정: 전장 내부 준비 실패 → %.0f초 뒤 재시도", self.retry_seconds)
            return True

        if mission is None:
            self.phase = "retry"
            self.next_try = now + self.retry_seconds
            log.warning("요화선녀 일정: 임무 자동화가 없어 %.0f초 뒤 다시 확인", self.retry_seconds)
            return True

        self.phase = "entering"
        self.bot._source = "낙화 요화선녀"
        log.warning("요화선녀 준비 시작: 매일 22:00 · 낙화의 전장 일반 채널 입장")
        try:
            entered = mission._enter_nakhwa_battlefield(reason="요화선녀")
        except Exception as exc:  # noqa: BLE001
            log.warning("요화선녀 낙화의 전장 입장 오류: %s", exc)
            entered = False

        if entered:
            self.attempted_date = wall.date()
            self.phase = "idle"
            self.bot._want_hunt = False
            log.warning("요화선녀 준비 완료: 안전 위치 점수표 및 AUTO 사냥 시작")
        else:
            self.phase = "retry"
            self.next_try = now + self.retry_seconds
            save = getattr(self.bot, "_save_screen", None)
            if save:
                save(snap.frame, "nakhwa_boss_entry_failure")
            log.warning("요화선녀 입장 실패 → %.0f초 뒤 재시도", self.retry_seconds)
        return True
