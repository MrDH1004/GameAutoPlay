"""시작 전 점검: 설정값 검증, 템플릿 파일 확인, 오래된 임시 파일 정리."""
import glob
import logging
import os
import time

log = logging.getLogger(__name__)

from app_paths import ROOT
TEMPLATE_DIR = os.path.join(ROOT, "templates")

# (경로, 타입, 최소, 최대)
RULES = [
    (("loop_interval",), (int, float), 0.1, 5),
    (("match_threshold",), (int, float), 0.5, 0.99),
    (("user_idle_resume",), int, 1, 3600),
    (("idle", "seconds"), int, 1, 600),
    (("death", "block_seconds"), int, 30, 86400),
    (("death", "streak_limit"), int, 1, 100),
    (("farm", "margin"), (int, float), 0.1, 2.0),
    (("farm", "reselect_minutes"), (int, float), 1, 1440),
    (("yeonggi", "interval"), int, 10, 86400),
    (("stall", "minutes"), (int, float), 1, 240),
]


def _get(cfg, path):
    for k in path:
        if not isinstance(cfg, dict) or k not in cfg:
            return None
        cfg = cfg[k]
    return cfg


def validate_config(cfg):
    """문제 목록(문자열)을 돌려준다. 빈 리스트면 정상."""
    problems = []
    if not isinstance(cfg, dict):
        return ["설정 최상위 값은 객체여야 함"]
    for path, typ, lo, hi in RULES:
        v = _get(cfg, path)
        if v is None:
            continue
        name = ".".join(path)
        if isinstance(v, bool) or not isinstance(v, typ):
            problems.append(f"{name}: 숫자여야 함 (지금 {v!r})")
        elif not lo <= v <= hi:
            problems.append(f"{name}: {lo}~{hi} 범위 밖 (지금 {v})")
    for key in ("window_title", "quest_tracker", "idle"):
        if key not in cfg:
            problems.append(f"{key}: 필수 항목 없음")
    if "window_title" in cfg and (not isinstance(cfg["window_title"], str) or not cfg["window_title"].strip()):
        problems.append("window_title: 게임 창 제목은 비어 있지 않은 문자열이어야 함")
    for key in ("quest_tracker", "idle", "death"):
        if key in cfg and not isinstance(cfg[key], dict):
            problems.append(key + ": 설정 객체여야 함")
    return problems


def _template_names(cfg):
    names = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and x.lower().endswith(".png"):
            names.add(x)
    walk(cfg)
    return names


def missing_templates(cfg):
    # v1(main.py) 전용 팝업 목록은 v2에서 '대화 넘기기'만 쓰므로 나머지는 검사하지 않는다
    cfg = dict(cfg)
    cfg["popups"] = [p for p in cfg.get("popups", []) if p.get("name") == "대화 넘기기"]
    return sorted(n for n in _template_names(cfg) if not os.path.exists(os.path.join(TEMPLATE_DIR, n)))


def cleanup(days_bridge=1, days_shots=7):
    """브리지 응답 이미지(하루 지난 것)와 오래된 자동 저장 화면을 지운다. 지운 개수를 돌려준다."""
    now = time.time()
    n = 0
    targets = [(os.path.join(ROOT, "bridge", "out", "*"), days_bridge),
               (os.path.join(ROOT, "screenshots", "stuck_*.png"), days_shots),
               (os.path.join(ROOT, "screenshots", "unknown", "*.png"), days_shots),
               (os.path.join(ROOT, "screenshots", "stall_*.png"), days_shots),
               (os.path.join(ROOT, "screenshots", "death_*.png"), days_shots * 2)]
    for pattern, days in targets:
        for p in glob.glob(pattern):
            if os.path.basename(p).startswith("_status"):
                continue
            try:
                if now - os.path.getmtime(p) > days * 86400:
                    os.remove(p)
                    n += 1
            except OSError:
                pass
    return n


def run_all(cfg):
    """시작할 때 한 번. 결과를 로그로 남기고 (문제 수, 요약 문자열)을 돌려준다."""
    probs = validate_config(cfg)
    miss = missing_templates(cfg)
    removed = cleanup()
    for p in probs:
        log.warning("설정 점검: %s", p)
    if miss:
        log.warning("템플릿 파일 없음: %s", ", ".join(miss))
    if removed:
        log.info("오래된 임시 이미지 %d개 정리", removed)
    summary = f"설정 문제 {len(probs)}건 · 없는 템플릿 {len(miss)}개"
    log.info("시작 점검: %s", summary)
    return len(probs) + len(miss), summary
