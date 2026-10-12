"""템플릿 매칭과 멈춤 감지."""
import logging
import os
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)


def load_image(path):
    """한글 경로도 읽을 수 있는 이미지 로더."""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_image(path, image):
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or ".png", image)
    if ok:
        buf.tofile(path)
    return ok


def crop_ratio(frame, region):
    """region = [x, y, w, h] 비율. (잘라낸 이미지, 원본 기준 x 오프셋, y 오프셋) 반환."""
    if not region:
        return frame, 0, 0
    h, w = frame.shape[:2]
    x0 = max(0, int(region[0] * w))
    y0 = max(0, int(region[1] * h))
    x1 = min(w, int((region[0] + region[2]) * w))
    y1 = min(h, int((region[1] + region[3]) * h))
    return frame[y0:y1, x0:x1], x0, y0


@dataclass
class Match:
    name: str
    x: int        # 캡처 이미지 기준 중심 좌표
    y: int
    score: float


class Matcher:
    def __init__(self, template_dir, threshold=0.85):
        self.template_dir = template_dir
        self.threshold = threshold
        self._cache = {}
        self._missing_warned = set()
        self.degrade = 1.0          # 창이 기준 해상도보다 작을 때, 확대돼 흐려진 화면에 맞춰 템플릿도 같이 흐리게

    def set_degrade(self, scale):
        """창 배율(<1)이면 템플릿을 같은 배율로 줄였다 늘려 화면과 선명도를 맞춘다."""
        scale = min(1.0, float(scale))
        if abs(scale - self.degrade) > 0.01:
            self.degrade = scale
            self._cache.clear()

    def _limit(self, threshold):
        base = threshold if threshold is not None else self.threshold
        return base - 0.1 * (1.0 - self.degrade)      # 확대로 흐려진 화면은 점수가 조금 낮게 나온다

    def has(self, name):
        return self._get(name) is not None

    def _get(self, name):
        if not name:
            return None
        if name not in self._cache:
            path = os.path.join(self.template_dir, name)
            image = load_image(path) if os.path.exists(path) else None
            if image is not None and self.degrade < 0.99:
                h, w = image.shape[:2]
                sw, sh = max(1, round(w * self.degrade)), max(1, round(h * self.degrade))
                image = cv2.resize(cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA),
                                   (w, h), interpolation=cv2.INTER_CUBIC)
            if image is None and name not in self._missing_warned:
                log.warning("템플릿 없음 (건너뜀): %s", path)
                self._missing_warned.add(name)
            self._cache[name] = image
        return self._cache[name]

    def find(self, frame, name, region=None, threshold=None):
        template = self._get(name)
        if template is None:
            return None
        area, ox, oy = crop_ratio(frame, region)
        th, tw = template.shape[:2]
        if area.shape[0] < th or area.shape[1] < tw:
            return None
        result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(result)
        if score < self._limit(threshold):
            return None
        return Match(name, ox + mx + tw // 2, oy + my + th // 2, float(score))


    def find_all(self, frame, name, region=None, threshold=None):
        """화면에 여러 개 있는 템플릿을 모두 찾는다. 왼쪽에 있는 것부터 정렬."""
        template = self._get(name)
        if template is None:
            return []
        area, ox, oy = crop_ratio(frame, region)
        th, tw = template.shape[:2]
        if area.shape[0] < th or area.shape[1] < tw:
            return []
        result = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        limit = self._limit(threshold)
        matches = []
        while True:
            _, score, _, (mx, my) = cv2.minMaxLoc(result)
            if score < limit:
                break
            matches.append(Match(name, ox + mx + tw // 2, oy + my + th // 2, float(score)))
            # 같은 자리 중복 검출 방지: 찾은 위치 주변을 지운다
            result[max(0, my - th):my + th, max(0, mx - tw):mx + tw] = -1
        return sorted(matches, key=lambda m: (m.x, m.y))


class MotionIdleDetector:
    """지정 영역의 화면 변화가 없는 시간을 잰다."""

    def __init__(self, region, diff_threshold):
        self.region = region
        self.diff_threshold = diff_threshold
        self._prev = None
        self._last_change = None
        self.last_diff = 0.0

    def reset(self, now):
        self._prev = None
        self._last_change = now

    def update(self, frame, now):
        """멈춰 있던 시간(초)을 반환."""
        area, _, _ = crop_ratio(frame, self.region)
        gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY)
        scale = 160 / max(1, gray.shape[1])
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (5, 5), 0)

        if self._last_change is None:
            self._last_change = now
        if self._prev is not None and self._prev.shape == small.shape:
            self.last_diff = float(np.mean(cv2.absdiff(small, self._prev)))
            if self.last_diff > self.diff_threshold:
                self._last_change = now
        self._prev = small
        return now - self._last_change
