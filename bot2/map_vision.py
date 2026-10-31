"""지역 지도에서 도로 영역·중심선·경로 그래프를 추출한다."""
from __future__ import annotations

from dataclasses import dataclass
import heapq

import cv2
import numpy as np


@dataclass(frozen=True)
class RoadGraph:
    mask: np.ndarray
    skeleton: np.ndarray
    nodes: tuple[tuple[int, int], ...]


def _content_roi(shape):
    h, w = shape[:2]
    valid = np.zeros((h, w), np.uint8)
    valid[int(h * .065):int(h * .91), int(w * .047):int(w * .995)] = 255
    return valid


def extract_road_mask(image):
    """베이지색 도로의 밝은 능선을 다중 크기 국소 대비로 분리한다."""
    if image is None or image.size == 0:
        raise ValueError("빈 지도 이미지")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hue, sat, val = (hsv[..., index] for index in range(3))
    light = lab[..., 0]

    ridge = np.zeros_like(light)
    for size in (25, 45, 75):
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        ridge = np.maximum(ridge, cv2.morphologyEx(light, cv2.MORPH_TOPHAT, kernel))
    local = cv2.GaussianBlur(light, (0, 0), 19)
    contrast = light.astype(np.int16) - local.astype(np.int16)

    beige = ((hue >= 7) & (hue <= 32) & (sat >= 18) & (sat <= 150)
             & (val >= 88) & (val <= 240))
    seeds = beige & ((ridge >= 14) | (contrast >= 10))
    mask = seeds.astype(np.uint8) * 255
    mask &= _content_roi(image.shape)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8))
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    min_area = max(700, int(image.shape[0] * image.shape[1] * .0005))
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if count > 1 else 0
    connected_min = max(min_area, int(largest * .08))
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if area >= connected_min and max(width, height) >= 70:
            cleaned[labels == index] = 255
    return cleaned


def skeletonize(mask):
    """OpenCV 기본 연산만으로 이진 마스크의 1픽셀 중심선을 만든다."""
    work = (mask > 0).astype(np.uint8) * 255
    skeleton = np.zeros_like(work)
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while cv2.countNonZero(work):
        eroded = cv2.erode(work, kernel)
        opened = cv2.dilate(eroded, kernel)
        skeleton = cv2.bitwise_or(skeleton, cv2.subtract(work, opened))
        work = eroded
    return skeleton


def graph_nodes(skeleton, spacing=45):
    """끝점·교차점과 긴 구간의 보간 지점을 반환한다."""
    binary = (skeleton > 0).astype(np.uint8)
    neighbors = cv2.filter2D(binary, -1, np.ones((3, 3), np.uint8)) - binary
    important = ((binary > 0) & ((neighbors == 1) | (neighbors >= 4))).astype(np.uint8) * 255
    merged = cv2.dilate(important, np.ones((9, 9), np.uint8))
    count, labels, stats, centers = cv2.connectedComponentsWithStats(merged, 8)
    nodes = []
    skeleton_points = np.column_stack(np.where(binary > 0))
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] > spacing * spacing * 6:
            continue
        cx, cy = centers[index]
        if len(skeleton_points):
            nearest = skeleton_points[np.argmin(
                (skeleton_points[:, 1] - cx) ** 2 + (skeleton_points[:, 0] - cy) ** 2)]
            nodes.append((int(nearest[1]), int(nearest[0])))
    return tuple(nodes)


def _nearest_skeleton(skeleton, point):
    ys, xs = np.where(skeleton > 0)
    if not len(xs):
        return None
    x, y = point
    index = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
    return int(xs[index]), int(ys[index])


def shortest_path(skeleton, start, goal):
    """중심선 위의 두 지점을 8방향 A*로 연결한다."""
    start = _nearest_skeleton(skeleton, start)
    goal = _nearest_skeleton(skeleton, goal)
    if start is None or goal is None:
        return ()
    queue = [(0.0, start)]
    cost = {start: 0.0}
    parent = {}
    h, w = skeleton.shape
    while queue:
        _, current = heapq.heappop(queue)
        if current == goal:
            path = [current]
            while current in parent:
                current = parent[current]
                path.append(current)
            return tuple(reversed(path))
        x, y = current
        for dx, dy, step in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                             (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h and skeleton[ny, nx]):
                continue
            new_cost = cost[current] + step
            if new_cost < cost.get((nx, ny), float("inf")):
                cost[(nx, ny)] = new_cost
                parent[(nx, ny)] = current
                heuristic = ((goal[0] - nx) ** 2 + (goal[1] - ny) ** 2) ** .5
                heapq.heappush(queue, (new_cost + heuristic, (nx, ny)))
    return ()


def map_translation(origin, current):
    """같은 배율의 두 지도에서 현재 화면에 나타난 지형 이동량과 신뢰도를 구한다."""
    if origin is None or current is None or origin.shape != current.shape:
        return None
    h, w = origin.shape[:2]
    valid = _content_roi(origin.shape).astype(np.float32) / 255.0

    def features(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        result = cv2.magnitude(gx, gy) * valid
        result[:int(h * .08)] = 0
        result[int(h * .90):] = 0
        result[:, :int(w * .08)] = 0
        return result

    shift, confidence = cv2.phaseCorrelate(features(origin), features(current))
    dx, dy = map(float, shift)
    if confidence < .04 or abs(dx) > w * .48 or abs(dy) > h * .48:
        return None
    return dx, dy, float(confidence)


def analyze_map(image):
    mask = extract_road_mask(image)
    skeleton = skeletonize(mask)
    return RoadGraph(mask=mask, skeleton=skeleton, nodes=graph_nodes(skeleton))


def overlay(image, graph):
    result = image.copy()
    green = np.zeros_like(result)
    green[..., 1] = 220
    road = graph.mask > 0
    result[road] = (result[road] * .45 + green[road] * .55).astype(np.uint8)
    result[graph.skeleton > 0] = (30, 230, 255)
    for x, y in graph.nodes:
        cv2.circle(result, (x, y), 4, (255, 80, 30), -1, cv2.LINE_AA)
    return result
