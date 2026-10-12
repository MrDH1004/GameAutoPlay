"""보스 전투 감지 → 도술 프리셋 자동 전환 (1 일반 ↔ 2 보스).

도술 화면(K) 아래쪽 프리셋 선택(▲)을 열어 고른 뒤 뒤로가기로 닫는다 (약 4초).
보스 체력바(combat_boss.png)가 boss_seconds 이상 보이면 보스 프리셋, 보스가 normal_seconds 동안 안 보이면 일반 프리셋.
"""
import logging
import time

from .farm import BotDriver
from .rewards import BACK, _seen, _tpl

log = logging.getLogger(__name__)

PRESET_TOGGLE = (1323, 1033)
PRESET_ROWS = {1: (1245, 980), 2: (1245, 935), 3: (1245, 890)}


class PresetSwitcher:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.drv = BotDriver(bot)
        self.normal = int(cfg.get("normal", 1))
        self.boss = int(cfg.get("boss", 2))
        self.boss_secs = float(cfg.get("boss_seconds", 3))
        self.normal_secs = float(cfg.get("normal_seconds", 60))
        self.region = cfg.get("boss_region", [0.35, 0.04, 0.3, 0.06])
        self.current = self.normal          # 시작 시 일반 프리셋이라고 가정
        self._boss_since = None
        self._last_boss = 0.0
        self._last_switch = 0.0
        self.t_title = _tpl("dosul_title.png")

    def boss_visible(self, frame):
        return bool(self.bot.matcher.find(frame, "combat_boss.png", self.region))

    def switch(self, n):
        d = self.drv
        d.key("k")
        for _ in range(6):
            d.sleep(0.6)
            if _seen(d.shot(), self.t_title, (0, 0, 400, 90)):
                break
        else:
            log.warning("프리셋 전환: 도술 화면이 열리지 않음")
            return False
        d.click(*PRESET_TOGGLE)
        d.sleep(0.8)
        d.click(*PRESET_ROWS[n])
        d.sleep(0.8)
        d.click(*BACK)
        d.sleep(1.0)
        self.current = n
        log.info("도술 프리셋 → %d (%s)", n, "보스" if n == self.boss else "일반")
        return True

    def tick(self, snap, now):
        """전투/필드에서 매 틱 호출. 전환했으면 True."""
        if snap.state not in ("COMBAT", "IDLE", "MOVING"):
            return False
        boss = snap.state == "COMBAT" and self.boss_visible(snap.frame)
        if boss:
            self._last_boss = now
            self._boss_since = self._boss_since or now
        else:
            self._boss_since = None
        if now - self._last_switch < 20:            # 전환 연타 방지
            return False
        target = None
        if self.current != self.boss and self._boss_since and now - self._boss_since >= self.boss_secs:
            target = self.boss
        elif self.current == self.boss and now - self._last_boss >= self.normal_secs:
            target = self.normal
        if target is None:
            return False
        self._last_switch = now
        return self.switch(target)
