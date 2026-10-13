"""화첩 / 유물 수집: 수집품을 떨어뜨리는 몬스터를 찾아 사냥하고, 모은 것은 주기적으로 등록한다.

화첩(☰ → 화첩): 책 6권 72그림, 그림마다 조각 9개. 조각 → 획득처 → '요귀'면 떨어뜨리는 몬스터 목록
               (이름 / 지역 / 전투력 배지, 발자국 버튼 = 그 몬스터 위치로 무료 자동 이동).
유물(☰ → 유물 수집): 성물/전리품 탭, 같은 아이템을 01~05단계로 1~5개 요구. 아이콘 → 핀 → 획득처 → 요귀.

획득처에 '요귀'가 없는 것(퀘스트·콘텐츠 전용)은 건너뛴다.
수집 대상: 몬스터 전투력 ≤ 내 전투력 × max_ratio(기본 2배), 약한 몬스터부터.
DB: collect_db.json (스캔 결과). 좌표는 1920x1080 기준.
"""
import json
import logging
import os
import re
import time
from collections import Counter

import cv2
import numpy as np

from .farm import StopRequested, _binarize, _crop, _ocr, clean_power, parse_k

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_DIR = os.path.join(ROOT, "templates")

MENU = (1870, 44)
MENU_ALBUM = (1380, 558)
MENU_RELIC = (1473, 558)
MENU_DOT = {"album": (1405, 518, 1435, 545), "relic": (1498, 518, 1528, 545)}   # 메뉴 아이콘 우상단 빨간 점
EXIT = (1872, 45)

BOOKS = ["인물화", "동물화", "곤충화", "사물화", "도깨비화", "요귀화"]
BOOK_COVER = [(285, 345), (640, 345), (995, 345), (390, 795), (745, 795), (1100, 795)]   # 화첩 첫 화면 표지
BOOK_DOT = [(405, 132, 445, 168), (760, 132, 800, 168), (1115, 132, 1155, 168),       # 표지 우상단 빨간 마름모
            (505, 580, 545, 616), (862, 580, 902, 616), (1218, 580, 1258, 616)]
BOOK_TAB_Y = [307, 393, 480, 567, 654, 741]       # 책 안 왼쪽 탭 (x=75)
GRID_FIRST = (480, 380)                            # 책 안 첫 그림 (사물화처럼 그림이 4개뿐이면 목록이 가운데에서 시작해 x=380은 카드 밖)
NEXT = (1005, 567)                                 # 그림 화면 '>' (마지막 그림에서는 그대로)
TITLE_BOX = (450, 150, 870, 195)
GRID_ORIGIN, GRID_CELL = (405, 310), 172
PIECE_ROW = (1230, 600)                            # 오른쪽 등록효과 목록의 조각 행 (완성효과 줄 수 3~5 모두 맞음)
REGISTER_ALL = (1488, 945)
PILL_BOX = (1400, 430, 1580, 1000)                 # '등록가능' 금색 표시를 찾는 영역

POPUP_SRC_TAB = (918, 442)                         # 아이템 창 '획득처' 탭
SRC_ROWS = [(960, 497), (960, 551)]                # 획득처 행 ('요귀' / '콘텐츠' …)
SRC_ROW_BOX = [(812, 478, 1100, 516), (812, 532, 1100, 570)]
POPUP_CLOSE = (1098, 240)
DICT_CLOSE = (1245, 111)
YOGWI_CLOSE = (1658, 111)
YOGWI_PANEL = (1300, 150, 1680, 1000)              # 요귀 목록 (발자국 버튼으로 행을 찾음)

RELIC_TAB = {"성물": (120, 192), "전리품": (120, 265)}
RELIC_FILTER_TODO = (1308, 1027)                   # '미완료'
RELIC_LIST = (225, 85, 1450, 995)
RELIC_ICON_X = 1402
RELIC_PIN = (1570, 290)
RELIC_PANEL_CLOSE = (1848, 117)
RELIC_REGISTER = (1671, 1011)


def _tpl(name):
    p = os.path.join(TPL_DIR, name)
    img = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(p)
    return img


