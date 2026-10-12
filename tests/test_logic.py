"""저장된 실제 게임 화면으로 인식/판단 로직을 검사한다 (게임 없이 실행 가능).

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import os
import sys
import threading
import time
import unittest

import cv2
import numpy as np
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.vision import Matcher  # noqa: E402
from bot2 import farm, safety, selfcheck  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def img(name):
    return cv2.imdecode(np.fromfile(os.path.join(FIX, name), dtype=np.uint8), cv2.IMREAD_COLOR)


def config():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


class ParseTest(unittest.TestCase):
    def test_parse_k(self):
        self.assertEqual(farm.parse_k("51K 84.5K 1.2M"), [51000, 84500, 1200000])
        self.assertEqual(farm.parse_k("8,760 8,790"), [8760, 8790])

    def test_clean_power(self):
        self.assertEqual(farm.clean_power(451000), 51000)      # 아이콘이 '4'로 읽힘
        self.assertEqual(farm.clean_power(4150000), 150000)
        self.assertEqual(farm.clean_power(8415000), 84500)     # '.'이 '1'로 읽힘
        self.assertEqual(farm.clean_power(110000), 110000)

    def test_consensus(self):
        self.assertEqual(farm.consensus([38500, 5000, 385000, 38500]), 38500)
        self.assertEqual(farm.consensus([16500, 16500, 15500]), 16500)
        self.assertIsNone(farm.consensus([13500]))


class ScreenTest(unittest.TestCase):
    """OCR(Windows 내장)을 쓰므로 수 초 걸린다."""

    def test_read_power(self):
        self.assertEqual(farm.read_power(img("field_power_63099.png")), 63099)

    def test_monster_powers_white(self):
        f = img("map_spot_white_51k.png")
        self.assertEqual(farm.read_monster_powers(f), [51000] * 4)
        self.assertLess(farm.red_ratio(f), 0.01)

    def test_monster_powers_red(self):
        f = img("map_spot_red_84k.png")
        self.assertEqual(farm.consensus(farm.read_monster_powers(f)), 84500)
        self.assertGreater(farm.red_ratio(f), 0.01)

    def test_dialog_skip_button(self):
        m = Matcher(os.path.join(ROOT, "templates"), 0.85)
        hit = m.find(img("dialog_skip.png"), "dialog_skip.png", [0.9, 0.0, 0.1, 0.15])
        self.assertIsNotNone(hit)
        self.assertGreater(hit.x, 1700)

    def test_map_open(self):
        class D(farm.Driver):
            pass
        nav = farm.MapNav(D())
        self.assertTrue(nav.map_open(img("map_open.png")))
        self.assertFalse(nav.map_open(img("field_power_63099.png")))


class FarmPickTest(unittest.TestCase):
    def setUp(self):
        class FakeBot:
            stop_event = threading.Event()
            window = None

            def _alert(self, *a):
                pass
        self.t = farm.FarmTask(FakeBot(), {"margin": 0.9})

    def test_pick_by_power(self):
        self.assertEqual(self.t.pick(63099, 0)["power"], 51000)
        self.assertLessEqual(self.t.pick(30000, 0)["power"], 27000)
        self.assertEqual(self.t.pick(100000, 0)["power"], 84500)

    def test_death_steps_down(self):
        self.t.current = self.t.pick(63099, 0)
        self.t.on_death(0)
        self.assertLess(self.t.pick(63099, 10)["power"], 51000)
        self.assertEqual(self.t.pick(63099, 10 ** 6)["power"], 51000)    # 제외 시간이 지나면 복귀


class DeathBookTest(unittest.TestCase):
    def test_escalation_and_gate(self):
        b = safety.DeathBook({"block_seconds": 100, "max_block_seconds": 1000, "gate_deaths": 2, "gate_gain": 0.03})
        safety.DEATH_LOG = os.path.join(ROOT, "logs", "_test_deaths.jsonl")
        s1, n1 = b.record("본편", 60000)
        s2, n2 = b.record("본편", 60000)
        s3, _ = b.record("본편", 60000)
        self.assertEqual((s1, s2, s3), (100, 200, 400))
        self.assertTrue(b.gate_closed("본편", 61000))       # 1.7% 상승 → 아직 닫힘
        self.assertFalse(b.gate_closed("본편", 62000))      # 3.3% 상승 → 열림
        self.assertFalse(b.gate_closed("서사", 1))
        os.remove(safety.DEATH_LOG)


class SelfCheckTest(unittest.TestCase):
    def test_config_ok(self):
        cfg = config()
        self.assertEqual(selfcheck.validate_config(cfg), [])
        self.assertEqual(selfcheck.missing_templates(cfg), [])

    def test_config_bad(self):
        cfg = config()
        cfg["loop_interval"] = "빠르게"
        cfg["death"]["block_seconds"] = 5
        self.assertEqual(len(selfcheck.validate_config(cfg)), 2)


if __name__ == "__main__":
    unittest.main()
