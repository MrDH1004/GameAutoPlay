"""HP가 아주 낮을 때 '자리 회피': 세계지도에서 몹이 적은 쪽을 눌러 걸어가기(무료)로 빠졌다가,
회복한 뒤 같은 거리만큼 반대로 걸어가기를 눌러 원래 자리로 돌아온다.

세계지도는 열 때마다 내 캐릭터가 화면 중앙(960, 540)에 오므로, 중앙에서 (dx, dy)만큼 떨어진 곳을 누르면 그 방향으로 걷고,
다음에 지도를 열어 (-dx, -dy)를 누르면 처음 자리 근처로 돌아온다. 좌표를 따로 읽을 필요가 없다. (실측: 약 1~2m 오차)

'축지술'(유료)은 걸어가기 버튼 바로 옆에 있다 → 버튼은 걸어가기 템플릿을 찾아 그 위치만 누른다.
"""
import logging
import math

import numpy as np

from .collect import _matches, _tpl

log = logging.getLogger("bot2")

MINIMAP = (175, 131)                 # 좌상단 미니맵: 누르면 세계지도가 열린다 (실측)
CENTER = (960, 540)                  # 지도에서 내 캐릭터 위치
MAP_TITLE_BOX = (100, 10, 330, 90)   # '세계지도' 제목
WALK_BTN_BOX = (1400, 940, 1900, 1075)   # 핀을 누르면 나오는 [걸어가기] [축지술] 줄
PX_PER_M = 27.0                      # 지도 190px ≈ 7m (실측)
SPEED_MPS = 1.3                      # 걷는 속도 추정 (넉넉하게)


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


class Evader:
    def __init__(self, drv, dist_px=380):
        self.drv = drv
        self.dist = dist_px

    def _map_open(self, img):
        return bool(_matches(img, _tpl("world_map_title.png"), MAP_TITLE_BOX, 0.85))

    def _close_map(self):
        d = self.drv
        for _ in range(3):
            if not self._map_open(d.shot()):
                return
            d.key("esc")
            d.sleep(0.8)

    def _walk_wait(self, dx, dy):
        meters = math.hypot(dx, dy) / PX_PER_M
        self.drv.sleep(3.0 + meters / SPEED_MPS)

    def _go(self, offsets):
        """지도를 열고 offsets 후보를 차례로 눌러 첫 번째로 걸어가기가 뜨는 곳으로 걷는다. 성공한 (dx, dy) 또는 None."""
        d = self.drv
        d.click(*MINIMAP)
        d.sleep(1.5)
        if not self._map_open(d.shot()):
            log.warning("자리 회피: 세계지도가 열리지 않음")
            self._close_map()
            return None
        for dx, dy in offsets:
            d.click(CENTER[0] + dx, CENTER[1] + dy)
            d.sleep(0.8)
            hit = _matches(d.shot(), _tpl("world_walk_btn.png"), WALK_BTN_BOX, 0.85)
            if not hit:
                continue                                   # 걸을 수 없는 곳 (벽 등) → 다음 후보
            t = _tpl("world_walk_btn.png")
            d.click(hit[0][0] + t.shape[1] // 2, hit[0][1] + t.shape[0] // 2)
            self._walk_wait(dx, dy)
            return (dx, dy)
        self._close_map()
        return None

    def flee(self, frame):
        """몹에서 먼 쪽으로 걸어 나간다. 이동한 (dx, dy)를 돌려준다 (돌아올 때 쓴다). 실패하면 None."""
        off = self._go(flee_directions(frame, self.dist))
        if off:
            log.info("자리 회피: 지도 오프셋 (%d, %d)만큼 이동 (약 %.0fm)", off[0], off[1], math.hypot(*off) / PX_PER_M)
        else:
            log.warning("자리 회피: 걸어갈 곳을 찾지 못함")
        return off

    def back(self, off):
        """flee가 돌려준 오프셋의 반대로 걸어 원래 자리로 돌아온다."""
        back = self._go([(-off[0], -off[1])])
        if back:
            log.info("자리 회피: 원래 자리 근처로 복귀")
        else:
            log.warning("자리 회피: 원래 자리로 복귀하지 못함 (현재 자리에서 사냥)")
        return bool(back)
