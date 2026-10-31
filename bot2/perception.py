"""화면 한 장을 보고 '지금 게임이 어떤 상태인지'를 판별한다."""
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from bot.vision import Match, crop_ratio


@dataclass
class Snapshot:
    t: float
    frame: np.ndarray
    in_field: bool = False          # 필드 HUD(우하단 톱니바퀴)가 보임
    auto_moving: bool = False       # "자동 이동 중" 바가 보임
    combat: bool = False            # 대상 몬스터 체력 바가 보임
    dead: bool = False              # 사망 화면
    dialog: Optional[Match] = None  # 대화 넘기기 버튼
    dialog_name: str = ""           # 대화 넘기기 / 대화 다음 / 컷신 스킵
    walking: bool = False           # 하단 '자동 이동 중 N m' 바가 보임 (웨이포인트 이동 중)
    auto_on: bool = False           # 하단 T 슬롯이 주황색으로 켜져 있음 (자동 사냥/자동 퀘스트)
    tracker_changed: bool = False   # 직전 스냅샷보다 퀘스트 목록이 바뀜
    world_motion: float = 0.0       # 캐릭터 주변 화면 변화량
    notes: list = field(default_factory=list)

    @property
    def state(self):
        """우선순위가 높은 순으로 하나의 상태 이름."""
        if self.dead:
            return "DEAD"
        if self.dialog:
            return "DIALOG"
        if not self.in_field:
            return "OVERLAY"        # 메뉴/컷신/로딩 등 필드가 아닌 화면
        if self.combat:
            return "COMBAT"
        if self.auto_moving:
            return "MOVING"
        return "IDLE"


def tracker_text_mask(frame, region):
    """퀘스트 목록의 글자(흰색/노란색)만 남긴 마스크. 배경(반투명 너머의 전투 화면)은 무시한다."""
    area, _, _ = crop_ratio(frame, region)
    a = area.astype(np.int16)
    b, g, r = a[..., 0], a[..., 1], a[..., 2]
    white = (b > 215) & (g > 215) & (r > 215)
    yellow = (r > 210) & (g > 160) & (b < 110)
    return (white | yellow).astype(np.uint8)


def text_mask_diff(a, b, min_pixels=200):
    """두 글자 마스크의 차이 (0=같음, 1=완전히 다름). 1~2px 흔들림은 팽창으로 흡수. 글자가 거의 없으면 0."""
    if int(a.sum()) + int(b.sum()) < 2 * min_pixels:
        return 0.0
    k = np.ones((5, 5), np.uint8)
    ad, bd = cv2.dilate(a, k), cv2.dilate(b, k)
    kept = (int((a & bd).sum()) + int((b & ad).sum())) / 2
    return float(1 - kept / max((int(a.sum()) + int(b.sum())) / 2, 1))


def _thumb(frame, region, width=96):
    area, _, _ = crop_ratio(frame, region)
    gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
    scale = width / max(1, gray.shape[1])
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small, (3, 3), 0)


def autohunt_on(frame, cfg):
    """하단 AUTO 버튼의 주황색 점유율로 실제 활성 상태를 읽는다."""
    ah = cfg.get("autohunt") or {}
    area, _, _ = crop_ratio(frame, ah.get("region", [0.851, 0.922, 0.0167, 0.0315]))
    if not area.size:
        return False
    b, g, r = (area[..., i].astype(int) for i in range(3))
    return float(np.mean((r > 200) & (g > 120) & (b < 130)) * 100) > ah.get("on_percent", 5.0)


class Perceiver:
    def __init__(self, matcher, cfg):
        self.m = matcher
        self.cfg = cfg
        self._tracker_prev = None
        self._world_prev = None

    def reset(self):
        """창 크기가 바뀌면 이전 프레임 비교값을 버린다."""
        self._tracker_prev = self._world_prev = None

    def _find(self, frame, name, region=None, threshold=None):
        return self.m.find(frame, name, region, threshold) if self.m.has(name) else None

    def look(self, frame, now):
        c = self.cfg
        snap = Snapshot(t=now, frame=frame)

        fi = c.get("field_indicator") or {}
        snap.in_field = bool(self._find(frame, fi.get("template"), fi.get("region")))

        idle = c.get("idle") or {}
        snap.auto_moving = bool(self._find(frame, idle.get("auto_indicator"),
                                           idle.get("indicator_region")))

        # 하단 '자동 이동 중 202m' 바 (길라잡이/발자국/퀘스트 웨이포인트 이동). 이 동안 자동 사냥(T)을 켜면
        # 주변 몹을 공격하느라 이동이 취소되므로, 이동 중인지 따로 알아 둔다.
        seen_bar = bool(self._find(frame, "collect_moving.png", [0.30, 0.60, 0.45, 0.40], 0.8))
        if seen_bar:
            self._walk_seen_at = now
        # 이동 바는 이동 중에도 1~2초씩 깜빡이며 사라진다(실측) → 마지막으로 보인 뒤 grace초 안이면 계속 이동 중으로 본다
        snap.walking = seen_bar or (now - getattr(self, "_walk_seen_at", -1e9) < self.cfg.get("walk_grace", 4.0))

        ci = c.get("combat_indicator") or {}
        snap.combat = any(self._find(frame, t, ci.get("region")) for t in ci.get("templates", []))

        death = c.get("death") or {}
        snap.dead = bool(self._find(frame, death.get("template"), death.get("region")))

        for popup in c.get("popups", []):
            if popup.get("name") in ("대화 넘기기", "대화 다음", "컷신 스킵"):
                match = self._find(frame, popup["template"], popup.get("region"),
                                   popup.get("threshold"))
                if match:
                    snap.dialog = match
                    snap.dialog_name = popup["name"]

        snap.auto_on = autohunt_on(frame, c)

        qt = c.get("quest_tracker") or {}
        sig = tracker_text_mask(frame, qt.get("text_region", [0.80, 0.05, 0.155, 0.40]))
        if self._tracker_prev is not None and self._tracker_prev.shape == sig.shape:
            # 목록 배경이 반투명이라 화면 평균 차이는 전투 중 계속 '변함'으로 나온다 → 글자 마스크만 비교
            snap.tracker_changed = text_mask_diff(sig, self._tracker_prev) > qt.get("text_threshold", 0.3)
        self._tracker_prev = sig

        world = _thumb(frame, idle.get("region", [0.3, 0.25, 0.4, 0.5]), 160)
        if self._world_prev is not None and self._world_prev.shape == world.shape:
            # 평균 차이 대신 '크게 변한 픽셀 비율(%)': NPC 애니메이션은 일부만, 카메라 스크롤은 화면 전체가 변한다
            snap.world_motion = float(np.mean(cv2.absdiff(world, self._world_prev) > 18) * 100)
        self._world_prev = world
        return snap
