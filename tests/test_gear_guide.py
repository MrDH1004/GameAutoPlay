"""추천 탭 데이터: 모든 항목에 출처([웹]/[실측])가 붙어 있고, 아이템 이름을 지어내지 않는지."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gear_guide as g  # noqa: E402


def tagged(text):
    return text.startswith("[웹]") or text.startswith("[실측]")


class GearGuideTest(unittest.TestCase):
    def test_every_sentence_has_a_source_tag(self):
        for name, data in (("무기", g.WEAPON), ("방어구", g.ARMOR)):
            for t in data["principles"] + data.get("measured", []):
                self.assertTrue(tagged(t), f"{name}: 출처 표시 없음 → {t[:30]}")
        for t in g.POWER_GUIDE["measured"]:
            self.assertTrue(tagged(t), f"투력: 출처 표시 없음 → {t[:30]}")

    def test_materials_and_steps_have_valid_source(self):
        for _name, _where, src in g.WEAPON["materials"]:
            self.assertIn(src, ("웹", "실측"))
        for n, title, text, src in g.POWER_GUIDE["steps"]:
            self.assertIn(src, ("웹", "실측"))
            self.assertTrue(title and text)
        self.assertEqual([s[0] for s in g.POWER_GUIDE["steps"]], list(range(1, len(g.POWER_GUIDE["steps"]) + 1)))

    def test_no_invented_item_list(self):
        self.assertIn("임의로 만들어 넣지 않았습니다", g.NO_ITEM_LIST)       # 아이템 이름을 지어내지 않았다는 안내가 남아 있음
        for forbidden in ("+10 ", "전설 무기", "신화 무기", "추천 무기:"):
            self.assertNotIn(forbidden, str(g.WEAPON) + str(g.ARMOR))

    def test_ladder_comes_from_the_bot_constants(self):
        from bot2.farm import GUIDE_NODES
        ladder = g.ladder()
        self.assertEqual(len(ladder), len(GUIDE_NODES))
        self.assertEqual(ladder, sorted(ladder, key=lambda r: r[1]))          # 권장 전투력 오름차순

    def test_sources_are_urls(self):
        self.assertTrue(all(u.startswith("https://") for _t, u, _n in g.SOURCES))


class RecommendTabTest(unittest.TestCase):
    def test_tab_renamed_and_has_four_subtabs(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import gui
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        w = gui.Window()
        names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        self.assertIn("추천", names)
        self.assertNotIn("스킬 추천", names)
        rec = w.tabs.widget(names.index("추천"))
        self.assertEqual([rec.tabText(i) for i in range(rec.count())], ["스킬", "무기", "방어구", "투력 공략집"])
        w.close()
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()
