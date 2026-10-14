"""아이템 사전(메뉴 → 아이템 사전)의 '수집 재료'·'화첩 조각' 획득처 수집.

아이템마다 [획득처] 탭의 줄(요귀 / 콘텐츠 / 상점 …)을 읽고, '요귀'가 있으면 눌러서 몬스터 목록(이름·지역·전투력·구분)을 읽어
item_dict.json 에 저장한다. 나중에 사냥 추천·수집 계획의 자료로 쓴다. 읽기만 하고 아무것도 쓰거나 사지 않는다.

화면 구조 (1920x1080 기준, 실측):
  요귀 패널이 닫혀 있으면 사전 창이 가운데(왼쪽 탭 x≈532), 열리면 왼쪽으로 밀려 있다(탭 x≈320, 패널 x 1290~1690).
  다른 아이템을 고르면 패널은 닫히고 탭은 '상세 정보'로 돌아간다 → 아이템마다 [획득처] 탭을 다시 눌러야 한다.
"""
import json
import logging
import os
import re
import time

from .collect import (YOGWI_CLOSE, YOGWI_PANEL, _matches, _similar, _tpl, read_badge)
from .farm import ROOT, _crop

log = logging.getLogger("bot2")

DB_FILE = os.path.join(ROOT, "item_dict.json")

CATEGORY_TAB = {"수집 재료": None, "화첩 조각": None}                # 카테고리 이름 목록 (탭 위치는 글자로 찾는다)
LIST_BOX = (640, 215, 1100, 985)                    # 아이템 목록
LIST_CLICK_X = 870
SRC_TAB = (1294, 535)                               # [획득처]
SRC_BOX = (1130, 560, 1470, 900)                    # 획득처 줄들
SRC_ROW_Y0, SRC_ROW_DY = 591, 54                    # 첫 줄 중심 y, 줄 간격 (실측: 첫 줄 591)
SCROLL = (870, 880, 870, 480)                       # 목록을 한 번에 약 4.6줄 올림
PANEL_SCROLL = (1480, 900, 1480, 420)
HEADERS = ("추천요귀", "획득가능한요귀", "획득한요귀")

_PW = re.compile(r"(\d{1,3}(?:\.\d)?)\s*([KkMm])")


def load(path=DB_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"updated": 0, "items": [], "monsters": {}}


def save(db, path=DB_FILE):
    db["updated"] = time.time()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _lines(img, box, scale=2):
    """box 영역의 OCR 줄 [{text, x, cy}] (좌표는 1920 기준 절대값). OCR 실패 시 []."""
    from tools.ocr import read_text
    crop = _crop(img, box)
    try:
        ls = read_text(crop, scale=scale)
    except Exception as e:                           # noqa: BLE001
        log.warning("아이템 사전: OCR 실패 %s", e)
        return []
    return [{"text": l["text"].strip(), "x": box[0] + l["x"], "cy": box[1] + l["y"] + l["h"] / 2} for l in ls if l["text"].strip()]


def parse_power(text):
    """'⚔94.5K' 같은 글자에서 전투력 숫자. 못 읽으면 None."""
    m = _PW.search(text or "")
    if not m:
        return None
    v = float(m.group(1)) * (1000 if m.group(2) in "Kk" else 1_000_000)
    return int(round(v))


