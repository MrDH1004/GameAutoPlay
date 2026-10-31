"""HP 물약 자동 구매: 타바로 성 잡화 상점(복분이)에서 한 번에 정해진 수량만 산다.

경로: M(세계지도) → 왼쪽 '위치' → 저장해 둔 '타바로 성(112,104)'(복분이 앞) → 걸어가기(무료)
      → 도착하면 G(상호작용) → 잡화 상점 → 2단 체력 회복 물약 → 목표 수량 조절 → 확인 → 구매 → 뒤로.
축지술(유료)은 쓰지 않는다. 좌표는 1920x1080 기준.
"""
import logging
import re
import time
from datetime import date

from .farm import BotDriver, StopRequested, _binarize, _crop, _ocr
from .rewards import BACK, _load_state, _save_state, _seen, _tpl

log = logging.getLogger(__name__)

POTION_COUNT_BOXES = [(812, 1016, 852, 1040)]                                # 하단 Ctrl+1 칸 숫자 (물약은 Ctrl+1 하나뿐)
SIDE_SAVED = (50, 400)                          # 세계지도 왼쪽 '위치' 아이콘
WALK_BTN = (1634, 1019)
SHOP_ROW = {"2단": (220, 300), "1단": (220, 196)}
QTY_MAX = (1093, 558)
QTY_MINUS = (868, 558)
QTY_OK = (1047, 679)
BUY = (1615, 1036)


