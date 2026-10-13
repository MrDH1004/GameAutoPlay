"""화첩/유물 수집: 실제 화면(fixtures)으로 읽기 함수와 후보 정렬을 검증."""
import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot2 import collect as c  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")


def load(name):
    return cv2.imread(os.path.join(FIX, name))


class ReadTest(unittest.TestCase):
    def test_painting_have_and_register_pill(self):
        self.assertEqual(c.painting_have(load("album_rat_registrable.png")), 0)
        self.assertEqual(c.painting_have(load("album_deer_3of9.png")), 3)
        self.assertTrue(c.has_register_pill(load("album_rat_registrable.png")))
        self.assertFalse(c.has_register_pill(load("album_deer_3of9.png")))

    def test_red_dots(self):
        covers = [c.red_dot(load("album_covers.png"), b) for b in c.BOOK_DOT]
        self.assertEqual(covers, [False, True, False, False, True, True])   # 동물화/도깨비화/요귀화에 알림
        menu = load("menu_album_dot.png")
        self.assertTrue(c.red_dot(menu, c.MENU_DOT["album"]))
        self.assertFalse(c.red_dot(menu, c.MENU_DOT["relic"]))

    def test_menu_open_template(self):
        col = c.Collector(None)
        self.assertTrue(col.menu_open(load("menu_album_dot.png")))
        self.assertFalse(col.menu_open(load("field_hp_95.png")))

    def test_read_monsters(self):
        ms = c.read_monsters(load("relic_bowl_monsters.png"))
        self.assertEqual(len(ms), 8)
        self.assertTrue(all(m["power"] == 68000 and not m["red"] for m in ms))
        self.assertIn("호랑이", ms[0]["name"])
        rats = c.read_monsters(load("album_rat_monsters_red.png"))
        self.assertTrue(all(m["red"] for m in rats))                       # 우물 몬스터는 모두 빨간 이름

    def test_parse_badge(self):
        self.assertEqual(c.parse_badge("15.5K"), 15500)
        self.assertEqual(c.parse_badge("9000"), 9000)
        self.assertEqual(c.parse_badge("91000"), 9000)
        self.assertEqual(c.parse_badge("1170K"), 170000)
        self.assertIsNone(c.parse_badge("가나다"))


class CandidatesTest(unittest.TestCase):
    def db(self):
        def mon(name, p, red=False, conf=3):
            return {"name": name, "region": "", "power": p, "red": red, "conf": conf}
        return {"album": [
            {"book": 1, "idx": 0, "name": "곰", "done": False, "monsters": [mon("약한곰", 17000), mon("센곰", 215000, True)]},
            {"book": 1, "idx": 1, "name": "봉", "done": False, "monsters": [mon("약한곰", 17000), mon("호랑이", 68000)]},
            {"book": 1, "idx": 2, "name": "끝", "done": True, "monsters": [mon("완성몹", 10000)]},
            {"book": 1, "idx": 3, "name": "경계", "done": False, "monsters": [mon("경계몹", 120000, True), mon("오독몹", 15000, True, 1)]},
        ], "relics": []}

    def test_order_and_limits(self):
        out = c.candidates(self.db(), 65000, 2.0)
        names = [m["name"] for _, _, m, _ in out]
        self.assertEqual(names[0], "약한곰")                  # 두 그림에 필요 → 안전 구간에서 1순위
        self.assertIn("호랑이", names)
        self.assertIn("경계몹", names)                        # 내 전투력 초과 ~ 2배 이하
        self.assertLess(names.index("호랑이"), names.index("경계몹"))   # 안전 구간이 먼저
        self.assertNotIn("센곰", names)                       # 2배 초과(215K)
        self.assertNotIn("완성몹", names)                     # 완성된 그림
        self.assertNotIn("오독몹", names)                     # 빨간 이름 + 확실치 않은 숫자
        self.assertEqual(c.candidates(self.db(), 65000, 2.0, banned={("약한곰", "")})[0][2]["name"], "호랑이")
        self.assertEqual(c.candidates(self.db(), None), [])


class UpdateHaveTest(unittest.TestCase):
    def test_have_never_decreases(self):
        db = {"album": [{"book": 1, "idx": 0, "have": 9, "done": True}, {"book": 1, "idx": 1, "have": 0, "done": False}]}
        c.Collector._update_have(db, 1, 0, 0)               # 연출 화면 오독(9→0)은 무시
        self.assertEqual((db["album"][0]["have"], db["album"][0]["done"]), (9, True))
        c.Collector._update_have(db, 1, 1, 9)               # 등록으로 늘어난 값은 반영 → 완성
        self.assertEqual((db["album"][1]["have"], db["album"][1]["done"]), (9, True))



class RotationTest(unittest.TestCase):
    def make_task(self):
        t = c.CollectTask.__new__(c.CollectTask)
        t.done_round, t.banned = set(), {}
        t.min_hunt, t.max_hunt, t.hunt_since = 180.0, 1500.0, 100.0
        t.current = {"name": "몬스터A", "region": ""}
        return t

    def test_switch_rules(self):
        t = self.make_task()
        snap = type("S", (), {"frame": object()})()
        t.cfg = {}
        t.piece_in_chat = lambda frame: True
        self.assertFalse(t.should_switch(snap, 150))          # 최소 사냥 시간 전에는 안 바꿈
        self.assertFalse(t.should_switch(snap, 400))          # 채팅 감지는 기본 꺼짐 → 시간 전에는 계속
        self.assertTrue(t.should_switch(snap, 100 + 1500))    # 상한 시간이 지나면 교대
        t.cfg = {"detect_piece_in_chat": True}
        self.assertTrue(t.should_switch(snap, 400))           # 켜면 조각이 보일 때 교대

    def test_done_round_excluded_from_candidates(self):
        t = self.make_task()
        t.next_target()
        self.assertIsNone(t.current)
        self.assertEqual(t.done_round, {("몬스터A", "")})
        db = {"album": [{"book": 1, "idx": 0, "name": "곰", "done": False, "monsters": [
            {"name": "몬스터A", "region": "", "power": 17000, "red": False, "conf": 3},
            {"name": "몬스터B", "region": "", "power": 20000, "red": False, "conf": 3}]}], "relics": []}
        names = [m["name"] for _, _, m, _ in c.candidates(db, 65000, 2.0, banned=t.done_round)]
        self.assertEqual(names, ["몬스터B"])                    # 방금 사냥한 몬스터는 이번 순회에서 빠짐


if __name__ == "__main__":
    unittest.main()
