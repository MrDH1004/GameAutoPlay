"""열려 있는 지역 지도를 같은 배율로 이동하며 OpenCV 샘플을 수집한다."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

from tools import ctl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "screenshots" / "map-road"


def read_image(path: Path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def perceptual_signature(image):
    gray = cv2.cvtColor(image[70:970, 90:1910], cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32)


def is_duplicate(image, signatures, threshold=2.0):
    signature = perceptual_signature(image)
    duplicate = any(float(np.mean(np.abs(signature - old))) < threshold for old in signatures)
    return duplicate, signature


def bridge(command):
    result = ctl.send(command, timeout=45)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "브리지 명령 실패"))
    return result


def capture_samples(output=DEFAULT_OUTPUT, wanted=20):
    output = Path(output)
    raw = output / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    # 현재 위치 포함 5열 × 4행. 지도만 움직이고 확대/축소 컨트롤은 건드리지 않는다.
    moves = [(960, 350, 960, 850)]                    # 기준점 북쪽부터 확보
    for row in range(4):
        if row:
            moves.append((960, 600, 960, 330))       # 남쪽 영역 표시
        direction = -1 if row % 2 == 0 else 1
        for _ in range(4):
            moves.append((960, 540, 960 + direction * 360, 540))

    existing = sorted(raw.glob("map_*.png"))
    signatures = []
    records = []
    for path in existing:
        image = read_image(path)
        if image is not None:
            signatures.append(perceptual_signature(image))
            records.append({"file": path.name, "attempt": "previous"})
    next_index = max((int(path.stem.split("_")[-1]) for path in existing), default=-1) + 1
    if len(records) >= wanted:
        return records[:wanted]
    pending_move = None
    for attempt in range(wanted + len(moves)):
        if pending_move:
            x1, y1, x2, y2 = pending_move
            bridge({"action": "drag", "x": x1, "y": y1, "x2": x2, "y2": y2, "wait": 0.7})
        result = bridge({"action": "screenshot", "wait": 0.2})
        source = Path(result["screenshot"])
        image = read_image(source)
        if image is None:
            raise RuntimeError(f"스크린샷 읽기 실패: {source}")
        duplicate, signature = is_duplicate(image, signatures)
        if not duplicate:
            destination = raw / f"map_{next_index:02d}.png"
            shutil.copy2(source, destination)
            next_index += 1
            signatures.append(signature)
            records.append({"file": destination.name, "attempt": attempt})
            print(f"수집 {len(records):02d}/{wanted}: {destination.name}")
            if len(records) >= wanted:
                break
        if attempt < len(moves):
            pending_move = moves[attempt]
        else:
            diagonal = ((420, 280), (-420, 280), (420, -280), (-420, -280))
            dx, dy = diagonal[attempt % len(diagonal)]
            pending_move = (960, 540, 960 + dx, 540 + dy)

    manifest = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(records),
                "zoom": "fixed-current", "records": records}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if len(records) < wanted:
        raise RuntimeError(f"서로 다른 지도 샘플이 {len(records)}장만 수집됨")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    capture_samples(args.output, args.count)


if __name__ == "__main__":
    main()
