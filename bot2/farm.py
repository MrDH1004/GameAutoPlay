"""사냥터 고정(앵벌) 모드: 내 전투력에 맞는 사냥터를 골라 걸어가서 자동 사냥(T)만 유지한다.

구성
  - 사냥터 DB(farm_spots.json): tools/farm_scan.py가 세계지도의 '지역 정보' 목록을 훑어
    사냥터별 몬스터 전투력과 목록 카드 이미지를 저장해 둔다.
  - best_spot(power): 몬스터 전투력이 내 전투력 x margin 이하인 곳 중 가장 높은 곳을 고른다.
    전투력이 오르면 다음 재평가 때 더 센 사냥터로 올라가고, 사망하면 그 사냥터를 잠시 제외해 내려간다.
  - travel(): M(세계지도) → 지역 → 목록에서 카드 이미지를 찾아 선택 → 걸어가기(무료). 유료 축지술은 누르지 않는다.

좌표는 전부 1920x1080 기준이고 Driver가 실제 화면 크기에 맞춰 변환한다.
"""
import json
import logging
import os
import re
import time

import cv2
import numpy as np

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPOTS_FILE = os.path.join(ROOT, "farm_spots.json")
CARD_DIR = os.path.join(ROOT, "templates", "farm")

# ----- 화면 좌표 (1920x1080) -----
POWER_BOX = (110, 1045, 260, 1080)        # 필드 좌하단 "전투력 63,099"
MON_BOX = (1560, 228, 1890, 262)          # 지역 지도 오른쪽 몬스터 전투력 줄 (아이콘 아래 "51K")
MON_ICONS = (1560, 180, 1890, 262)
CRUMB_COUNTRY = (386, 46)                 # 세계지도 위쪽 "타바로국"
SIDE_REGION_INFO = (50, 183)              # 왼쪽 사이드바 '지역 정보' 아이콘
LIST_X = (110, 400)
LIST_Y = (135, 1075)
LIST_CLICK_X = 255
CARD_COL = 385                            # 카드 판별용 열 (밝은 회색이면 사냥터 카드)
WALK_BTN = (1634, 1019)
PANEL_TITLE = (1620, 90, 1790, 125)       # 오른쪽 패널 제목
LIST_TITLE = (100, 90, 260, 125)          # "지역 정보" 제목 (목록이 열려 있는지 판별)


class Driver:
    """화면 캡처/입력 추상화. 좌표는 1920x1080 기준."""
    def shot(self):
        raise NotImplementedError

    def click(self, x, y):
        raise NotImplementedError

    def drag(self, x1, y1, x2, y2):
        raise NotImplementedError

    def key(self, k):
        raise NotImplementedError

    def sleep(self, s):
        time.sleep(s)


# ---------- 읽기 ----------
def _ocr(crop, scale=3):
    from tools.ocr import read_text
    try:
        return " ".join(l["text"] for l in read_text(crop, scale=scale))
    except Exception as e:                       # noqa: BLE001
        log.debug("OCR 실패: %s", e)
        return ""


def _crop(img, box):
    """img는 1920x1080 기준 좌표로 자른다 (다른 크기면 비율 변환)."""
    h, w = img.shape[:2]
    sx, sy = w / 1920.0, h / 1080.0
    x0, y0, x1, y1 = box
    return img[int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]


_last_power = None          # 마지막으로 믿을 만하게 읽은 전투력 (자릿수 빠짐 같은 오독을 걸러내는 기준)
_odd_power = []             # 기준과 많이 다르게 읽힌 값들 (같은 값이 3번 연속이면 진짜 변화로 받아들임)


def _plausible(v):
    """직전 값에서 절반 미만으로 급락하거나 두 배를 넘는 값은 오독으로 본다 (예: 66,682 → 6,682)."""
    return _last_power is None or _last_power / 2 <= v <= _last_power * 2


