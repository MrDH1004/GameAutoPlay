"""수집된 지역 지도 샘플의 도로 마스크와 중심선 오버레이를 생성한다."""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from bot2.map_vision import analyze_map, overlay

ROOT = Path(__file__).resolve().parents[1]


def read_image(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def write_image(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise OSError(f"이미지 인코딩 실패: {path}")
    encoded.tofile(path)


def analyze(source):
    source = Path(source)
    output = source.parent / "analysis"
    rows = []
    for path in sorted(source.glob("*.png")):
        image = read_image(path)
        graph = analyze_map(image)
        write_image(output / f"{path.stem}_mask.png", graph.mask)
        write_image(output / f"{path.stem}_overlay.jpg", overlay(image, graph))
        ratio = float(np.mean(graph.mask > 0))
        rows.append((path.name, ratio, len(graph.nodes)))
        print(f"{path.name}: road={ratio:.3f}, nodes={len(graph.nodes)}")
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default=ROOT / "screenshots" / "map-road" / "raw")
    args = parser.parse_args()
    analyze(args.source)


if __name__ == "__main__":
    main()