def _white_mask(crop):
    """흰 숫자만 남겨 흰 바탕에 검은 글자로 (물약 개수는 흰색, 배경은 어둡거나 알록달록해서 이 방식이 가장 잘 읽힌다)."""
    import cv2
    import numpy as np
    m = (crop.min(axis=2) > 200).astype(np.uint8) * 255
    img = cv2.copyMakeBorder(255 - m, 10, 10, 14, 14, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _read_count(crop):
    """여러 방식으로 읽어 가장 많이 나온 값을 쓴다 (한 번만 읽으면 '204'를 '2'나 '304'로 잘못 읽어 물약이 부족한 것으로 착각할 수 있다).
    같은 표가 나오면 자릿수가 많은 쪽."""
    from collections import Counter
    tries = [(_white_mask(crop), 4), (_white_mask(crop), 3), (crop, 3), (_binarize(crop, 170), 3), (crop, 4)]
    votes = Counter()
    for img, sc in tries:
        m = re.search(r"\d{1,3}", _ocr(img, sc))
        if m:
            votes[int(m.group(0))] += 1
    if not votes:
        return None
    return max(votes.items(), key=lambda kv: (kv[1], len(str(kv[0]))))[0]


def read_potion_count(frame):
    """하단 물약 칸(Ctrl+1)의 개수. 못 읽으면 None."""
    vals = [v for v in (_read_count(_crop(frame, box)) for box in POTION_COUNT_BOXES) if v is not None]
    return max(vals) if vals else None


def purchase_quantity(current, amount=200, stack_limit=250):
    """전체 구매 대신 고정 수량을 살 수 있는지 계산한다. 공간이 부족하면 구매하지 않는다."""
    current = max(0, int(current))
    amount = max(0, int(amount))
    return amount if current + amount <= int(stack_limit) else 0


class PotionShop:
    def __init__(self, bot, cfg):
        self.bot = bot
        self.drv = BotDriver(bot)
        self.cfg = cfg or {}
        self.below = int(self.cfg.get("buy_below", 50))
        self.purchase_amount = int(self.cfg.get("purchase_amount", 200))
        self.stack_limit = int(self.cfg.get("stack_limit", 250))
        self.item = self.cfg.get("item", "2단")
        self.check_every = float(self.cfg.get("check_minutes", 5)) * 60
        self.max_per_day = int(self.cfg.get("auto_per_day", 1))     # 자동 구매 하루 최대 횟수 (수동 버튼은 제한 없음)
        self._limit_logged = None
        self._fails = 0                 # 오늘 시도 횟수 (상점 도착 전 실패 재시도 제한용)
        self._fails_day = None
        self.next_check = 0.0
        self.requested = False          # 제어판 버튼
        self.last_count = None
        self.t_map = _tpl("map_title.png")
        self.t_saved = _tpl("map_saved_potion.png")
        self.t_shop = _tpl("shop_title.png")
        self.t_qty = _tpl("shop_qty_title.png")

    # ---------- 판단 ----------
    def due(self, snap, now):
        if self.requested:
            return True
        if now < self.next_check or snap.state not in ("IDLE", "COMBAT", "MOVING"):
            return False
        self.next_check = now + self.check_every
        n = read_potion_count(snap.frame)
        if n is not None:
            self.last_count = n
            if n <= self.below:
                used = self.auto_today()
                if self._fails_day != date.today():
                    self._fails_day, self._fails = date.today(), 0
                if self._fails >= 3:
                    used = max(used, self.max_per_day)
                if used >= self.max_per_day:
                    if self._limit_logged != date.today():
                        self._limit_logged = date.today()
                        log.warning("HP 물약 %d개지만 오늘 자동 구매 %d회를 이미 함 → 내일까지 자동 구매 안 함 (버튼으로 수동 구매 가능)",
                                    n, used)
                        self.bot.notifier.send(f"HP 물약 {n}개 남음. 오늘 자동 구매는 끝 (수동 구매 버튼 사용)", key="potion_limit")
                    return False
                log.info("HP 물약 %d개 (<= %d) → 물약 %d개 자동 구매하러 감 (오늘 %d/%d회)",
                         n, self.below, self.purchase_amount, used + 1, self.max_per_day)
                self._auto = True
                return True
        return False

    @staticmethod
    def auto_today():
        st = _load_state()
        return st.get("potion_auto", {}).get(date.today().isoformat(), 0)

    @staticmethod
    def _count_auto():
        st = _load_state()
        today = date.today().isoformat()
        st["potion_auto"] = {today: st.get("potion_auto", {}).get(today, 0) + 1}    # 지난 날짜는 버린다
        _save_state(st)

    # ---------- 동작 ----------
    def _wait(self, tpl, box, secs):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if _seen(self.drv.shot(), tpl, box):
                return True
            self.drv.sleep(0.5)
        return False

    def _go_to_shop(self):
        d = self.drv
        if not _seen(d.shot(), self.t_map, (0, 0, 480, 90)):
            d.key("m")
            if not self._wait(self.t_map, (0, 0, 480, 90), 5):
                log.warning("물약 구매: 세계지도가 열리지 않음")
                return False
        d.click(*SIDE_SAVED)
        d.sleep(1.2)
        import cv2
        shot = d.shot()
        area = _crop(shot, (100, 130, 410, 900))
        res = cv2.matchTemplate(area, self.t_saved, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx < 0.8:
            log.warning("물약 구매: 저장된 위치 '타바로 성(112,104)'를 찾지 못함 (일치도 %.2f)", mx)
            d.key("m")
            return False
        h, w = shot.shape[:2]
        sx, sy = w / 1920.0, h / 1080.0
        d.click(100 + (loc[0] + self.t_saved.shape[1] / 2) / sx, 130 + (loc[1] + self.t_saved.shape[0] / 2) / sy)
        d.sleep(1.2)
        d.click(*WALK_BTN)                       # 걸어가기(무료)
        d.sleep(2.0)
        if _seen(d.shot(), self.t_map, (0, 0, 480, 90)):
            d.key("m")                           # 지도가 남아 있으면 닫는다 (이동은 계속됨)
        return True

    def _arrive_and_open(self, timeout):
        """이동이 끝나기를 기다렸다가 G로 상점을 연다."""
        d = self.drv
        end = time.monotonic() + timeout
        still = 0
        while time.monotonic() < end:
            snap = self.bot.eyes.look(d.shot(), time.monotonic())
            if snap.state == "MOVING":
                still = 0
            else:
                still += 1
            if still >= 3:
                d.key("g")
                if self._wait(self.t_shop, (0, 0, 480, 90), 4):
                    return True
                still = 0
            d.sleep(1.0)
        return False

    def _buy(self, current):
        d = self.drv
        quantity = purchase_quantity(current, self.purchase_amount, self.stack_limit)
        if quantity <= 0:
            log.warning("물약 구매 생략: 현재 %d개라 %d개를 더 담을 공간이 없음 (소지 한도 %d)",
                        current, self.purchase_amount, self.stack_limit)
            return False
        d.click(*SHOP_ROW.get(self.item, SHOP_ROW["2단"]))
        if not self._wait(self.t_qty, (760, 340, 1160, 420), 4):
            log.info("물약 구매: 수량 창이 안 뜸 (이미 한도까지 있음)")
            return False
        d.click(*QTY_MAX)
        d.sleep(0.6)
        # 최대 구매 수량에서 원하는 고정 구매량을 초과하는 만큼 뺀다.
        decrement = max(0, (self.stack_limit - current) - quantity)
        for index in range(decrement):
            d.click(*QTY_MINUS)
            if index and index % 10 == 0:
                d.sleep(0.1)
        log.info("물약 구매 수량 설정: 현재 %d + 구매 %d = 구매 후 %d (최대에서 %d회 감소)",
                 current, quantity, current + quantity, decrement)
        d.click(*QTY_OK)
        d.sleep(1.0)
        d.click(*BUY)
        d.sleep(2.0)
        return True

    def run(self, now):
        manual = self.requested
        self.requested = False
        auto = not manual and getattr(self, "_auto", False)
        self._auto = False
        log.info("물약 %s 구매", "수동(버튼)" if manual else "자동")
        self.next_check = now + self.check_every
        current = self.last_count
        if current is None:
            current = read_potion_count(self.drv.shot())
        if current is None:
            log.warning("물약 구매 보류: 현재 보유량을 읽지 못해 200개 구매 가능 여부를 안전하게 계산할 수 없음")
            return False
        if not purchase_quantity(current, self.purchase_amount, self.stack_limit):
            log.info("물약 구매 생략: 현재 %d개라 %d개 구매 시 소지 한도 %d개를 넘음",
                     current, self.purchase_amount, self.stack_limit)
            return True
        log.info("물약 구매 시작 (현재 %d개 + 구매 %d개, 타바로 성 잡화 상점)",
                 current, self.purchase_amount)
        if auto:
            self._fails += 1                    # 상점 도착 전 실패(지도 안 열림 등)는 하루 3번까지 10분 뒤 재시도
            if self._fails < 3:
                self.next_check = now + 600
        try:
            if not self._go_to_shop():
                return False
            if not self._arrive_and_open(float(self.cfg.get("walk_timeout", 600))):
                if auto:
                    self._count_auto()          # 걸어가서 못 연 경우는 반복하지 않는다
                log.warning("물약 구매: 상점에 도착/열기 실패")
                self.bot.notifier.send("물약 구매 실패 (상점에 도착하지 못함)", key="potion_shop")
                return False
            ok = self._buy(current)
            if auto:
                self._count_auto()              # 상점까지 가서 산 것만 오늘 1회로 센다
            d = self.drv
            d.click(*BACK)
            d.sleep(1.5)
            n = read_potion_count(d.shot())
            log.info("물약 구매 끝 → %s개", n if n is not None else "?")
            self.last_count = n
            return ok
        except StopRequested:
            return False