def _matches(img, tpl, box, thr=0.85):
    """box 안에서 tpl이 보이는 위치들 (왼쪽 위 좌표, 1920 기준), 위→아래, 겹친 것 제거."""
    x0, y0 = box[0], box[1]
    area = _crop(img, box)
    if area.shape[0] < tpl.shape[0] or area.shape[1] < tpl.shape[1]:
        return []
    r = cv2.matchTemplate(area, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(r >= thr)
    out = []
    for y, x in sorted(zip(ys.tolist(), xs.tolist())):
        if all(abs(y - oy) > tpl.shape[0] // 2 for _, oy in out):
            out.append((x, y))
    return [(x0 + x, y0 + y) for x, y in out]


def red_dot(img, box):
    """작은 영역에 빨간 알림 점이 있으면 True."""
    c = _crop(img, box).astype(int)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    return int(((r > 180) & (g < 90) & (b < 90)).sum()) >= 6


def has_register_pill(img):
    """그림 화면 오른쪽 목록에 금색 '등록가능' 표시가 있으면 True."""
    c = _crop(img, PILL_BOX).astype(int)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    return int(((r > 200) & (g > 150) & (b < 140)).sum()) >= 300


def painting_have(img):
    """그림 칸 중 등록된(그림이 보이는) 칸 수 (9칸 기준 환산). 빈 칸은 황토색(채도 ~40).
    그림마다 조각 수가 다르다(3x3=9, 2x2=4 …) → 3x3 표본 9곳으로 면적 비율을 재서 9칸 기준으로 돌려준다
    (9 = 완성)."""
    n = 0
    for r in range(3):
        for c in range(3):
            x0 = GRID_ORIGIN[0] + c * GRID_CELL + 40
            y0 = GRID_ORIGIN[1] + r * GRID_CELL + 40
            cell = _crop(img, (x0, y0, x0 + 90, y0 + 90)).reshape(-1, 3).astype(int)
            b, _, red = cell.mean(0)
            sat = (cell.max(1) - cell.min(1)).mean()
            tan = 28 < sat < 58 and 88 < b < 125 and 125 < red < 165
            n += 0 if tan else 1
    return n


def _gray_mask(crop, thr):
    c = crop.astype(int)
    mx, mn = c.max(axis=2), c.min(axis=2)
    m = ((mx > thr) & (mx - mn < 40)).astype(np.uint8)
    img = cv2.copyMakeBorder(255 - m * 255, 10, 10, 14, 14, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_GRAY2BGR)


def parse_badge(txt):
    """배지 글자 하나를 전투력으로. '15.5K' → 15500, '9,000'/'9000' → 9000. 못 읽으면 None."""
    vals = [clean_power(v) for v in parse_k(txt)]
    if vals:
        v = vals[-1]
        t = str(v // 1000)
        if v >= 1_000_000 and len(t) == 4 and t[0] == "1":     # 칼 아이콘이 '1'로 붙음 (170K → 1170K)
            v = int(t[1:]) * 1000
        return v
    d = re.sub(r"\D", "", txt)
    if len(d) == 4:
        return int(d)
    if len(d) == 5 and d[1] == "1":         # 쉼표가 1로 읽힘 (9,000 → 91000)
        return int(d[0] + d[2:])
    return None


def read_badge(img, cy):
    """몬스터 아이콘 아래 '⚔15.5K' 배지 → (전투력, 같은 값이 나온 횟수). 초상화 배경 때문에 오독이 잦아
    여러 위치/방식으로 읽어 최빈값을 쓴다 (2번 이상 같으면 바로 확정). 못 읽으면 (None, 0)."""
    votes = Counter()
    for x0 in (1331, 1334, 1337):
        cr = _crop(img, (x0, cy + 13, 1381, cy + 34))
        big = cv2.resize(cr, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for im, sc in ((_gray_mask(big, 225), 2), (cr, 5), (_gray_mask(big, 215), 2)):
            v = parse_badge(_ocr(im, sc))
            if v:
                votes[v] += 1
                if votes[v] >= 3:
                    return v, votes[v]
    if not votes:
        return None, 0
    v, n = votes.most_common(1)[0]
    return v, n


def name_is_red(img, cy):
    """몬스터 이름이 빨강(= 나보다 강함)이면 True."""
    c = _crop(img, (1378, cy - 30, 1560, cy - 8)).astype(int)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    return int(((r > 170) & (g < 120) & (b < 120)).sum()) >= 15


def _first_line(crop):
    """OCR 결과 중 맨 윗줄 (몬스터 이름; 아랫줄은 지역)."""
    from tools.ocr import read_text
    try:
        lines = read_text(crop, scale=2)
    except Exception:  # noqa: BLE001
        return ""
    if not lines:
        return ""
    top = min(l["y"] for l in lines)
    return " ".join(l["text"] for l in sorted(lines, key=lambda l: l["x"]) if l["y"] - top < 8).strip()


def read_monsters(img):
    """요귀 목록 패널의 몬스터들: [{name, region, power, red, cy}] (위→아래)."""
    walk = _tpl("collect_walk.png")
    out = []
    for _, y in _matches(img, walk, YOGWI_PANEL):
        cy = y + 20
        name = _first_line(_crop(img, (1378, cy - 32, 1560, cy + 14)))      # 이름 앞에 지역명이 붙어 있음 ('연못 흑사슴')
        power, conf = read_badge(img, cy)
        out.append({"name": name, "region": "", "power": power, "conf": conf,
                    "red": name_is_red(img, cy), "cy": cy})
    return out


def usable_power(m, power, max_ratio):
    """수집 대상으로 쓸 전투력 (정렬용). 대상이 아니면 None.
    흰 이름 = 나보다 약함 확실 → 숫자를 잘못 읽었어도 대상 (숫자가 이상하면 내 전투력으로 둠).
    빨간 이름 = 나보다 강함 → 2번 이상 같게 읽혔고 '내 전투력 < 값 ≤ 상한'일 때만 대상."""
    p = m.get("power")
    if not m.get("red"):
        return p if p and p <= power else power
    if p and m.get("conf", 0) >= 2 and power < p <= power * max_ratio:
        return p
    return None


def _similar(a, b):
    """OCR로 읽은 이름 비교 (공백 무시, 글자 2/3 이상 같으면 같은 것으로)."""
    a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same >= max(len(a), len(b)) * 2 / 3


def _page_changed(a, b):
    """그림 화면이 다른 그림으로 바뀌었으면 True. 빈 그림끼리는 그림 영역이 거의 같으므로
    제목 글자 영역(작아서 글자가 바뀌면 차이가 큼) 또는 설명 글 영역으로 판단한다."""
    def diff(box):
        return float(np.mean(cv2.absdiff(_crop(a, box), _crop(b, box))))
    # 제목 줄은 시스템 공지(상단 배너)가 겹쳐 오판이 잦으니 쓰지 않는다: 설명 글 + 그림 영역만
    return diff((300, 860, 1020, 920)) > 2.5 or diff((405, 310, 920, 825)) > 6.0


def _same_title(a, b):
    """그림 제목이 같은지 (OCR 흔들림 한 글자까지 허용; '쥐'/'박쥐'처럼 길이가 다르면 다른 그림)."""
    a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
    if a == b:
        return True
    return len(a) == len(b) >= 3 and sum(1 for x, y in zip(a, b) if x != y) <= 1


def load_db(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"scanned_at": 0, "album": [], "relics": []}


def save_db(db, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def candidates(db, power, max_ratio=2.0, banned=()):
    """사냥 후보 몬스터: [(power, -모을것수, monster, via)] 약한 순.
    via = 그 몬스터 목록을 다시 여는 경로 (화첩 그림 / 유물 아이템)."""
    if not power:
        return []
    mons = {}
    entries = [("album", e) for e in db.get("album", []) if not e.get("done")]
    entries += [("relic", e) for e in db.get("relics", []) if not e.get("done")]
    for kind, e in entries:
        for m in e.get("monsters", []):
            p = usable_power(m, power, max_ratio)
            key = (m["name"], m.get("region", ""))
            if p is None or not m["name"] or key in banned:
                continue
            slot = mons.setdefault(key, {"monster": m, "via": (kind, e), "count": 0, "p": p})
            slot["count"] += 1
    out = [(s["p"], -s["count"], s["monster"], s["via"]) for s in mons.values()]
    # 내 전투력 이하(안전)를 먼저, 그 안에서는 더 많은 수집품에 필요한 몬스터 → 센 몬스터 순.
    # 그다음 내 전투력 초과 ~ 상한 이하는 약한 순.
    out.sort(key=lambda t: (t[0] > power, t[1], -t[0]) if t[0] <= power else (True, t[0], t[1]))
    return out


class Collector:
    """게임 화면 조작 (drv: farm.Driver 계열 — shot/click/drag/key/sleep)."""

    def __init__(self, drv):
        self.drv = drv

    # ---------- 공통 ----------
    def _shot(self):
        return self.drv.shot()

    def reset_ui(self):
        """중단 등으로 남은 창을 닫고 필드로 돌아간다 (아이템 사전·요귀 목록 → 아이템 창 → 화첩/유물 화면 → 메뉴)."""
        d = self.drv
        for _ in range(6):
            img = d.shot()
            if "아이템" in _ocr(_crop(img, (600, 90, 1000, 130)), 2):
                d.click(*YOGWI_CLOSE)
                d.sleep(1.0)
                d.click(*DICT_CLOSE)
                d.sleep(1.2)
            elif "사용처" in _ocr(_crop(img, (800, 425, 1060, 462)), 2):
                d.click(*POPUP_CLOSE)
                d.sleep(1.0)
            elif _matches(img, _tpl("collect_exit.png"), (1830, 10, 1910, 80), 0.85):     # 화첩/유물 등 전체 화면의 나가기
                d.click(*EXIT)
                d.sleep(1.5)
            elif self.menu_open(img):
                d.click(*MENU)                  # 메뉴가 열려 있으면 같은 자리가 닫기 X
                d.sleep(1.2)
            else:
                return True
        return False

    def menu_open(self, img):
        return bool(_matches(img, _tpl("collect_menu.png"), (1300, 340, 1520, 440), 0.8))

    def open_menu_item(self, which):
        """☰ 메뉴에서 화첩/유물 수집을 연다 (메뉴가 이미 열려 있으면 ☰을 누르지 않음 — 같은 자리가 닫기 X). 빨간 점 여부를 돌려준다."""
        d = self.drv
        shot = d.shot()
        if not self.menu_open(shot):
            d.click(*MENU)
            d.sleep(1.3)
            shot = d.shot()
            if not self.menu_open(shot):
                raise RuntimeError("메뉴가 열리지 않음")
        dot = red_dot(shot, MENU_DOT[which])
        d.click(*(MENU_ALBUM if which == "album" else MENU_RELIC))
        d.sleep(2.0)
        return dot

    def exit_screen(self):
        self.drv.click(*EXIT)
        self.drv.sleep(1.5)

    def _open_sources(self):
        """아이템 창이 떠 있는 상태: 획득처 탭에서 '요귀' 행을 눌러 몬스터 목록을 연다. 요귀가 없으면 False."""
        d = self.drv
        d.click(*POPUP_SRC_TAB)
        d.sleep(1.0)
        shot = d.shot()
        for (x, y), box in zip(SRC_ROWS, SRC_ROW_BOX):
            if "요귀" in _ocr(_crop(shot, box), 2):
                d.click(x, y)
                d.sleep(1.8)
                return True
        return False

    def _close_sources(self, opened=True):
        """요귀 목록·아이템 사전·아이템 창을 닫는다. 창이 늦게 닫히면 클릭이 씹히므로
        닫힐 때까지 화면을 확인하며 최대 5번 시도한다."""
        d = self.drv
        for _ in range(5):
            d.sleep(0.6)
            img = d.shot()
            if "아이템" in _ocr(_crop(img, (600, 90, 1000, 130)), 2):
                d.click(*YOGWI_CLOSE)
                d.sleep(1.0)
                d.click(*DICT_CLOSE)
                d.sleep(1.2)
            elif "사용처" in _ocr(_crop(img, (800, 425, 1060, 462)), 2):
                d.click(*POPUP_CLOSE)
                d.sleep(1.0)
            else:
                return True
        log.warning("수집: 아이템 창이 닫히지 않음")
        return False

    # ---------- 화첩 ----------
    def open_book(self, i):
        """화첩 화면(표지)에서 i번째 책을 열고 첫 그림으로."""
        d = self.drv
        d.click(*BOOK_COVER[i])
        d.sleep(1.5)
        d.click(*GRID_FIRST)
        d.sleep(1.5)

    def switch_book(self, i):
        """그림 화면에서 왼쪽 탭으로 다른 책 → 첫 그림."""
        d = self.drv
        d.click(75, BOOK_TAB_Y[i])
        d.sleep(1.5)
        d.click(*GRID_FIRST)
        d.sleep(1.5)

    def title(self, img):
        return _ocr(_crop(img, TITLE_BOX), 2).strip()

    def next_painting(self, prev_title, prev_img=None):
        """'>' → 새 제목. 마지막 그림이면 None. 넘어갔는지는 제목+그림 영역 이미지가 바뀌었는지로 본다
        (OCR 흔들림에 안전). 클릭이 화면 전환 중에 씹힐 수 있어 안 바뀌면 3번까지 다시 눌러 본다."""
        prev_img = prev_img if prev_img is not None else self.drv.shot()
        img = prev_img
        for wait in (1.0, 1.5, 2.0):
            self.drv.click(*NEXT)
            self.drv.sleep(wait)
            img = self.settled()
            if _page_changed(prev_img, img):
                return self.title(img) or "?", img
        return None, img

    def confirmed_have(self, img):
        """등록 칸 수. 9/9(완성)이면 화면 전환 중 오판일 수 있어 잠시 뒤 다시 읽어 낮은 쪽을 쓴다."""
        have = painting_have(img)
        if have >= 9:
            self.drv.sleep(1.2)
            have = min(have, painting_have(self.settled()))
        return have

    def settled(self, tries=4):
        """화면 전환이 끝날 때까지(연속 두 장이 거의 같을 때까지) 기다린 화면."""
        prev = self.drv.shot()
        for _ in range(tries):
            self.drv.sleep(0.4)
            cur = self.drv.shot()
            if float(np.mean(cv2.absdiff(cur, prev))) < 2.0:
                return cur
            prev = cur
        return prev

    def painting_monsters(self, retry=False):
        """현재 그림의 조각 → 획득처 → 요귀 목록. 요귀 획득처가 없으면 []. (창은 닫고 돌아온다)
        retry=True면 못 열었을 때 한 번 더 (클릭이 씹혀도 몬스터가 있는 그림을 놓치지 않게)."""
        d = self.drv
        for attempt in range(2 if retry else 1):
            d.click(*PIECE_ROW)
            d.sleep(1.2 + attempt)
            opened = self._open_sources()
            mons = read_monsters(d.shot()) if opened else []
            self._close_sources(opened)
            if opened:
                break
        return opened, mons

    def scan_album(self, max_minutes=20, books=None):
        """화첩 스캔 → [{book, idx, name, have, done, has_yogwi, monsters}]. books=[책 번호…]면 그 책만."""
        out = []
        end = time.monotonic() + max_minutes * 60
        self.reset_ui()
        self.open_menu_item("album")
        first = True
        try:
            for b in (books if books is not None else range(len(BOOKS))):
                if first:
                    self.open_book(b)
                    first = False
                else:
                    self.switch_book(b)
                img = self.settled()
                name = self.title(img) or "?"          # '솥'처럼 한 글자 제목은 못 읽을 수 있음 → 그래도 스캔
                idx = 0
                while name and time.monotonic() < end:
                    have = self.confirmed_have(img)
                    entry = {"book": b, "idx": idx, "name": name, "have": have, "done": have >= 9,
                             "has_yogwi": False, "monsters": []}
                    if not entry["done"]:
                        entry["has_yogwi"], entry["monsters"] = self.painting_monsters(retry=(b != 0))
                    log.info("화첩 %s #%d '%s' %d/9 · 몬스터 %s", BOOKS[b], idx, name, have,
                             ", ".join(f"{m['name']}({(m['power'] or 0) // 1000}K)" for m in entry["monsters"]) or "-")
                    out.append(entry)
                    if idx >= 30:                    # 한 책에 이렇게 많을 수 없음 (끝 판정 실패 방지)
                        break
                    name, img = self.next_painting(name, img)
                    idx += 1
        finally:
            self.exit_screen()
        return out

    def rescan_done(self, db, save=None):
        """DB에서 완성(9/9)으로 기록됐지만 몬스터가 비어 있는 그림만 다시 확인. 고친 개수.
        save()를 주면 그림 하나를 고칠 때마다 호출해 중간에 실패해도 결과가 남게 한다."""
        todo = [e for e in db.get("album", []) if not e.get("monsters")
                and (e.get("done") or (e["book"] != 0 and not e.get("has_yogwi")))]
        fixed = 0
        for e in todo:
            if not self.goto_painting(e["book"], e["idx"], None):
                self.reset_ui()
                continue
            img = self.settled()
            have = self.confirmed_have(img)
            if have < 9:
                e["have"], e["done"] = have, False
                e["has_yogwi"], e["monsters"] = self.painting_monsters(retry=True)
                log.info("화첩 재확인 %s #%d '%s' %d/9 · 몬스터 %s", BOOKS[e["book"]], e["idx"], e["name"], have,
                         ", ".join(f"{m['name']}({(m['power'] or 0) // 1000}K)" for m in e["monsters"]) or "-")
                fixed += 1
                if save:
                    save()
            self.exit_screen()
        return fixed

    def goto_painting(self, book, idx, name=None):
        """화첩을 열어 book권 idx번째 그림으로. 성공하면 True."""
        self.open_menu_item("album")
        self.open_book(book)
        t = None
        for _ in range(idx):
            t, _ = self.next_painting(t or "")
            if t is None:
                return False
        if name:
            t = self.title(self.drv.shot())
            if not _similar(t, name):
                log.warning("수집: 그림 제목이 다름 ('%s' ≠ '%s')", t, name)
                return False
        return True

    # ---------- 유물 ----------
    def relic_rows(self, img):
        """유물 목록에 보이는 행들: [(y, 이름, 단계)]. 별 아이콘으로 행을 찾는다."""
        star = _tpl("collect_star.png")
        rows = []
        for _, y in _matches(img, star, (225, 85, 300, 995), 0.7):
            cy = y + 13
            txt = _ocr(_crop(img, (272, cy - 36, 720, cy - 3)), 3).strip()
            m = re.match(r"(.+?)\s*(\d{2})$", txt)
            if m:
                rows.append((cy, m.group(1).strip(), int(m.group(2))))
            elif txt:
                rows.append((cy, txt, 1))
        return rows

    def relic_monsters(self, cy):
        """유물 행 cy의 아이콘 → 핀 → 요귀 목록. (창은 닫고 돌아온다)"""
        d = self.drv
        d.click(RELIC_ICON_X, cy)
        d.sleep(1.2)
        d.click(*RELIC_PIN)
        d.sleep(1.2)
        opened = self._open_sources()
        mons = read_monsters(d.shot()) if opened else []
        self._close_sources(opened)
        d.click(*RELIC_PANEL_CLOSE)
        d.sleep(0.8)
        return opened, mons

    def _scroll_relics(self):
        self.drv.drag(800, 900, 800, 300)
        self.drv.sleep(1.8)

    def scan_relics(self, max_items=150, max_minutes=25, save=None):
        """미완료 유물(성물·전리품)을 아이템 이름별로 한 번씩 스캔 → [{tab, name, level, has_yogwi, monsters}]."""
        out, seen = [], set()
        end = time.monotonic() + max_minutes * 60
        self.reset_ui()
        self.open_menu_item("relic")
        try:
            for tab, xy in RELIC_TAB.items():
                self.drv.click(*xy)
                self.drv.sleep(1.2)
                self.drv.click(*RELIC_FILTER_TODO)
                self.drv.sleep(1.5)
                stale = 0
                while stale < 2 and len(out) < max_items and time.monotonic() < end:
                    rows = self.relic_rows(self.drv.shot())
                    new = 0
                    for cy, name, level in rows:
                        if any(_similar(name, s) for s in seen):
                            continue
                        seen.add(name)
                        new += 1
                        has, mons = self.relic_monsters(cy)
                        log.info("유물 %s '%s' %02d · 몬스터 %s", tab, name, level,
                                 ", ".join(f"{m['name']}({(m['power'] or 0) // 1000}K)" for m in mons) or "-")
                        out.append({"tab": tab, "name": name, "level": level, "done": False,
                                    "has_yogwi": has, "monsters": mons})
                        if save:
                            save(out)
                    stale = stale + 1 if new == 0 else 0
                    self._scroll_relics()
        finally:
            self.exit_screen()
        return out

    def goto_relic(self, tab, name, max_pages=40):
        self.open_menu_item("relic")
        self.drv.click(*RELIC_TAB[tab])
        self.drv.sleep(1.2)
        self.drv.click(*RELIC_FILTER_TODO)
        self.drv.sleep(1.5)
        for _ in range(max_pages):
            for cy, n, _ in self.relic_rows(self.drv.shot()):
                if _similar(n, name):
                    self.drv.click(RELIC_ICON_X, cy)
                    self.drv.sleep(1.2)
                    self.drv.click(*RELIC_PIN)
                    self.drv.sleep(1.2)
                    return True
            self._scroll_relics()
        return False

    # ---------- 이동 ----------
    def walk_to(self, monster, via):
        """via(화첩 그림/유물)의 요귀 목록에서 monster 행의 발자국(무료 자동 이동)을 누른다. 성공하면 True."""
        kind, e = via
        self.reset_ui()
        try:
            if kind == "album":
                nm = e.get("name") or ""
                if not (2 <= len(nm) <= 8 and " " not in nm and "?" not in nm):
                    nm = None                       # 제목을 못 읽었거나 공지가 섞인 그림은 제목 검증 생략
                if not self.goto_painting(e["book"], e["idx"], nm):
                    self.exit_screen()
                    return False
                self.drv.click(*PIECE_ROW)
                self.drv.sleep(1.2)
            else:
                if not self.goto_relic(e["tab"], e["name"]):
                    self.exit_screen()
                    return False
            if not self._open_sources():
                self._close_sources(False)
                self.exit_screen()
                return False
            for m in read_monsters(self.drv.shot()):
                if _similar(m["name"], monster["name"]):
                    self.drv.click(1636, m["cy"])       # 발자국 → 화면이 닫히고 자동 이동 시작
                    self.drv.sleep(2.0)
                    return True
            log.warning("수집: 목록에서 '%s'를 찾지 못함", monster["name"])
            self._close_sources(True)
            self.exit_screen()
            return False
        except StopRequested:
            raise

    # ---------- 등록 ----------
    def register_album(self, db=None):
        """화첩 표지 6권 중 우상단 빨간 점(등록할 퍼즐 있음)이 있는 책만 열어, 그림을 '>'로 넘기며
        '등록가능'인 그림에서 '모두 등록'. 돌면서 DB의 등록 수도 갱신. 등록한 그림 수."""
        d = self.drv
        n = 0
        self.reset_ui()
        if not self.open_menu_item("album"):
            self.exit_screen()
            return 0
        try:
            covers = d.shot()
            books = [b for b, (x, y) in enumerate(BOOK_COVER) if red_dot(covers, BOOK_DOT[b])]
            log.info("화첩: 등록할 것이 있는 책 %s", ", ".join(BOOKS[b] for b in books) or "없음")
            for k, b in enumerate(books):
                if k == 0:
                    self.open_book(b)
                else:
                    self.switch_book(b)
                img = d.shot()
                name, idx = self.title(img), 0
                while name:
                    if has_register_pill(img):
                        d.click(*REGISTER_ALL)
                        d.sleep(1.5)
                        d.click(960, 540)            # 등록 연출 닫기
                        d.sleep(1.0)
                        n += 1
                        log.info("화첩 '%s' 조각 등록", name)
                        img = d.shot()
                    if db is not None:
                        for e in db.get("album", []):
                            if e["book"] == b and e["idx"] == idx:
                                e["have"] = painting_have(img)
                                e["done"] = e["have"] >= 9
                    name, img = self.next_painting(name, img)
                    idx += 1
        finally:
            self.exit_screen()
        return n

    def register_relics(self):
        """유물 메뉴에 빨간 점이 있으면 미완료 목록에서 빨간 점 붙은 아이콘을 눌러 '등록' (미검증)."""
        d = self.drv
        self.reset_ui()
        if not self.open_menu_item("relic"):
            self.exit_screen()
            return 0
        n = 0
        try:
            for xy in RELIC_TAB.values():
                d.click(*xy)
                d.sleep(1.2)
                d.click(*RELIC_FILTER_TODO)
                d.sleep(1.5)
                for _ in range(30):
                    img = d.shot()
                    rows = self.relic_rows(img)
                    hit = [cy for cy, _, _ in rows if red_dot(img, (RELIC_ICON_X + 15, cy - 40, RELIC_ICON_X + 45, cy - 15))]
                    for cy in hit:
                        d.click(RELIC_ICON_X, cy)
                        d.sleep(1.2)
                        d.click(*RELIC_REGISTER)
                        d.sleep(1.5)
                        d.click(*RELIC_PANEL_CLOSE)
                        d.sleep(0.8)
                        n += 1
                    before = [r[1] for r in rows]
                    self._scroll_relics()
                    if [r[1] for r in self.relic_rows(d.shot())] == before:
                        break
        finally:
            self.exit_screen()
        if n:
            log.info("유물 %d개 등록", n)
        return n


class CollectTask:
    """Bot에 붙는 수집 작업: 앵벌 시간에 수집 몬스터 사냥 + 주기적 등록."""

    def __init__(self, bot, cfg):
        from .farm import BotDriver
        self.bot = bot
        self.cfg = cfg or {}
        self.path = os.path.join(ROOT, self.cfg.get("db_file", "collect_db.json"))
        self.db = load_db(self.path)
        self.col = Collector(BotDriver(bot))
        self.max_ratio = float(self.cfg.get("max_ratio", 2.0))
        self.scan_secs = float(self.cfg.get("scan_hours", 24)) * 3600
        self.register_secs = float(self.cfg.get("register_minutes", 60)) * 60
        self.ban_secs = float(self.cfg.get("death_ban_minutes", 60)) * 60
        self.next_register = time.monotonic() + float(self.cfg.get("first_register_delay", 300))
        self.banned = {}            # (이름, 지역) → 해제 시각
        self.current = None         # 지금 사냥 중인 수집 몬스터
        self.status = "대기"

    def describe(self):
        cur = self.current
        return f"{self.status}" + (f" · {cur['name']}({(cur.get('power') or 0) // 1000}K)" if cur else "")

    def _bans(self, now):
        self.banned = {k: t for k, t in self.banned.items() if t > now}
        return set(self.banned)

    def need_scan(self):
        return not (self.db.get("album") or self.db.get("relics")) or \
            time.time() - self.db.get("scanned_at", 0) > self.scan_secs

    def scan(self):
        log.info("수집 스캔 시작 (화첩 → 유물, 십여 분 걸림)")
        self.status = "스캔 중"
        db = {"scanned_at": time.time(), "album": self.db.get("album", []), "relics": self.db.get("relics", [])}
        if self.cfg.get("album", True):
            db["album"] = self.col.scan_album()
            save_db(db, self.path)
        if self.cfg.get("relics", True):
            db["relics"] = self.col.scan_relics(int(self.cfg.get("relic_max_items", 150)))
        db["scanned_at"] = time.time()
        save_db(db, self.path)
        self.db = db
        log.info("수집 스캔 끝: 화첩 %d그림(미완성 %d), 유물 %d종",
                 len(db["album"]), sum(1 for e in db["album"] if not e.get("done")), len(db["relics"]))

    def go(self, power, now):
        """앵벌 시작: 수집 몬스터를 골라 걸어간다. 갈 곳이 없거나 실패하면 False (→ 일반 사냥터)."""
        if not power:
            log.warning("수집: 전투력을 몰라 수집 사냥을 건너뜀")
            return False
        try:
            if self.need_scan():
                self.scan()
            for p, negc, m, via in candidates(self.db, power, self.max_ratio, self._bans(now))[:3]:
                what = via[1].get("name")
                log.info("수집 사냥: '%s'(%s, %dK) ← %s '%s' 외 %d건 (전투력 %s, 상한 %d%%)", m["name"], m.get("region"),
                         p // 1000, "화첩" if via[0] == "album" else "유물", what, -negc - 1,
                         f"{power:,}", self.max_ratio * 100)
                if self.col.walk_to(m, via):
                    self.current = m
                    self.status = "수집 사냥 중"
                    return True
                self.banned[(m["name"], m.get("region", ""))] = now + 600
            self.status = "수집 대상 없음"
            log.info("수집 사냥할 몬스터 없음 (전투력 %s × %.1f 이하) → 일반 사냥터", f"{power:,}", self.max_ratio)
        except StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("수집 사냥 이동 실패: %s", e)
        self.current = None
        return False

    def on_death(self, now):
        if self.current:
            self.banned[(self.current["name"], self.current.get("region", ""))] = now + self.ban_secs
            log.warning("수집 몬스터 '%s'에서 사망 → %d분 제외", self.current["name"], self.ban_secs // 60)
        self.current = None

    def stop(self):
        self.current = None
        self.status = "대기"

    def register_due(self, now):
        return now >= self.next_register

    def register(self, now):
        self.next_register = now + self.register_secs
        n1 = n2 = 0
        try:
            if self.cfg.get("album", True):
                n1 = self.col.register_album(self.db)
                save_db(self.db, self.path)
            if self.cfg.get("relics", True):
                n2 = self.col.register_relics()
        except StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("수집 등록 실패: %s", e)
        log.info("수집 등록 끝: 화첩 %d그림, 유물 %d개", n1, n2)
        return n1 + n2