class ItemDictScanner:
    """drv: farm.Driver 계열 (shot/click/drag/key/sleep). 사전 창이 이미 열려 있어야 한다."""

    def __init__(self, drv, db=None, save_fn=None):
        self.drv = drv
        self.db = db if db is not None else load()
        self.save_fn = save_fn or (lambda: save(self.db))
        self.power_cache = {}
        for k, m in (self.db.get("monsters") or {}).items():
            if m.get("power"):
                self.power_cache[k] = m["power"]
        self._seed_from_collect_db()

    def _seed_from_collect_db(self):
        """수집 DB(화첩·유물 스캔)에서 이미 읽어 둔 몬스터 전투력을 가져와 느린 배지 읽기를 줄인다."""
        try:
            from .collect import load_db
            cdb = load_db(os.path.join(ROOT, "collect_db.json"))
        except Exception:                            # noqa: BLE001
            return
        for e in (cdb.get("album") or []) + (cdb.get("relics") or []):
            for m in e.get("monsters") or []:
                if m.get("name") and m.get("power") and m["name"] not in self.power_cache:
                    self.power_cache["name:" + m["name"]] = m["power"]

    # ---------- 화면 상태 ----------
    def panel_open(self, img):
        return bool(_matches(img, _tpl("collect_walk.png"), YOGWI_PANEL)) or \
            any("요귀" == l["text"].replace(" ", "") for l in _lines(img, (1400, 85, 1580, 135)))

    def dict_open(self, img):
        return any("아이템" in l["text"] for l in _lines(img, (560, 85, 1360, 135)))

    def close_panel(self):
        d = self.drv
        for _ in range(3):
            if not self.panel_open(d.shot()):
                return True
            d.click(*YOGWI_CLOSE)
            d.sleep(1.0)
        return False

    # ---------- 아이템 목록 ----------
    def list_rows(self, img):
        """현재 보이는 아이템 줄: [(이름, 중심 y)] 위→아래."""
        out = []
        for l in _lines(img, LIST_BOX):
            if l["x"] < 740 or len(l["text"]) < 2:        # 이름 글자는 아이콘 오른쪽(x≈765~)에서 시작
                continue
            out.append((l["text"], int(l["cy"])))
        return sorted(out, key=lambda r: r[1])

    @staticmethod
    def merge_pages(seen, page_names):
        """이미 본 이름 목록 seen 뒤에 이번 페이지를 겹치는 부분을 빼고 이어 붙인다. 새로 붙인 이름 수를 돌려준다."""
        best = 0
        for k in range(min(len(seen), len(page_names)), 0, -1):
            if all(_similar(a, b) for a, b in zip(seen[-k:], page_names[:k])):
                best = k
                break
        new = page_names[best:]
        seen.extend(new)
        return len(new)

    # ---------- 획득처 ----------
    def read_sources(self):
        """[획득처] 탭을 열어 줄들의 글자를 읽는다 → [(글자, 클릭 y)]."""
        d = self.drv
        d.click(*SRC_TAB)
        d.sleep(0.9)
        rows = [(l["text"], int(l["cy"])) for l in _lines(d.shot(), SRC_BOX) if l["x"] < 1300 and len(l["text"]) >= 2]
        return sorted(rows, key=lambda r: r[1])

    def read_monsters(self):
        """열려 있는 요귀 패널의 몬스터: [{section, name, region, power}] — 스크롤해서 끝까지."""
        d = self.drv
        walk = _tpl("collect_walk.png")
        out, seen = [], set()
        for page in range(5):
            img = d.shot()
            lines = _lines(img, YOGWI_PANEL, 3)
            heads = sorted([(l["cy"], re.sub(r"\s", "", l["text"])) for l in lines
                            if any(_similar(re.sub(r"\s", "", l["text"]), h) for h in HEADERS) and 1380 < l["x"] + 200 < 1700], key=lambda t: t[0])
            new = 0
            for _x, y in _matches(img, walk, YOGWI_PANEL):
                cy = y + 20
                name, region = self._name_region(img, lines, cy)
                key = (name, region)
                if not name or key in seen:
                    continue
                seen.add(key)
                new += 1
                section = ""
                for hy, ht in heads:
                    if hy < cy:
                        section = ht
                out.append({"section": section, "name": name, "region": region,
                            "power": self._power(img, lines, cy, name, region)})
            if new == 0 and page > 0:
                break
            if not _matches(img, walk, YOGWI_PANEL):
                break
            d.drag(*PANEL_SCROLL)
            d.sleep(1.2)
        return out

    @staticmethod
    def _clean(t):
        return re.sub(r"^[^가-힣A-Za-z0-9]+", "", t).strip()

    def _name_region(self, img, lines, cy):
        """한 줄 행의 (이름, 지역). 패널 전체 OCR에서 이름/지역 두 줄이 안 잡히면 그 행만 잘라 다시 읽는다."""
        near = sorted([l for l in lines if cy - 40 < l["cy"] < cy + 30 and 1355 < l["x"] < 1405], key=lambda l: l["cy"])
        if len(near) == 2 and all(re.search(r"[가-힣]{2}", l["text"]) for l in near):
            return self._clean(near[0]["text"]), self._clean(near[1]["text"])
        row = sorted(_lines(img, (1370, cy - 36, 1560, cy + 30), 3), key=lambda l: l["cy"])
        row = [l for l in row if re.search(r"[가-힣]{2}", l["text"])]
        if len(row) >= 2:
            return self._clean(row[0]["text"]), self._clean(row[1]["text"])
        if row:
            return self._clean(row[0]["text"]), ""
        return "", ""

    def known_powers(self):
        try:
            from .farm import GUIDE_NODES
            base = {p for _n, p, _xy in GUIDE_NODES}
        except Exception:                            # noqa: BLE001
            base = set()
        return base | set(self.power_cache.values())

    def _snap_power(self, text):
        """배지 글자(예: 'Y945K', 'x94.5K')를 아는 전투력 값에 맞춰 읽는다. 못 맞추면 None."""
        m = re.search(r"(\d{2,3})\s*([.:])?\s*(\d)?\s*[Kk]", text.replace(":", "."))
        if not m:
            return None
        known = self.known_powers()
        digits = m.group(1) + (m.group(3) or "")
        cands = [int(digits) * 1000, int(digits) * 100]
        if m.group(2) and m.group(3):
            cands = [int(round(float(m.group(1) + "." + m.group(3)) * 1000))]
        for c in cands:
            if any(abs(c - k) <= 60 for k in known):
                return c
        return None

    def _power(self, img, lines, cy, name, region):
        """전투력: 패널 OCR의 배지 글자를 아는 값에 맞춰 읽고, 안 되면 배지 투표(read_badge)에서 2표 이상 나온 값. 못 읽으면 None."""
        k = f"{name}|{region}"
        if k in self.power_cache:
            return self.power_cache[k]
        for l in lines:
            if l["x"] < 1355 and cy + 4 < l["cy"] < cy + 44:
                v = self._snap_power(l["text"])
                if v:
                    self.power_cache[k] = v
                    return v
        p, conf = read_badge(img, cy)               # 배지 투표는 틀린 값(6K 등)도 낼 수 있어서, 이미 아는 전투력과 맞을 때만 쓴다
        if p and conf >= 2 and any(abs(p - kp) <= 60 for kp in self.known_powers()):
            self.power_cache[k] = p
            return p
        return None                                  # 못 읽음 → finalize()가 다른 아이템에서 읽은 같은 몬스터 값으로 채운다

    # ---------- 한 아이템 ----------
    def scan_item(self, name, y):
        d = self.drv
        d.click(LIST_CLICK_X, y)
        d.sleep(1.0)
        header = self._clean(" ".join(l["text"] for l in _lines(d.shot(), (940, 215, 1260, 268), 3)))     # 상세 창 제목 (두 번째 의견)
        sources = self.read_sources()
        rec = {"name": name, "name_header": header, "sources": [s for s, _ in sources], "monsters": []}
        for text, sy in sources:
            if "요귀" in text.replace(" ", ""):
                d.click(1296, sy)
                d.sleep(1.8)
                rec["monsters"] = self.read_monsters()
                if not self.close_panel():
                    raise RuntimeError("요귀 패널이 닫히지 않음")
                break
        for m in rec["monsters"]:
            self.db.setdefault("monsters", {})[f"{m['name']}|{m['region']}"] = {"power": m["power"], "region": m["region"]}
        return rec

    def select_category(self, cat):
        """왼쪽 탭 목록(스크롤 위치에 따라 y가 달라짐)에서 글자로 찾아 누른다."""
        d = self.drv
        want = re.sub(r"\s", "", cat)
        hits = [l for l in _lines(d.shot(), (440, 140, 640, 1000), 2)           # '화첩'을 '화접'으로 읽는 식의 흔들림 허용
                if len(l["text"]) <= len(cat) + 3 and _similar(want, re.sub(r"\s", "", l["text"]))]
        if not hits:
            return False
        top = min(hits, key=lambda l: l["cy"])
        d.click(top["x"] + 60, top["cy"])
        d.sleep(1.5)
        return True

    def scroll_top(self, max_drags=40):
        """목록을 맨 위로 (이어서 스캔할 때 같은 순번으로 세려면 항상 맨 위에서 시작해야 한다)."""
        d = self.drv
        prev = None
        for _ in range(max_drags):
            rows = [n for n, _y in self.list_rows(d.shot())]
            if prev is not None and len(rows) == len(prev) and all(_similar(a, b) for a, b in zip(rows, prev)):
                return
            prev = rows
            d.drag(SCROLL[0], SCROLL[3], SCROLL[2], SCROLL[1])        # 아래로 끌어 위쪽 목록을 보여 줌
            d.sleep(0.8)

    # ---------- 카테고리 전체 ----------
    def scan_category(self, cat, max_items=400, max_minutes=60):
        d = self.drv
        end = time.monotonic() + max_minutes * 60
        if not self.close_panel():
            raise RuntimeError("요귀 패널을 닫지 못함")
        if not self.select_category(cat):
            raise RuntimeError(f"'{cat}' 탭을 찾지 못함")
        self.scroll_top()
        done = {(r["category"], r["key"]) for r in self.db["items"]}
        seen, stale, total, empty = [], 0, 0, 0
        while stale < 2 and total < max_items and time.monotonic() < end:
            rows = self.list_rows(d.shot())
            if not rows:                             # OCR가 순간적으로 실패했거나 화면이 바뀌는 중 → 끝으로 오인하지 않고 다시 읽는다
                empty += 1
                if empty > 4:
                    raise RuntimeError("아이템 목록을 읽지 못함")
                d.sleep(1.5)
                continue
            empty = 0
            new = self.merge_pages(seen, [n for n, _ in rows])
            fresh = rows[len(rows) - new:] if new else []
            base = len(seen) - new
            for i, (nm, y) in enumerate(fresh):
                key = str(base + i)                  # 목록 맨 위부터의 순번 (OCR 글자가 매번 조금 달라도 이어서 하기가 맞는다)
                if (cat, key) in done:
                    continue
                rec = self.scan_item(nm, y)
                rec.update(category=cat, key=key, scanned_at=time.time())
                self.db["items"].append(rec)
                done.add((cat, key))
                total += 1
                self.save_fn()
                log.info("[%s] %s → 획득처 %s · 몬스터 %d (%s)", cat, nm, ", ".join(rec["sources"]) or "-", len(rec["monsters"]),
                         ", ".join(f"{m['name']} {(m['power'] or 0) // 1000}K" for m in rec["monsters"][:4]))
            stale = stale + 1 if new == 0 else 0
            d.drag(*SCROLL)
            d.sleep(1.5)
        return total


