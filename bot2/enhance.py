"""도술 계열 강화 (선택 기능, 기본 꺼짐).

성장(U) → 도술 탭 → 계열 선택 → '자동 등록' → 재료 개수가 빨간색(부족)이 아니면 '강화'.
재료가 모자라면 아무것도 누르지 않는다. 강화 뒤 화면은 아직 확인하지 못해서, 뜨는 창은 ESC로 닫는다.
"""
import logging

import numpy as np

from .farm import BotDriver, _crop
from .rewards import BACK, _seen, _tpl

log = logging.getLogger(__name__)

TAB_DOSUL = (446, 111)
AUTO_REGISTER = (668, 1013)
ENHANCE = (1026, 1013)
MATERIAL_COUNT = (815, 895, 850, 918)
STYLE_ROWS = {"절명": 194, "연타": 285, "강타": 377, "척살": 468, "저격": 559, "주술": 650, "파멸": 742,
              "강인": 833, "기동": 924, "기만": 1015}
STYLE_X = 1680


class EnhanceTask:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.drv = BotDriver(bot)
        self.styles = cfg.get("styles", ["파멸", "저격"])
        self.interval = float(cfg.get("interval_minutes", 180)) * 60
        self.next_at = 0.0
        self.t_title = _tpl("growth_title.png")

    def due(self, now):
        return now >= self.next_at

    def _material_short(self):
        px = _crop(self.drv.shot(), MATERIAL_COUNT).astype(int)
        return float(((px[..., 2] > 180) & (px[..., 1] < 90)).mean()) > 0.02

    def run(self, now):
        self.next_at = now + self.interval
        d = self.drv
        d.key("u")
        for _ in range(6):
            d.sleep(0.6)
            if _seen(d.shot(), self.t_title, (0, 0, 400, 90)):
                break
        else:
            log.warning("도술 강화: 성장 화면이 열리지 않음")
            return 0
        d.click(*TAB_DOSUL)
        d.sleep(1.0)
        done = 0
        for style in self.styles:
            if style not in STYLE_ROWS:
                continue
            d.click(STYLE_X, STYLE_ROWS[style])
            d.sleep(1.0)
            d.click(*AUTO_REGISTER)
            d.sleep(1.0)
            if self._material_short():
                log.info("도술 강화: %s 재료 부족 → 건너뜀", style)
                continue
            log.info("도술 강화: %s 강화", style)
            d.click(*ENHANCE)
            d.sleep(3.0)
            if not _seen(d.shot(), self.t_title, (0, 0, 400, 90)):
                d.key("esc")                      # 결과 연출/확인 창
                d.sleep(1.0)
            done += 1
        d.click(*BACK)
        d.sleep(1.0)
        return done
