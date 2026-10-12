"""Windows 내장 OCR(한국어) 래퍼. 설치 없이 PowerShell로 호출한다 (호출당 약 1~2초).

    from tools.ocr import read_text
    lines = read_text(image_bgr)        # [{'text','x','y','w','h'}] (입력 이미지 픽셀 좌표)
"""
import json
import os
import subprocess
import tempfile

import cv2

PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr.ps1")


def read_text(image, scale=2.0, lang="ko"):
    """BGR 이미지에서 줄 단위 글자를 읽는다. 작은 글자는 scale배 키워서 인식률을 높이고, 좌표는 원본 기준으로 돌려준다."""
    big = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else image
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ok, buf = cv2.imencode(".png", big)
        buf.tofile(path)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS1, "-Path", path, "-Lang", lang],
            capture_output=True, timeout=30)
        out = proc.stdout.decode("utf-8", "replace").strip()
        if proc.returncode != 0 or not out:
            raise RuntimeError("OCR 실패: " + proc.stderr.decode("utf-8", "replace")[:300])
        data = json.loads(out)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return [{"text": d["text"], "x": d["x"] / scale, "y": d["y"] / scale,
             "w": d["w"] / scale, "h": d["h"] / scale} for d in data]