def canonicalize(db):
    """비슷한 몬스터 이름(OCR 흔들림)을 가장 많이 나온 철자로 통일한다. 바꾼 이름 수를 돌려준다."""
    from collections import Counter
    cnt = Counter()
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            cnt[m["name"]] += 1
    canon = {}
    for name, _n in cnt.most_common():
        if name in canon:
            continue
        canon[name] = name
        for other in cnt:
            if other not in canon and _similar(name, other) and abs(len(name) - len(other)) <= 1:
                canon[other] = name
    changed = 0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if canon.get(m["name"], m["name"]) != m["name"]:
                m["name"] = canon[m["name"]]
                changed += 1
    return changed


def fill_powers(db):
    """전투력을 못 읽은(None) 몬스터를, 다른 아이템에서 읽은 같은 이름+지역의 가장 흔한 값으로 채운다. 채운 수를 돌려준다."""
    from collections import Counter, defaultdict
    vals = defaultdict(Counter)
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if m.get("power"):
                vals[(m["name"], m["region"])][m["power"]] += 1
    # 같은 지역 몬스터 값의 중앙값보다 훨씬 작은(1/4 미만) 값은 오독(94.5K를 6K로 읽는 식)으로 보고 비운다
    by_region = defaultdict(list)
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if m.get("power"):
                by_region[m["region"]].append(m["power"])
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            peers = sorted(by_region.get(m["region"], []))
            if m.get("power") and len(peers) >= 5 and m["power"] < peers[len(peers) // 2] / 4:
                vals[(m["name"], m["region"])].pop(m["power"], None)
                m["power"] = None
    n = 0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if not m.get("power") and vals.get((m["name"], m["region"])):
                m["power"] = vals[(m["name"], m["region"])].most_common(1)[0][0]
                m["power_filled"] = True
                n += 1
    mons = {}
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            mons[f"{m['name']}|{m['region']}"] = {"power": m["power"], "region": m["region"]}
    db["monsters"] = mons
    return n


def finalize(db):
    """스캔 뒤 정리: 이름 철자 통일 → 전투력 채우기. (바꾼 이름 수, 채운 전투력 수)"""
    pick_item_names(db)
    c = canonicalize(db)
    canonicalize_regions(db)
    fix_power_digits(db)
    f = fill_powers(db)
    f += fill_by_region(db)
    fill_powers(db)                                  # 몬스터 표(db["monsters"]) 갱신
    return c, f


def pick_item_names(db):
    """아이템 이름: 목록 글자와 상세 제목 글자 중 서로 비슷하면 긴 쪽, 다르면 한글이 더 많은 쪽. 그다음 같은 이름끼리 철자를 통일."""
    from collections import Counter

    def han(t):
        return len(re.findall(r"[가-힣]", t or ""))
    for it in db.get("items", []):
        a, b = it.get("name", ""), it.get("name_header", "")
        if b and (han(b) > han(a) or (han(b) == han(a) and len(b) > len(a) and _similar(a, b))):
            it["name"] = b
    cnt = Counter(it.get("name", "") for it in db.get("items", []))
    canon = {}
    for name, _n in cnt.most_common():
        if name in canon:
            continue
        canon[name] = name
        for other in cnt:
            if other not in canon and _similar(name, other) and abs(len(name) - len(other)) <= 1:
                canon[other] = name
    for it in db.get("items", []):
        if "name" in it:
            it["name"] = canon.get(it["name"], it["name"])


def canonicalize_regions(db):
    """지역 이름 철자 통일 ('안개 기습'→'안개 기슭'처럼 OCR 흔들림). 같은 방식으로 가장 많이 나온 철자를 쓴다."""
    from collections import Counter
    known = known_regions()
    n0 = 0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            r = re.sub(r"[^가-힣\s]+", " ", m.get("region") or "")                 # 숫자·기호 잡글자 제거
            r = re.sub(r"\s+", " ", r).strip()
            r = re.sub(r"^[가-힣]\s(?=[가-힣]{2})", "", r)                          # 앞에 붙은 한 글자 잡음('구 안개 기슭')
            best = next((k for k in known if re.sub(r"\s", "", k) == re.sub(r"\s", "", r)), None) or                 next((k for k in known if _similar(k, r) and abs(len(k) - len(r)) <= 1), None)
            r = best or r
            if r != m.get("region"):
                m["region"] = r
                n0 += 1
    cnt = Counter(m["region"] for it in db.get("items", []) for m in it.get("monsters", []) if m.get("region"))
    canon = {}
    for name, _n in cnt.most_common():
        if name in canon:
            continue
        canon[name] = name
        for other in cnt:
            if other not in canon and _similar(name, other) and abs(len(name) - len(other)) <= 1:
                canon[other] = name
    n = n0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if m.get("region") and canon.get(m["region"], m["region"]) != m["region"]:
                m["region"] = canon[m["region"]]
                n += 1
    return n


def known_regions():
    """길라잡이 사냥터 이름에서 뽑은 지역 이름들 ('안개 기슭 1' → '안개 기슭'). 철자 기준으로 쓴다."""
    try:
        from .farm import GUIDE_NODES
        names = [re.sub(r"\s*\d+$", "", n) for n, _p, _xy in GUIDE_NODES]
    except Exception:                                # noqa: BLE001
        return []
    return list(dict.fromkeys(names))


LATE_POWERS = {360000, 370000, 490000, 695000, 960000}        # 웹 공략 기준 후반 사냥터 권장 전투력


def fix_power_digits(db):
    """자릿수가 빠지거나 늘어난 전투력(135K→35K, 38.5K→385K)을 같은 지역 값과 길라잡이 목록에 맞춰 고친다. 고친 수."""
    from collections import Counter, defaultdict
    try:
        from .farm import GUIDE_NODES
        ladder = {p for _n, p, _xy in GUIDE_NODES}
    except Exception:                                # noqa: BLE001
        ladder = set()
    target = ladder | LATE_POWERS                     # 고칠 때 맞출 값 (실제 사냥터 값)
    by_region = defaultdict(list)
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if m.get("power") and m["power"] in target:
                by_region[m["region"]].append(m["power"])
    fixed = 0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            v = m.get("power")
            if not v or v in target:
                continue
            peers = sorted(by_region.get(m["region"], []))
            if not peers:
                continue
            med = peers[len(peers) // 2]
            cands = [v * 10, v // 10] + [int(f"{d}{v}") for d in range(1, 10)]
            cands = [c for c in cands if c in target and med / 2 <= c <= med * 2]
            if cands:
                m["power"] = min(cands, key=lambda c: abs(c - med))
                m["power_fixed"] = True
                fixed += 1
    return fixed


def fill_by_region(db):
    """아직 전투력이 없는 몬스터를 같은 지역 값이 거의 한 가지(70% 이상)일 때만 그 값으로 추정해 채운다 (power_est 표시)."""
    from collections import Counter, defaultdict
    reg = defaultdict(Counter)
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if m.get("power"):
                reg[m["region"]][m["power"]] += 1
    n = 0
    for it in db.get("items", []):
        for m in it.get("monsters", []):
            if not m.get("power") and reg.get(m["region"]):
                val, c = reg[m["region"]].most_common(1)[0]
                if c >= 0.7 * sum(reg[m["region"]].values()):
                    m["power"], m["power_est"] = val, True
                    n += 1
    return n
