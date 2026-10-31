"""HP가 아주 낮을 때 '자리 회피': 세계지도에서 몹이 적은 쪽을 눌러 걸어가기(무료)로 빠졌다가,
회복한 뒤 같은 거리만큼 반대로 걸어가기를 눌러 원래 자리로 돌아온다.

세계지도는 열 때마다 내 캐릭터가 화면 중앙(960, 540)에 오므로, 중앙에서 (dx, dy)만큼 떨어진 곳을 누르면 그 방향으로 걷고,
다음에 지도를 열어 (-dx, -dy)를 누르면 처음 자리 근처로 돌아온다. 좌표를 따로 읽을 필요가 없다. (실측: 약 1~2m 오차)

'축지술'(유료)은 걸어가기 버튼 바로 옆에 있다 → 버튼은 걸어가기 템플릿을 찾아 그 위치만 누른다.
"""
import logging
import math
import re
import time

import cv2
import numpy as np

from .collect import _matches, _tpl
from .farm import _crop, _ocr
from .map_vision import extract_road_mask, map_translation

log = logging.getLogger("bot2")

MINIMAP = (175, 131)                 # 좌상단 미니맵: 누르면 세계지도가 열린다 (실측)
CENTER = (960, 540)                  # 지도에서 내 캐릭터 위치
MAP_TITLE_BOX = (100, 10, 330, 90)   # '세계지도' 제목
WALK_BTN_BOX = (1400, 940, 1900, 1075)   # 핀을 누르면 나오는 [걸어가기] [축지술] 줄
MAP_ZOOM_MAX = (1104, 1020)            # 지도 진입 시 기본 최대 확대 노브
MAP_ZOOM_MID = (960, 1020)             # 하단 지도 확대 슬라이더의 중앙값(1920×1080 기준)
REGION_TAB = (50, 180)               # 세계지도 왼쪽 '지역 정보' 탭 (실측)
LIST_BOX = (100, 130, 420, 1060)     # 지역 정보 목록 (그룹 머리줄은 어둡고, 하위 지역 줄은 밝다)
LIST_CLICK_X = 250
DIST_BOX = (1560, 945, 1760, 985)    # 지역을 누르면 오른쪽 아래에 나오는 '326m'
ROW_BRIGHT = 130                     # 하위 지역 줄 배경 밝기 기준 (머리줄 82~92, 하위 지역 158~187)
MIN_DIST = 50                        # 이 거리(m) 안의 지역은 '지금 내가 있는 지역'
CACHE_SECS = 45 * 60                 # 미리 읽어 둔 지역 정보를 믿는 시간
MAX_DIST = 900                       # 이보다 먼 지역으로는 걸어가지 않는다
PX_PER_M = 13.4                      # 중앙 배율 실측: 지도 약 887px ≈ 이동 거리 66m
SPEED_MPS = 1.3                      # 걷는 속도 추정 (넉넉하게)


def parse_dist(text):
    """'326m' 같은 글자에서 미터. 못 읽으면 None."""
    m = re.search(r"(\d{1,4})\s*m", (text or "").replace(",", ""))
    return int(m.group(1)) if m else None


def pick_target(areas):
    """areas = [{name, cy, dist}] → (지금 있는 지역, 걸어갈 지역).
    지금 있는 지역 = 거리가 MIN_DIST 안인 곳(가장 가까운 것). 걸어갈 지역 = 나머지 중 가장 가까운 곳 (MAX_DIST 이내). 없으면 None."""
    if not areas:
        return None, None
    here = min((a for a in areas if a["dist"] < MIN_DIST), key=lambda a: a["dist"], default=None)
    far = [a for a in areas if MIN_DIST <= a["dist"] <= MAX_DIST]
    return here, (min(far, key=lambda a: a["dist"]) if far else None)


def mob_centroid(frame):
    """화면에서 몹 체력바(빨강)가 몰린 곳의 중심 (x, y). 없으면 None. UI 영역은 제외."""
    h, w = frame.shape[:2]
    sx, sy = w / 1920.0, h / 1080.0
    x0, x1, y0, y1 = int(250 * sx), int(1500 * sx), int(160 * sy), int(900 * sy)
    c = frame[y0:y1, x0:x1].astype(int)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    ys, xs = np.where((r > 170) & (g < 70) & (b < 70))
    if len(xs) < 20:
        return None
    return (float(xs.mean() + x0) / sx, float(ys.mean() + y0) / sy)