def read_power(frame):
    """필드 화면에서 내 전투력(숫자)을 읽는다. 원본/이진화 여러 번 읽어 다수결. 못 읽으면 None.
    창 모드(작은 글씨)에서는 원본 OCR이 숫자를 틀리게 읽어 이진화 결과가 더 정확하다."""
    global _last_power
    v = _read_power_raw(frame)
    if v is None:
        return None
    if not _plausible(v):
        _odd_power.append(v)
        del _odd_power[:-3]
        if len(_odd_power) == 3 and len(set(_odd_power)) == 1:       # 진짜로 크게 바뀐 것 (장비 교체 등)
            log.info("전투력이 크게 바뀐 것으로 받아들임: %s → %s", f"{_last_power:,}", f"{v:,}")
            _last_power = v
            _odd_power.clear()
            return v
        log.warning("전투력 오독으로 판단해 무시: %s (직전 %s)", f"{v:,}", f"{_last_power:,}")
        return None
    _odd_power.clear()
    _last_power = v
    return v


def _read_power_raw(frame):
    from collections import Counter
    global _power_reads
    crop = _crop(frame, POWER_BOX)
    book = _digit_book()
    _power_reads += 1
    if book.complete and _power_reads % 10:          # 0~9 템플릿이 다 있으면 OCR 없이 (10번에 1번은 OCR로 교차 확인)
        v = book.read(crop)
        if v:
            return v
    votes = Counter()
    tries = [(crop, 2), (crop, 3)] + [(_binarize(crop, t), 3) for t in (150, 190)]
    for img, scale in tries:
        m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,})", _ocr(img, scale))
        if m:
            votes[int(m.group(1).replace(",", ""))] += 1
        if votes and votes.most_common(1)[0][1] >= 2:
            break
    if not votes:
        return None
    value, n = votes.most_common(1)[0]
    if n >= 2:
        book.learn(crop, value)                      # 확실히 읽은 값으로 숫자 템플릿을 배운다
    return value


_power_reads = 0
_book = None


def _digit_book():
    global _book
    if _book is None:
        from .digits import DigitBook
        _book = DigitBook("power")
    return _book


SLOT_X = (1565, 1646, 1727, 1808)          # 몬스터 아이콘 4칸의 왼쪽 x (1920 기준)
SLOT_LABEL_Y = (232, 258)


def parse_k(txt):
    """'51K', '84.5K', '1.2M', '8,760' 같은 표기를 정수 리스트로 (순서대로)."""
    out = []
    for m in re.finditer(r"(\d+(?:[.,]\d)?)\s*([KkMm])|(\d{1,3},\d{3})(?!\d)", txt):
        if m.group(3):
            out.append(int(m.group(3).replace(",", "")))
        else:
            v = float(m.group(1).replace(",", "."))
            out.append(int(round(v * (1000 if m.group(2) in "Kk" else 1_000_000))))
    return out


