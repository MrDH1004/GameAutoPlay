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

    def test_read_power_windowed(self):
        self.assertEqual(farm.read_power(img("field_windowed_upscaled.png")), 66537)    # 1360x768 창 모드 확대

    def test_menu_titles(self):
        from bot2.rewards import _seen, _tpl
        box = (0, 0, 400, 90)
        self.assertTrue(_seen(img("mail_open.png"), _tpl("mail_title.png"), box))
        self.assertTrue(_seen(img("achievement_open.png"), _tpl("achv_title.png"), box))
        self.assertTrue(_seen(img("dosul_open.png"), _tpl("dosul_title.png"), box))
        self.assertTrue(_seen(img("enhance_no_material.png"), _tpl("growth_title.png"), box))
        self.assertFalse(_seen(img("field_power_63099.png"), _tpl("mail_title.png"), box))
        self.assertFalse(_seen(img("mail_open.png"), _tpl("achv_title.png"), box))

    def test_map_open(self):
        class D(farm.Driver):
            pass
        nav = farm.MapNav(D())
        self.assertTrue(nav.map_open(img("map_open.png")))
        self.assertFalse(nav.map_open(img("field_power_63099.png")))


class TrackerAndDigitsTest(unittest.TestCase):
    def test_tracker_text_mask_static(self):
        from bot2.perception import text_mask_diff, tracker_text_mask
        f = img("field_power_63099.png")
        a = tracker_text_mask(f, [0.80, 0.05, 0.155, 0.40])
        self.assertEqual(text_mask_diff(a, a.copy()), 0.0)
        same = tracker_text_mask(img("field_hp_95.png"), [0.80, 0.05, 0.155, 0.40])    # 배경만 다르고 목록은 같음
        self.assertLess(text_mask_diff(a, same), 0.3)
        other = tracker_text_mask(img("field_windowed_upscaled.png"), [0.80, 0.05, 0.155, 0.40])   # 목록이 다름
        self.assertGreater(text_mask_diff(a, other), 0.3)

    def test_digit_segments(self):
        from bot2.digits import segment_number
        crop = farm._crop(img("field_power_63099.png"), farm.POWER_BOX)
        self.assertEqual(len(segment_number(crop)), 5)                # 63,099 → 쉼표 빼고 5글자

    def test_yeonggi_remaining(self):
        from bot.yeonggi import parse_remaining
        left = parse_remaining(img("yeonggi_progress.png"), 705, 981)     # 화면에 '9분 57초'
        self.assertIsNotNone(left)
        self.assertTrue(500 <= left <= 600, left)

    def test_potion_count(self):
        from bot2.shop import purchase_quantity, read_potion_count
        self.assertEqual(read_potion_count(img("potion_ctrl1_250.png")), 250)     # 물약이 Ctrl+1 칸에 있을 때
        self.assertEqual(purchase_quantity(50), 200)
        self.assertEqual(purchase_quantity(5), 200)
        self.assertEqual(purchase_quantity(51), 0)       # 200개를 전부 담을 수 없으면 전체 구매하지 않음

    def test_shop_templates(self):
        from bot2.rewards import _seen, _tpl
        self.assertTrue(_seen(img("shop_open.png"), _tpl("shop_title.png"), (0, 0, 480, 90)))
        self.assertTrue(_seen(img("shop_qty.png"), _tpl("shop_qty_title.png"), (760, 340, 1160, 420)))
        self.assertTrue(_seen(img("map_saved_list.png"), _tpl("map_saved_potion.png"), (100, 130, 410, 900)))
        self.assertFalse(_seen(img("field_power_63099.png"), _tpl("shop_title.png"), (0, 0, 480, 90)))

    def test_skill_count(self):
        from bot2.rewards import read_skill_count
        self.assertEqual(read_skill_count(img("achievement_open.png")), 26)


class DeathRetryTest(unittest.TestCase):
    def test_base_revive_button_and_quest_name(self):
        import types
        from bot2.brain import Bot
        safety.DEATH_LOG = os.path.join(ROOT, "logs", "_test_deaths.jsonl")
        cfg = config()
        cfg["normal_mode"]["enabled"] = False
        b = Bot(cfg, dry_run=True)
        b._save_screen = lambda f, p: None
        f = img("death_base_revive.png")
        b._on_dead(types.SimpleNamespace(frame=f, state="DEAD"), 100)
        self.assertIsNotNone(b._retry_quest)                 # 1번째 사망 → 같은 퀘스트 재도전
        self.assertIn("길목을 막는 요귀", b._quest_name or "")
        self.assertFalse(b.mission.is_blocked("본편"))
        os.remove(safety.DEATH_LOG)


class HpTest(unittest.TestCase):
    def test_hp_ratio(self):
        from bot2.safety import hp_ratio
        self.assertGreater(hp_ratio(img("field_power_63099.png")), 0.95)     # 29,766/29,766
        self.assertAlmostEqual(hp_ratio(img("field_hp_95.png")), 0.95, delta=0.04)
        self.assertEqual(hp_ratio(img("map_open.png")), 0.0)
        f = img("field_power_63099.png")
        f[945:965, 958:1085] = (27, 16, 10)          # 바 오른쪽 절반을 빈 칸 색으로
        self.assertAlmostEqual(hp_ratio(f), 0.5, delta=0.03)


class FarmPickTest(unittest.TestCase):
    def setUp(self):
        class FakeBot:
            stop_event = threading.Event()
            window = None

            def _alert(self, *a):
                pass
        self.t = farm.FarmTask(FakeBot(), {"margin": 0.9, "travel": "map"})
        self.g = farm.FarmTask(FakeBot(), {"margin": 0.9})

    def test_pick_by_power(self):
        self.assertEqual(self.t.pick(63099, 0)["power"], 51000)
        self.assertLessEqual(self.t.pick(30000, 0)["power"], 27000)
        self.assertEqual(self.t.pick(100000, 0)["power"], 84500)

    def test_guide_ladder(self):
        self.assertEqual(self.g.pick(66537, 0)["name"], "안개 기슭 지하 동굴")
        self.assertEqual(self.g.pick(80000, 0)["name"], "산신의 영역 1")
        self.assertEqual(self.g.pick(170000, 0)["power"], 150000)

    def test_guide_screen(self):
        class D(farm.Driver):
            pass
        g = farm.GuideNav(D())
        self.assertTrue(g.is_open(img("guide_open.png")))
        self.assertFalse(g.is_open(img("field_power_63099.png")))
        self.assertEqual(farm.read_number(img("guide_open.png"), farm.GUIDE_REC_BOX), 51000)

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

    def test_clan_help_template_matches_measured_icon(self):
        m = Matcher(os.path.join(ROOT, "templates"), 0.85)
        frame = cv2.imdecode(np.fromfile(os.path.join(ROOT, "tests", "fixtures", "clan_help_visible.png"),
                                        dtype=np.uint8), cv2.IMREAD_COLOR)
        found = m.find(frame, "clan_help.png", threshold=0.90)
        self.assertIsNotNone(found)
        self.assertGreater(found.score, 0.98)
        without_help = frame.copy()
        without_help[:75, :] = 0
        self.assertIsNone(m.find(without_help, "clan_help.png", threshold=0.90),
                          "아래쪽 보스 아이콘을 문파 도움으로 오인하면 안 됨")

    def test_config_bad(self):
        cfg = config()
        cfg["loop_interval"] = "빠르게"
        cfg["death"]["block_seconds"] = 5
        self.assertEqual(len(selfcheck.validate_config(cfg)), 2)


if __name__ == "__main__":
    unittest.main()
