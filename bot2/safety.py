"""사망 기록과 '전투력 게이트'.

  * 사망할 때마다 logs/deaths.jsonl 에 (시각, 퀘스트 출처, 전투력, 화면 파일)을 남긴다.
  * 같은 퀘스트에서 반복해서 죽으면 중단 시간을 2배씩 늘린다 (최대 max_block).
  * 전투력 게이트: 한 퀘스트에서 gate_deaths번 이상 죽었으면, 중단 시간이 끝나도
    전투력이 그때보다 gate_gain 이상 오르기 전에는 다시 시도하지 않는다 (최대 max_block 뒤에는 한 번 재시도).
"""
import json
import logging
import os
import time
from datetime import datetime

import numpy as np

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEATH_LOG = os.path.join(ROOT, "logs", "deaths.jsonl")


class DeathBook:
    def __init__(self, cfg):
        self.base = float(cfg.get("block_seconds", 1800))
        self.max_block = float(cfg.get("max_block_seconds", 4 * 3600))
        self.window = float(cfg.get("repeat_window", 3 * 3600))      # 이 시간 안의 사망만 반복으로 센다
        self.gate_deaths = int(cfg.get("gate_deaths", 2))
        self.gate_gain = float(cfg.get("gate_gain", 0.03))
        self.records = []           # (monotonic, source, power)
        self.gates = {}             # source → {"power": 사망 당시 전투력, "until": 강제 재시도 시각}

    def record(self, source, power, shot=None):
        now = time.monotonic()
        self.records.append((now, source, power))
        os.makedirs(os.path.dirname(DEATH_LOG), exist_ok=True)
        try:
            with open(DEATH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "source": source,
                                    "power": power, "shot": shot}, ensure_ascii=False) + "\n")
        except OSError as e:
            log.debug("사망 기록 실패: %s", e)
        n = self.recent(source, now)
        secs = min(self.base * (2 ** (n - 1)), self.max_block)
        if n >= self.gate_deaths and power:
            self.gates[source] = {"power": power, "until": now + self.max_block}
            log.warning("'%s'에서 %d번째 사망 → 전투력이 %s의 %.0f%% 이상 오를 때까지 재도전 보류",
                        source, n, f"{power:,}", (1 + self.gate_gain) * 100)
        return secs, n

    def recent(self, source, now=None):
        now = now or time.monotonic()
        return sum(1 for t, s, _ in self.records if s == source and now - t < self.window)

    def gate_closed(self, source, power):
        """전투력 게이트가 아직 닫혀 있으면 True (= 이 퀘스트는 진행하지 말 것)."""
        g = self.gates.get(source)
        if not g:
            return False
        if time.monotonic() >= g["until"]:
            log.info("'%s' 게이트: 최대 보류 시간이 지나 한 번 재도전", source)
            self.gates.pop(source, None)
            return False
        if power and power >= g["power"] * (1 + self.gate_gain):
            log.info("'%s' 게이트 열림: 전투력 %s → %s", source, f"{g['power']:,}", f"{power:,}")
            self.gates.pop(source, None)
            return False
        return True


def load_history(limit=500):
    """제어판 통계용: deaths.jsonl의 최근 기록."""
    if not os.path.exists(DEATH_LOG):
        return []
    out = []
    with open(DEATH_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out[-limit:]


# ---------- HP ----------
HP_ROW = 956                 # 하단 체력 바 가운데 줄 (1920x1080 기준)
HP_X0, HP_X1 = 835, 1080     # 체력 바 왼쪽/오른쪽 끝


def hp_ratio(frame):
    """하단 체력 바의 채워진 비율 (0~1). 바 색은 초록→청록 그라데이션. 필드 화면에서만 의미가 있다."""
    h, w = frame.shape[:2]
    sx, sy = w / 1920.0, h / 1080.0
    y = int(HP_ROW * sy)
    rows = frame[max(0, y - 2):y + 3, int(HP_X0 * sx) - 2:int(HP_X1 * sx) + 3].astype(int)
    b, g, r = rows[..., 0], rows[..., 1], rows[..., 2]
    fill = ((g > 150) & (g - b > 90)).any(axis=0)      # 초록~연두 (BGR에서 B가 낮음). 흰 글자·빈 바는 제외
    xs = np.where(fill)[0]
    if len(xs) == 0:
        return 0.0
    span = (HP_X1 - HP_X0) * sx
    return float(min(1.0, max(0.0, (xs.max() - 2) / span)))
