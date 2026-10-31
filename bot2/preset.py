"""우측 하단 H 빠른 메뉴로 저장된 도술 프리셋 A~E를 전환한다."""
import logging

from .farm import BotDriver

log = logging.getLogger(__name__)

# 1920x1080 필드 HUD의 H 빠른 프리셋 메뉴. H를 누르면 A~E가 위로 펼쳐진다.
PRESET_ROWS = {
    1: (1558, 931), 2: (1558, 867), 3: (1558, 803),
    4: (1558, 739), 5: (1558, 675),
}


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

    def boss_visible(self, frame):
        return bool(self.bot.matcher.find(frame, "combat_boss.png", self.region))

    def switch(self, n):
        n = max(1, min(5, int(n)))
        d = self.drv
        d.key("h")
        d.sleep(0.5)
        d.click(*PRESET_ROWS[n])
        d.sleep(0.7)
        self.current = n
        log.info("H 빠른 전환 → 프리셋 %s (%s)", chr(64 + n),
                 "단일" if n == self.boss else "광역")
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