def clean_power(v):
    """아이콘(⚔)이 숫자 앞에 붙어 읽힌 경우를 보정: 200K 이상이면서 앞자리가 2/4/7이고 나머지가 두 자리면 앞자리를 뗀다."""
    if v >= 1_000_000:
        t = str(v // 1000)
        if len(t) == 4 and t[0] in "247":    # 앞에 아이콘 글자가 숫자로 붙음 (4150 → 150K)
            return int(t[1:]) * 1000
        if len(t) == 4 and t[2] == "1":      # 84.5K 의 '.'이 '1'로 읽힘 (8415K)
            return int(round(float(t[:2] + "." + t[3]) * 1000))
    if v >= 200_000:
        t = str(v // 1000)
        if t[0] in "247" and len(t) >= 3 and int(t[1:]) < 100:
            return int(t[1:]) * 1000 + v % 1000
    return v


def _binarize(crop, thr):
    b = 255 - (crop.max(axis=2) > thr).astype(np.uint8) * 255      # 글자(밝음/빨강)를 검정으로, 배경은 흰색
    b = cv2.copyMakeBorder(b, 10, 10, 14, 14, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(b, cv2.COLOR_GRAY2BGR)


def _slot_labels(frame):
    """슬롯 4칸의 '⚔51K' 줄을 잘라 (슬롯 있음 여부, 이미지) 리스트로."""
    out = []
    for x0 in SLOT_X:
        crop = _crop(frame, (x0 + 14, SLOT_LABEL_Y[0], x0 + 76, SLOT_LABEL_Y[1]))
        out.append(crop)
    return out


def read_monster_powers(frame, settings=((150, 3), (180, 3), (180, 4), (210, 3), (210, 4))):
    """지역 지도에서 선택한 사냥터의 몬스터 전투력 (슬롯별 다수결). 빈 슬롯은 건너뛴다."""
    from collections import Counter
    labels = _slot_labels(frame)
    # 글자가 거의 없는 슬롯(몬스터 없음)은 제외
    used = [i for i, c in enumerate(labels) if (c.max(axis=2) > 170).mean() > 0.08]
    if not used:
        return []
    votes = {i: Counter() for i in used}
    pool = Counter()
    for thr, sc in settings:
        # 슬롯 이미지를 흰 여백으로 이어 붙여 한 번에 읽는다
        gap = np.full((labels[0].shape[0], 24, 3), 0, np.uint8)
        strip = np.hstack([x for i in used for x in (labels[i], gap)])
        vals = [clean_power(v) for v in parse_k(_ocr(_binarize(strip, thr), sc))]
        for v in vals:
            pool[v] += 1
        if len(vals) == len(used):
            for i, v in zip(used, vals):
                votes[i][v] += 1
    out = []
    for i in used:
        if votes[i]:
            out.append(votes[i].most_common(1)[0][0])
    if not out and pool:
        out = [pool.most_common(1)[0][0]]
    return out


def consensus(vals):
    """슬롯 값들의 대표 전투력: 최빈값에서 2.5배 넘게 벗어난 오독 값을 버리고 남은 것 중 최댓값. 2개 미만이면 None."""
    from collections import Counter
    if len(vals) < 2:
        return None
    mode = Counter(vals).most_common(1)[0][0]
    kept = [v for v in vals if mode / 2.5 <= v <= mode * 2.5]
    return max(kept) if kept else None


def red_ratio(frame):
    """몬스터 전투력 줄에서 빨간 글자 비율 (빨강 = 나보다 강한 몬스터)."""
    px = _crop(frame, (SLOT_X[0], SLOT_LABEL_Y[0], SLOT_X[-1] + 76, SLOT_LABEL_Y[1])).astype(int)
    red = (px[:, :, 2] > 170) & (px[:, :, 1] < 110) & (px[:, :, 0] < 110)
    return float(red.mean())


# ---------- DB ----------
def load_spots(path=SPOTS_FILE):
    if not os.path.exists(path):
        return {"regions": {}, "spots": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_spots(db, path=SPOTS_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)


def best_spot(db, power, margin=0.9, banned=(), min_power=0):
    """몬스터 전투력이 power*margin 이하인 사냥터 중 몬스터가 가장 센 곳 (같으면 목록 앞쪽)."""
    limit = power * margin
    cands = [s for s in db["spots"]
             if s.get("power") and s["power"] <= limit and s["power"] >= min_power
             and s["id"] not in banned]
    if not cands:
        return None
    top = max(s["power"] for s in cands)
    return next(s for s in cands if s["power"] == top)


# ---------- 세계지도 탐색 ----------
def find_card_rows(img):
    """목록에서 사냥터 카드(밝은 회색)의 중심 y들. 지역 헤더(어두운 카드)는 제외."""
    h, w = img.shape[:2]
    sx, sy = w / 1920.0, h / 1080.0
    col = img[int(LIST_Y[0] * sy):int(LIST_Y[1] * sy), int(CARD_COL * sx)].astype(int)
    bright = col.mean(axis=1) > 150
    rows, start = [], None
    for i, b in enumerate(list(bright) + [False]):
        if b and start is None:
            start = i
        elif not b and start is not None:
            if i - start > 40 * sy:
                rows.append(int(((start + i) // 2) / sy) + LIST_Y[0])
            start = None
    return rows


def card_crop(img, y):
    """카드 한 장의 이름 부분 이미지 (1920 기준 y 중심)."""
    return _crop(img, (170, y - 22, 395, y + 22)).copy()


def list_open(img):
    """'지역 정보' 목록이 열려 있는가 (왼쪽 사이드바 옆 제목 영역이 밝은 글자)."""
    crop = _crop(img, LIST_TITLE)
    return crop.size > 0 and (crop.max(axis=2) > 200).mean() > 0.04


def scroll_shift(prev, cur):
    """prev→cur 목록이 위로 밀린 양 (1920 기준 px). 카드 이름 글자 띠를 템플릿 매칭 (밀림이 크면 아래쪽 띠부터)."""
    h, w = prev.shape[:2]
    sx, sy = w / 1920.0, h / 1080.0
    y0s, y1s = int(LIST_Y[0] * sy), int(LIST_Y[1] * sy)
    xa, xb = int(190 * sx), int(330 * sx)
    area = cur[y0s:y1s, xa:xb]
    best = None
    for y0 in range(1000, 300, -70):
        yy = int(y0 * sy)
        tpl = prev[yy:yy + int(30 * sy), xa:xb]
        if tpl.shape[0] < 20 or tpl.std() < 25:          # 빈 배경 띠는 건너뜀
            continue
        res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (mx, (yy - (loc[1] + y0s)) / sy)
        if mx > 0.9:
            break
    return best[1] if best and best[0] > 0.75 else None


class MapNav:
    def __init__(self, drv, cfg=None):
        self.drv = drv
        self.cfg = cfg or {}

    def _wait_list(self, tries=6):
        for _ in range(tries):
            if list_open(self.drv.shot()):
                return True
            self.drv.sleep(0.5)
        return False

    def map_open(self, img=None):
        """세계지도 화면인가 (왼쪽 위 '세계지도' 제목 템플릿)."""
        img = img if img is not None else self.drv.shot()
        h, w = img.shape[:2]
        tpl = cv2.imdecode(np.fromfile(os.path.join(ROOT, "templates", "map_title.png"), dtype=np.uint8), cv2.IMREAD_COLOR)
        scale = w / 1920.0
        if abs(scale - 1) > 0.01:
            tpl = cv2.resize(tpl, None, fx=scale, fy=scale)
        area = img[:int(h * 0.1), :int(w * 0.25)]
        if area.shape[0] < tpl.shape[0] or area.shape[1] < tpl.shape[1]:
            return False
        _, mx, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED))
        return mx > 0.8

    def close_map(self):
        if self.map_open():
            self.drv.key("m")
            self.drv.sleep(1.2)

    def open_region(self, region):
        """M → 타바로국 → 지역 배너 클릭 → 지역 정보 목록 열기. region은 DB의 regions[name] 정보."""
        d = self.drv
        if not self.map_open():
            d.key("m")
            d.sleep(1.8)
        d.click(*CRUMB_COUNTRY)
        d.sleep(1.5)
        d.click(*region["banner"])
        d.sleep(2.0)
        if not list_open(d.shot()):
            d.click(*SIDE_REGION_INFO)
            d.sleep(1.0)
        return self._wait_list()

    def scroll_top(self, n=8):
        for _ in range(n):
            self.drv.drag(LIST_CLICK_X, 300, LIST_CLICK_X, 950)
        self.drv.sleep(0.6)

    def find_and_select(self, spot, max_pages=14):
        """목록을 위에서부터 훑으며 저장된 카드 이미지와 같은 카드를 찾아 클릭한다."""
        d = self.drv
        tpl = cv2.imdecode(np.fromfile(os.path.join(CARD_DIR, spot["card"]), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.scroll_top()
        img = d.shot()
        for _ in range(max_pages):
            h, w = img.shape[:2]
            sx, sy = w / 1920.0, h / 1080.0
            y0s, y1s = int(LIST_Y[0] * sy), int(LIST_Y[1] * sy)
            xa, xb = int(170 * sx), int(395 * sx)
            area = img[y0s:y1s, xa:xb]
            if area.shape[0] > tpl.shape[0]:
                res = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
                _, mx, _, loc = cv2.minMaxLoc(res)
                if mx > 0.85:
                    y = (loc[1] + y0s) / sy + tpl.shape[0] / sy / 2
                    d.click(LIST_CLICK_X, int(y))
                    d.sleep(1.2)
                    return True
            d.drag(LIST_CLICK_X, 900, LIST_CLICK_X, 450)
            d.sleep(0.8)
            new = d.shot()
            if scroll_shift(img, new) is None:
                break
            img = new
        return False

    def travel(self, db, spot, red_check=False):
        """사냥터까지 걸어가기(무료). 성공하면 True, red_check이고 몬스터가 빨강(나보다 강함)이면 "red"."""
        region = db["regions"].get(spot["region"])
        if not region:
            log.warning("지역 정보 없음: %s", spot["region"])
            return False
        if not self.open_region(region):
            log.warning("세계지도 목록을 열지 못함")
            return False
        if not self.find_and_select(spot):
            log.warning("목록에서 사냥터 카드를 찾지 못함: %s", spot.get("name"))
            return False
        shot = self.drv.shot()
        vals = read_monster_powers(shot)
        log.info("사냥터 선택: %s (몬스터 %s)", spot.get("name"), vals)
        if red_check and red_ratio(shot) > 0.01:
            return "red"
        self.drv.click(*WALK_BTN)                # 걸어가기(무료). 축지술(유료)은 누르지 않는다
        self.drv.sleep(1.5)
        return True


class BotDriver(Driver):
    """Bot 창 위에서 동작하는 드라이버 (사용자 조작 감지/연습 모드를 Bot이 처리한다)."""
    def __init__(self, bot):
        self.bot = bot

    def shot(self):
        return self.bot.window.grab()

    def click(self, x, y):
        self.bot._click_frame("사냥터", x, y)

    def drag(self, x1, y1, x2, y2):
        self.bot._drag("사냥터 목록", x1, y1, x2, y2)

    def key(self, k):
        self.bot._press("사냥터", k)

    def sleep(self, s):
        end = time.monotonic() + s
        while time.monotonic() < end:
            if self.bot.stop_event.is_set():
                raise StopRequested()
            time.sleep(min(0.2, max(0.0, end - time.monotonic())))


class StopRequested(Exception):
    pass


class FarmTask:
    """전투력에 맞는 사냥터를 고르고, 이동/재평가/사망 후 복귀를 맡는다."""

    def __init__(self, bot, cfg):
        self.bot = bot
        self.cfg = cfg
        self.db = load_spots(os.path.join(ROOT, cfg.get("spots_file", "farm_spots.json")))
        self.margin = float(cfg.get("margin", 0.9))
        self.reselect = float(cfg.get("reselect_minutes", 15)) * 60
        self.upgrade_gain = float(cfg.get("upgrade_gain", 1.15))
        self.ban_secs = float(cfg.get("death_ban_minutes", 60)) * 60
        self.nav = MapNav(BotDriver(bot), cfg)
        self.guide = GuideNav(BotDriver(bot))
        self.use_guide = cfg.get("travel", "guide") == "guide"     # guide = 길라잡이 무료 순간이동, map = 세계지도 걸어가기
        if self.use_guide:
            self.db = {"regions": {}, "spots": guide_spots()}
        self.claim_every = float(cfg.get("claim_minutes", 30)) * 60
        self.next_claim = 0.0
        self.banned = {}                 # 사냥터 id → 해제 시각
        self.current = None
        self.power = None
        self.need_travel = True
        self.next_eval = 0.0
        self.next_try = 0.0
        self.fails = 0
        self.status = "대기"

    # --- 상태 조회 (GUI용) ---
    def describe(self):
        cur = self.current
        where = f"{cur.get('name') or cur['id']} (몬스터 {cur['power'] // 1000}K)" if cur else "-"
        pw = f"{self.power:,}" if self.power else "?"
        return f"{self.status} · 전투력 {pw} · 사냥터 {where}"

    def _active_bans(self, now):
        self.banned = {k: t for k, t in self.banned.items() if t > now}
        return set(self.banned)

    def pick(self, power, now):
        usable = {"spots": [s for s in self.db["spots"] if s.get("power") and not s.get("disabled")]}
        s = best_spot(usable, power, self.margin, self._active_bans(now))
        if not s:
            return None
        ties = [x for x in usable["spots"] if x["power"] == s["power"] and x["id"] not in self.banned]
        return ties[len(ties) // 2]       # 같은 전투력이면 가운데 사냥터 (헤더 카드/끝자락 회피)

    def on_death(self, now):
        """사망: 이 사냥터와 같거나 센 곳을 전부 한동안 제외해 한 단계 낮은 사냥터로 내려간다."""
        if self.current:
            hard = self.current["power"]
            for s in self.db["spots"]:
                if s.get("power") and s["power"] >= hard:
                    self.banned[s["id"]] = now + self.ban_secs
            log.warning("사냥터 '%s'(몬스터 %dK)에서 사망 → %d분간 이 단계 이상은 제외하고 더 낮은 곳으로 이동",
                        self.current.get("name") or self.current["id"], hard // 1000, self.ban_secs // 60)
        self.need_travel = True
        self.next_try = 0.0

    def _travel(self, spot, now):
        """한 사냥터로 이동. 빨간 몬스터(나보다 강함)면 제외하고 다음 후보로 최대 3번 시도."""
        for _ in range(3):
            self.status = f"이동 중: {spot.get('name') or spot['id']}"
            ok = (self.guide.travel(spot) if spot.get("guide")
                  else self.nav.travel(self.db, spot, red_check=True))
            if ok == "red":
                self.banned[spot["id"]] = now + self.ban_secs
                log.warning("'%s' 몬스터가 나보다 강함(빨강) → 제외", spot.get("name") or spot["id"])
                spot = self.pick(self.power, now)
                if not spot:
                    break
                continue
            if ok:
                self.current = spot
                self.need_travel = False
                self.fails = 0
                self.status = "사냥 중"
                self.bot._want_hunt = False       # 도착/이동 종료 감지가 T를 켠다
                return True
            break
        self.nav.close_map()
        self.guide.close()
        return False

    def maybe_claim(self, snap, now):
        """길라잡이 사냥터 미션 보상(권장 전투력 달성)을 주기적으로 받는다."""
        if now < self.next_claim or snap.state not in ("IDLE", "COMBAT") or not self.power:
            return
        self.next_claim = now + self.claim_every
        try:
            n = self.guide.claim_rewards(self.power)
            if n:
                log.info("길라잡이 보상 %d개 받음", n)
        except StopRequested:
            pass

    def tick(self, snap, now):
        """필드 화면에서 호출. 필요하면 사냥터를 정해 이동한다."""
        if now < self.next_try:
            return
        if not (self.need_travel or now >= self.next_eval):
            return
        if snap.state not in ("IDLE", "COMBAT", "MOVING"):
            return
        power = read_power(snap.frame)
        if power:
            if self.power and abs(power - self.power) / self.power > 0.02:
                log.info("전투력 변화: %s → %s", f"{self.power:,}", f"{power:,}")
            self.power = power
        if not self.power:
            log.warning("전투력을 읽지 못함 → 30초 뒤 재시도")
            self.next_try = now + 30
            return
        self.next_eval = now + self.reselect
        target = self.pick(self.power, now)
        if not target:
            self.bot._alert(f"전투력 {self.power:,}에 맞는 사냥터가 DB에 없음 (tools.farm_scan 으로 추가)")
            self.next_try = now + 300
            return
        cur = self.current
        if not self.need_travel and cur:
            too_hard = cur["power"] > self.power * self.margin * 1.05 or cur["id"] in self._active_bans(now)
            better = target["power"] >= cur["power"] * self.upgrade_gain
            if not (too_hard or better):
                self.status = "사냥 중"
                return
            log.info("사냥터 재선택: %s(%dK) → %s(%dK) (전투력 %s)", cur.get("name") or cur["id"],
                     cur["power"] // 1000, target.get("name") or target["id"], target["power"] // 1000,
                     f"{self.power:,}")
        else:
            log.info("사냥터 선택: %s (몬스터 %dK, 전투력 %s, 허용 %.0f%%)", target.get("name") or target["id"],
                     target["power"] // 1000, f"{self.power:,}", self.margin * 100)
        try:
            ok = self._travel(target, now)
        except StopRequested:
            return
        if not ok:
            self.fails += 1
            self.next_try = now + 60
            self.status = f"이동 실패({self.fails})"
            log.warning("사냥터 이동 실패 (%d회)", self.fails)
            if self.fails >= 3:
                self.bot._alert("사냥터로 이동하지 못함 (세계지도 화면 확인 필요)")
                self.fails = 0


# ---------- 길라잡이(F5) 추천 사냥터: 무료 순간이동 ----------
# 길라잡이 → 추천 사냥터 화면의 노드 좌표 (1920x1080). 권장 전투력 = 그 사냥터 몬스터 전투력.
GUIDE_ICON = (1408, 42)                  # 상단 F5 아이콘
GUIDE_GO = (1322, 865)                   # '사냥터 바로가기'
GUIDE_CLAIM = (1322, 790)                # '보상 받기' (사냥터 미션: 그 사냥터 권장 전투력 달성)
GUIDE_REC_BOX = (1195, 428, 1300, 460)   # 오른쪽 '권장' 숫자
GUIDE_CLOSE = (1485, 195)
GUIDE_INFO_BOX = (1160, 355, 1480, 395)    # 추천 사냥터 탭 오른쪽 '사냥터 정보' 제목 칸
GUIDE_TAB_RECO = (982, 257)              # 상단 탭 '추천 사냥터' (맞춤 추천 | 추천 사냥터 | 둘러보기)
GUIDE_NODES = [
    ("안개 기슭 1", 37000, (547, 800)),
    ("안개 기슭 2", 38500, (675, 800)),
    ("안개 기슭 지하 동굴", 51000, (803, 800)),
    ("산신의 영역 1", 68000, (931, 800)),
    ("산신의 영역 2", 84500, (1060, 747)),
    ("산신의 영역 지하 동굴 1", 94500, (1007, 623)),
    ("산신의 영역 지하 동굴 2", 110000, (879, 623)),
    ("황무지 마을 1", 135000, (751, 623)),
    ("황무지 마을 2", 150000, (623, 623)),
    ("우물 동굴 1", 170000, (494, 562)),
    ("우물 동굴 2", 215000, (547, 447)),
    ("음산한 폐광 1", 260000, (675, 447)),
    ("음산한 폐광 2", 290000, (803, 447)),
    ("독기 지대 1", 310000, (931, 447)),
    ("독기 지대 2", 335000, (1060, 393)),
]


def guide_spots():
    return [{"id": f"g{i:02d}", "region": "길라잡이", "name": n, "power": p, "node": list(xy), "guide": True}
            for i, (n, p, xy) in enumerate(GUIDE_NODES, 1)]


def read_number(frame, box):
    for scale in (3, 2, 4):
        m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{4,})", _ocr(_crop(frame, box), scale))
        if m:
            return int(m.group(1).replace(",", ""))
    return None


class GuideNav:
    """길라잡이 화면으로 사냥터 순간이동(무료)과 사냥터 미션 보상 수령."""

    def __init__(self, drv):
        self.drv = drv
        self._tpl = cv2.imdecode(np.fromfile(os.path.join(ROOT, "templates", "guide_title.png"), dtype=np.uint8),
                                 cv2.IMREAD_COLOR)

    def is_open(self, img=None):
        img = img if img is not None else self.drv.shot()
        area = _crop(img, (840, 160, 1080, 230))
        if area.shape[0] >= self._tpl.shape[0] and area.shape[1] >= self._tpl.shape[1]:
            _, mx, _, _ = cv2.minMaxLoc(cv2.matchTemplate(area, self._tpl, cv2.TM_CCOEFF_NORMED))
            if mx > 0.8:
                return True
        # 제목 줄을 시스템 공지 배너가 덮으면 제목으로는 못 찾는다 → 오른쪽 위 닫기 X로도 확인
        xt = self._close_tpl()
        xa = _crop(img, (1440, 160, 1530, 230))
        if xt is not None and xa.shape[0] >= xt.shape[0] and xa.shape[1] >= xt.shape[1]:
            _, mx, _, _ = cv2.minMaxLoc(cv2.matchTemplate(xa, xt, cv2.TM_CCOEFF_NORMED))
            return mx > 0.85
        return False

    def _close_tpl(self):
        if not hasattr(self, "_xtpl"):
            p = os.path.join(ROOT, "templates", "guide_close.png")
            self._xtpl = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR) if os.path.exists(p) else None
        return self._xtpl

    def open(self):
        """길라잡이를 열고 '추천 사냥터' 탭으로 맞춘다 (열 때마다 '맞춤 추천' 탭으로 시작할 수 있어, 사냥터 노드 좌표가 어긋났다)."""
        ok = self.is_open()
        if not ok:
            self.drv.click(*GUIDE_ICON)
            for _ in range(6):
                self.drv.sleep(0.6)
                if self.is_open():
                    ok = True
                    break
        if not ok:
            return False
        for _ in range(3):
            self.drv.sleep(1.0)                      # 창이 뜨는 애니메이션이 끝난 뒤에 눌러야 탭이 바뀐다
            self.drv.click(*GUIDE_TAB_RECO)
            self.drv.sleep(1.2)
            if "사냥터" in _ocr(_crop(self.drv.shot(), GUIDE_INFO_BOX), 2):      # 오른쪽 '사냥터 정보' 칸이 보이면 추천 사냥터 탭
                return True
        log.warning("길라잡이: '추천 사냥터' 탭으로 바뀌지 않음")
        return False

    def close(self):
        if self.is_open():
            self.drv.click(*GUIDE_CLOSE)
            self.drv.sleep(0.8)

    def select(self, spot):
        """노드를 누르고 오른쪽 권장 전투력이 맞는지 확인."""
        self.drv.click(*spot["node"])
        self.drv.sleep(1.0)
        rec = read_number(self.drv.shot(), GUIDE_REC_BOX)
        if rec != spot["power"]:
            log.warning("길라잡이: '%s' 선택 확인 실패 (권장 %s, 기대 %s)", spot["name"], rec, spot["power"])
            return False
        return True

    def travel(self, spot):
        if not self.open():
            log.warning("길라잡이 화면이 열리지 않음")
            return False
        if not self.select(spot):
            self.close()
            return False
        log.info("길라잡이 바로가기(무료 순간이동): %s (권장 %s)", spot["name"], f"{spot['power']:,}")
        self.drv.click(*GUIDE_GO)
        self.drv.sleep(8.0)                      # 로딩
        return True

    def claim_rewards(self, power):
        """권장 전투력을 넘긴 사냥터들의 '보상 받기'를 누른다 (이미 받은 곳은 버튼이 회색)."""
        if not self.open():
            return 0
        n = 0
        for spot in guide_spots():
            if spot["power"] > power:
                break
            if not self.select(spot):
                continue
            px = _crop(self.drv.shot(), (1260, 775, 1420, 805)).reshape(-1, 3).mean(0)
            if px[0] - px[2] > 25:               # 청록 버튼 = 받을 보상 있음 (회색 = 수령 완료)
                log.info("길라잡이 보상 받기: %s", spot["name"])
                self.drv.click(*GUIDE_CLAIM)
                self.drv.sleep(1.5)
                n += 1
        self.close()
        return n