def flee_directions(frame, dist):
    """도망 방향 후보 [(dx, dy)] — 몹에서 먼 쪽 → 옆쪽 → 반대쪽 순서."""
    m = mob_centroid(frame)
    base = math.atan2(CENTER[1] - m[1], CENTER[0] - m[0]) if m else 0.0
    out = []
    for deg in (0, 45, -45, 90, -90, 135, -135):
        a = base + math.radians(deg)
        out.append((int(round(math.cos(a) * dist)), int(round(math.sin(a) * dist * 0.6))))   # 지도 가로가 길어 세로는 줄여 눌러 화면 밖을 피함
    return out


def road_escape_target(frame, max_dist, min_dist=None):
    """세계지도에서 플레이어 표식과 연결된 밝은 도로 중 먼 클릭 지점을 찾는다.

    반환값은 ``(x, y, dx, dy)``이며, 색상 분리가 불가능하면 None이다.
    """
    if frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue, sat, val = (hsv[..., i] for i in range(3))

    # 현재 위치의 금색 화살. 작은 노란 글자·장식은 연결 요소 크기로 제외한다.
    marker_mask = ((hue >= 15) & (hue <= 35) & (sat >= 115) & (val >= 175)).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(marker_mask, 8)
    marker_candidates = []
    for i in range(1, count):
        x, y, bw, bh, area = stats[i]
        if 20 <= area <= 2500 and 0.05 * w < x + bw / 2 < 0.95 * w and 0.08 * h < y + bh / 2 < 0.92 * h:
            marker_candidates.append((area, centers[i]))
    if marker_candidates:
        _, center = max(marker_candidates, key=lambda item: item[0])
        cx, cy = map(float, center)
    else:
        cx, cy = w / 2.0, h / 2.0

    # 다중 크기 국소 대비로 도로 영역을 잡는다. 단순 색상 범위보다 지도별 명암 변화에 강하다.
    road = extract_road_mask(frame)
    if cv2.countNonZero(road) < 300:
        road = ((hue >= 8) & (hue <= 30) & (sat >= 28) & (sat <= 125)
                & (val >= 105) & (val <= 220)).astype(np.uint8) * 255
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        road = cv2.morphologyEx(road, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # 플레이어와 가장 가까운 도로 연결 요소만 사용하여 호수·배경을 찍지 않는다.
    n, road_labels, road_stats, _ = cv2.connectedComponentsWithStats(road, 8)
    yy, xx = np.indices((h, w))
    nearest = None
    for i in range(1, n):
        if road_stats[i, cv2.CC_STAT_AREA] < 300:
            continue
        ys, xs = np.where(road_labels == i)
        if not len(xs):
            continue
        d2 = np.min((xs - cx) ** 2 + (ys - cy) ** 2)
        if nearest is None or d2 < nearest[0]:
            nearest = (d2, i)
    if nearest is None or nearest[0] > (max(h, w) * 0.12) ** 2:
        return None

    component = (road_labels == nearest[1]).astype(np.uint8)
    distance = np.hypot(xx - cx, yy - cy)
    scale = w / 1920.0
    limit = max(80.0, float(max_dist) * scale)
    minimum = max(70.0, float(min_dist if min_dist is not None else max_dist * .75) * scale)
    candidates = (component > 0) & (distance >= minimum) & (distance <= limit * 1.15)
    margin = int(min(h, w) * 0.04)
    candidates[:margin] = candidates[-margin:] = False
    candidates[:, :margin] = candidates[:, -margin:] = False
    if not np.any(candidates):
        return None

    # 도로 가장자리보다 중앙을 선호하면서 가능한 한 멀리 간다.
    clearance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
    score = distance + np.minimum(clearance, 12.0) * 4.0
    score[~candidates] = -1
    ty, tx = np.unravel_index(np.argmax(score), score.shape)
    return int(tx), int(ty), int(round(tx - cx)), int(round(ty - cy))


class Evader:
    def __init__(self, drv, dist_px=900, min_dist_px=None, min_meters=40):
        self.drv = drv
        self.dist = dist_px
        self.min_dist = int(min_dist_px if min_dist_px is not None else dist_px * .75)
        self.min_meters = int(min_meters)
        self.cache = None                # prepare()로 미리 읽어 둔 지역 정보

    def _map_open(self, img):
        return bool(_matches(img, _tpl("world_map_title.png"), MAP_TITLE_BOX, 0.85))

    def _close_map(self):
        d = self.drv
        for _ in range(3):
            if not self._map_open(d.shot()):
                return
            d.key("esc")
            d.sleep(0.8)

    def _set_mid_zoom(self):
        """분석·복귀 좌표가 항상 같은 축척이 되도록 지도 배율을 중앙값으로 고정한다."""
        self.drv.drag(*MAP_ZOOM_MAX, *MAP_ZOOM_MID)
        self.drv.sleep(0.8)

    def _selected_distance(self, frame):
        return parse_dist(_ocr(_crop(frame, DIST_BOX), 3))

    def _walk_wait(self, dx, dy, meters=None):
        meters = float(meters if meters is not None else math.hypot(dx, dy) / PX_PER_M)
        self.drv.sleep(3.0 + meters / SPEED_MPS)

    def _go(self, offsets, prefer_road=True, road_only=False):
        """지도를 열고 offsets 후보를 차례로 눌러 첫 번째로 걸어가기가 뜨는 곳으로 걷는다. 성공한 (dx, dy) 또는 None."""
        d = self.drv
        d.click(*MINIMAP)
        d.sleep(1.5)
        if not self._map_open(d.shot()):
            log.warning("자리 회피: 세계지도가 열리지 않음")
            self._close_map()
            return None
        self._set_mid_zoom()
        frame = d.shot()
        road = road_escape_target(frame, self.dist, self.min_dist) if prefer_road else None
        if road_only and not road:
            log.warning("자리 회피: 연결된 도로에서 최소 이동 거리 목표를 찾지 못함")
            self._close_map()
            return None
        targets = ([(road[0], road[1], road[2], road[3])] if road else []) + [
            (CENTER[0] + dx, CENTER[1] + dy, dx, dy) for dx, dy in offsets]
        if road:
            log.info("자리 회피: 도로 픽셀 분석 목표 (%d, %d), 현재 위치에서 (%+d, %+d)", *road)
        for x, y, dx, dy in targets:
            d.click(x, y)
            d.sleep(0.8)
            selected = d.shot()
            hit = _matches(selected, _tpl("world_walk_btn.png"), WALK_BTN_BOX, 0.85)
            if not hit:
                continue                                   # 걸을 수 없는 곳 (벽 등) → 다음 후보
            meters = self._selected_distance(selected) if road else None
            if road and (meters is None or meters < self.min_meters):
                log.warning("자리 회피: 선택 경로 거리 %s → 최소 %dm 미달/판독 실패로 이동 취소",
                            f"{meters}m" if meters is not None else "판독 불가", self.min_meters)
                continue
            t = _tpl("world_walk_btn.png")
            d.click(hit[0][0] + t.shape[1] // 2, hit[0][1] + t.shape[0] // 2)
            self._walk_wait(dx, dy, meters)
            if road:
                return {"kind": "road", "offset": (dx, dy), "origin_map": frame,
                        "origin_point": (x - dx, y - dy), "meters": meters}
            return (dx, dy)
        self._close_map()
        return None

    # ---------- 지역 정보 목록으로 이동 (사용자 제안: 지도 임의 지점은 막힌 곳일 수 있다) ----------
    def _open_list(self):
        d = self.drv
        d.click(*MINIMAP)
        d.sleep(1.5)
        if not self._map_open(d.shot()):
            self._close_map()
            return False
        d.click(*REGION_TAB)
        d.sleep(1.0)
        return True

    def _list_rows(self, img):
        """지역 정보 목록의 하위 지역 줄: [(이름, 중심 y)] 위→아래 (머리줄은 어두워서 뺀다)."""
        from .itemdict import _lines
        out = []
        for l in _lines(img, LIST_BOX, 2):
            cy = int(l["cy"])
            band = img[max(cy - 10, 0):cy + 10, 150:380].astype(int)
            if band.size and band.mean() > ROW_BRIGHT and len(re.findall(r"[가-힣]", l["text"])) >= 2:
                out.append((re.sub(r"^[^가-힣]+", "", l["text"]).strip(), cy))
        return sorted(out, key=lambda r: r[1])

    def _press_walk(self):
        """오른쪽 아래 [걸어가기]를 눌렀으면 True ([축지술]은 유료라 템플릿으로 찾은 걸어가기 위치만 누른다)."""
        d = self.drv
        hit = _matches(d.shot(), _tpl("world_walk_btn.png"), WALK_BTN_BOX, 0.8)
        if not hit:
            return False
        t = _tpl("world_walk_btn.png")
        d.click(hit[0][0] + t.shape[1] // 2, hit[0][1] + t.shape[0] // 2)
        return True

    def _wait_arrival(self, dist):
        """걸어가는 동안 기다린다: '자동 이동 중' 바가 보였다 사라지면 도착, 아니면 거리로 어림한 시간."""
        d = self.drv
        limit = 4.0 + dist / 3.0
        seen, quiet, t = False, 0, 0.0
        while t < limit:
            d.sleep(1.0)
            t += 1.0
            walking = bool(_matches(d.shot(), _tpl("collect_moving.png"), (576, 660, 1344, 1080), 0.8))
            seen = seen or walking
            quiet = 0 if walking else quiet + 1
            if seen and quiet >= 3:
                return
        return

    def _scan_areas(self):
        """지도 지역 정보 목록의 하위 지역을 하나씩 눌러 거리를 읽는다 (지도 목록이 열려 있어야 함). [{name, cy, dist}]"""
        d = self.drv
        areas = []
        for name, cy in self._list_rows(d.shot())[:6]:
            d.click(LIST_CLICK_X, cy)
            d.sleep(1.2)
            dist = parse_dist(_ocr(_crop(d.shot(), DIST_BOX), 2))
            if dist is not None:
                areas.append({"name": name, "cy": cy, "dist": dist})
        return areas

    def prepare(self):
        """안전할 때 미리 지역 정보(이름·거리)를 읽어 둔다: 지도를 열어 읽고 아무 곳도 가지 않고 닫는다. 성공하면 True."""
        if not self._open_list():
            return False
        try:
            areas = self._scan_areas()
        finally:
            self._close_map()
        here, tgt = pick_target(areas)
        self.cache = {"at": time.monotonic(), "areas": areas, "here": here, "tgt": tgt}
        log.info("자리 회피 준비: 지금 있는 곳 %s · 피신할 곳 %s (%s)", here["name"] if here else "?", tgt["name"] if tgt else "없음",
                 ", ".join(f"{a['name']} {a['dist']}m" for a in areas) or "-")
        return bool(tgt)

    def flee_area(self):
        """지역 정보 목록에서 지금 있는 곳이 아닌 가장 가까운 지역을 골라 걸어간다.
        돌려주는 값: {"area": 원래 지역 이름 또는 None, "to": 간 지역, "dist": m} 또는 None."""
        d = self.drv
        if not self._open_list():
            return None
        if self.cache and time.monotonic() - self.cache["at"] < CACHE_SECS and self.cache.get("tgt"):
            # 안전할 때 미리 읽어 둔 지역 정보로 바로 이동 (위험할 때 줄마다 눌러 거리를 읽으면 20초 넘게 걸려 그 사이에 죽는다)
            here, tgt = self.cache.get("here"), self.cache["tgt"]
            log.info("자리 회피: 미리 읽어 둔 지역 정보 사용 → '%s'", tgt["name"])
        else:
            areas = self._scan_areas()
            here, tgt = pick_target(areas)
        if not tgt:
            log.warning("자리 회피: 지역 정보 목록에서 걸어갈 지역을 못 찾음 (%s)", ", ".join(f"{a['name']} {a['dist']}m" for a in areas) or "-")
            self._close_map()
            return None
        d.click(LIST_CLICK_X, tgt["cy"])
        d.sleep(1.2)
        if not self._press_walk():
            log.warning("자리 회피: '%s'에서 [걸어가기]가 안 보임", tgt["name"])
            self._close_map()
            return None
        log.info("자리 회피: 지역 정보 목록 → '%s' (%dm)로 걸어감 (지금 있는 곳: %s)", tgt["name"], tgt["dist"], here["name"] if here else "알 수 없음")
        self._wait_arrival(tgt["dist"])
        return {"area": here["name"] if here else None, "to": tgt["name"], "dist": tgt["dist"]}

    def back_area(self, token):
        """flee_area가 돌려준 원래 지역으로 다시 걸어간다. 지역 이름을 못 찾으면 False."""
        from .collect import _similar
        d = self.drv
        name = token.get("area")
        if not name or not self._open_list():
            return False
        for nm, cy in self._list_rows(d.shot()):
            if _similar(nm, name) or _close_name(nm, name):
                d.click(LIST_CLICK_X, cy)
                d.sleep(1.2)
                if not self._press_walk():
                    break
                log.info("자리 회피: 원래 지역 '%s'로 걸어서 복귀", name)
                self._wait_arrival(int(token.get("dist", 300)))
                return True
        self._close_map()
        return False

    def flee(self, frame):
        """실제 지도에서 현재 위치와 연결된 도로의 먼 지점으로 걸어간다."""
        off = self._go([], prefer_road=True, road_only=True)
        if off:
            dx, dy = off["offset"]
            log.info("자리 회피: 실제 지도 도로를 따라 (%d, %d) 이동 (약 %.0fm)",
                     dx, dy, math.hypot(dx, dy) / PX_PER_M)
        else:
            log.warning("자리 회피: 최소 거리 이상 연결된 도로를 찾지 못함")
        return off

    def _back_road(self, token):
        """회피 전후 지도 지형을 정합하여 저장한 원래 지점으로 최대 2회 보정 이동한다."""
        d = self.drv
        origin = token.get("origin_map")
        origin_point = token.get("origin_point", CENTER)
        offset = token.get("offset", (0, 0))
        for attempt in range(3):
            d.click(*MINIMAP)
            d.sleep(1.5)
            current = d.shot()
            if not self._map_open(current):
                self._close_map()
                return False
            self._set_mid_zoom()
            current = d.shot()
            aligned = map_translation(origin, current)
            if aligned:
                dx, dy, confidence = aligned
                remaining = math.hypot(dx, dy)
                log.info("자리 회피 복귀 정합: 잔여 %.0fpx · 신뢰도 %.2f", remaining, confidence)
                if remaining <= 38:
                    self._close_map()
                    return True
                x, y = origin_point[0] + dx, origin_point[1] + dy
            elif attempt == 0:
                dx, dy = -offset[0], -offset[1]
                remaining = math.hypot(dx, dy)
                x, y = CENTER[0] + dx, CENTER[1] + dy
                log.warning("자리 회피 복귀: 지도 정합 실패 → 저장 오프셋으로 1회 복귀")
            else:
                self._close_map()
                return False
            x = int(max(120, min(current.shape[1] - 120, x)))
            y = int(max(100, min(current.shape[0] - 130, y)))
            d.click(x, y)
            d.sleep(.8)
            selected = d.shot()
            hit = _matches(selected, _tpl("world_walk_btn.png"), WALK_BTN_BOX, .82)
            if not hit:
                self._close_map()
                return False
            meters = self._selected_distance(selected)
            template = _tpl("world_walk_btn.png")
            d.click(hit[0][0] + template.shape[1] // 2, hit[0][1] + template.shape[0] // 2)
            self._walk_wait(dx, dy, meters)
        return False

    def back(self, off):
        """원래 자리 근처로 돌아온다 (dict면 그 지역으로, tuple이면 반대 오프셋으로)."""
        if isinstance(off, dict) and off.get("kind") == "road":
            ok = self._back_road(off)
            log.info("자리 회피: 원래 위치 복귀 %s", "완료" if ok else "확인 실패")
            return ok
        if isinstance(off, dict):
            ok = self.back_area(off)
            if ok:
                return True
            log.warning("자리 회피: 원래 지역으로 복귀하지 못함 (현재 자리에서 사냥)")
            return False
        back = self._go([(-off[0], -off[1])], prefer_road=False)
        if back:
            log.info("자리 회피: 원래 자리 근처로 복귀")
        else:
            log.warning("자리 회피: 원래 자리로 복귀하지 못함 (현재 자리에서 사냥)")
        return bool(back)


def _close_name(a, b):
    from difflib import SequenceMatcher
    a, b = re.sub(r"\s", "", a or ""), re.sub(r"\s", "", b or "")
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= 0.75
