"""실시간 상황판: 도깨비헬퍼 대시보드에 보여 줄 값(지도·잡는 몹·물약·퀘스트·동기화)을 주기적으로 읽는다.

OCR이 느려서(회당 0.7초) 봇 루프와 분리된 별도 스레드에서 몇 초에 한 번 읽는다. 읽기만 하고 아무것도 누르지 않는다.
동기화 = 봇이 게임 화면과 얼마나 잘 맞아 돌고 있는지:
  * 인식률: 최근 N번 화면에서 '아는 화면'(필드/사망/대화 등)으로 분류한 비율
  * 마지막 인식 후 경과 시간
  * 지금 대기 중인 사유 (게임 창이 앞이 아님 / 사용자 조작 대기 / 일시정지)
"""
import logging
import re
import threading
import time
from collections import deque

from .farm import _crop, _ocr
from .safety import hp_ratio, target_fill
from .shop import read_potion_count
from .startup import read_map_name

log = logging.getLogger(__name__)

TARGET_NAME_BOX = (860, 72, 1100, 96)       # 상단 타겟 몹 '⚔170K 우물 박쥐'
QUEST_TOP_BOX = (1540, 105, 1830, 240)      # 트래커 맨 위 (위쪽 '도사 수행록' 위젯 아래에 본편 퀘스트가 나옴)


def read_target(frame):
    """현재 타겟 몹: {'name', 'power', 'fill'} (타겟이 없으면 None)."""
    fill = target_fill(frame)
    if fill <= 0.02:
        return None
    txt = _ocr(_crop(frame, TARGET_NAME_BOX), 2)
    power = None
    m = re.search(r"(\d+(?:\.\d)?)\s*([KkMm])", txt)
    if m:
        power = int(round(float(m.group(1)) * (1000 if m.group(2) in "Kk" else 1_000_000)))
    name = re.sub(r"\d+(?:\.\d)?\s*[KkMm]|\d+\s*m\b|[^\w가-힣 ]", " ", txt)
    name = re.sub(r"\s+", " ", name).strip()
    return {"name": name or "?", "power": power, "fill": fill}


def read_quest(frame):
    """트래커 맨 위 본편 퀘스트 제목 (예: '[5장] 우물동굴 소탕 3'). 못 읽으면 ''."""
    txt = _ocr(_crop(frame, QUEST_TOP_BOX), 2)
    m = (re.search(r"\[\s*\d+\s*장\s*\][^\[\n]{1,30}", txt)
         or re.search(r"\[[^\]]{1,8}\][^\[\n]{1,30}", txt))
    return m.group(0).strip()[:40] if m else ""


class LiveStatus:
    def __init__(self, bot, interval=3.0):
        self.bot = bot
        self.interval = interval
        self.data = {}                    # 대시보드가 읽는 최신 값
        self._seen = deque(maxlen=20)     # 최근 화면 분류 결과 (True=아는 화면)
        self._last_ok = 0.0
        self._stop = threading.Event()
        self._thread = None

    # ---- 봇 루프에서 매 틱 호출 (가벼움) ----
    def note_frame(self, known):
        """이번 틱 화면을 아는 화면으로 분류했는지 기록 (동기화 인식률용)."""
        self._seen.append(bool(known))
        if known:
            self._last_ok = time.monotonic()

    def sync(self):
        """동기화 요약: {'rate': 0~1, 'age': 마지막 인식 후 초, 'why': 대기 사유 또는 ''}"""
        n = len(self._seen)
        rate = (sum(self._seen) / n) if n else None
        age = (time.monotonic() - self._last_ok) if self._last_ok else None
        b = self.bot
        why = ""
        if getattr(b, "paused", False):
            why = "일시정지"
        elif getattr(b, "waiting_user", False):
            why = "사용자 조작 대기"
        elif not b.window.locate():
            why = "게임 창을 찾지 못함"
        elif b.window.is_minimized():
            why = "게임 창 최소화"
        elif not b.window.is_foreground():
            why = "게임 창이 앞이 아님"
        return {"rate": rate, "age": age, "why": why}

    # ---- 읽기 스레드 ----
    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="livestatus", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                if self.bot.stop_event.is_set():
                    return
                self.refresh()
            except Exception as e:  # noqa: BLE001
                log.debug("실시간 상황판 읽기 실패: %s", e)

    def refresh(self):
        b = self.bot
        if not b.window.locate() or b.window.is_minimized():
            return
        frame = b.window.grab()
        known = b._in_field(frame)
        d = {"at": time.monotonic(), "field": known}
        if known:
            d["map"] = read_map_name(frame)
            d["hp"] = round(hp_ratio(frame), 2)
            d["potions"] = read_potion_count(frame)
            d["target"] = read_target(frame)
            d["quest"] = read_quest(frame)
        else:
            d["map"], d["hp"], d["potions"], d["target"], d["quest"] = None, None, None, None, None
        self.data = {**self.data, **{k: v for k, v in d.items() if v is not None or k in ("target",)}}
        self.data["field"] = known
        if not known:
            self.data["target"] = None
