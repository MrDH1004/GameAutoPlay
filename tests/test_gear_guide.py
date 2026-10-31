"""추천 탭 데이터: 모든 항목에 출처([웹]/[실측])가 붙어 있고, 아이템 이름을 지어내지 않는지."""
import os
import json
import struct

os.environ.setdefault("DOKKAEBI_TEST", "1")      # 테스트가 실제 로그 파일에 쓰지 않게
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gear_guide as g  # noqa: E402
import skill_guide as skills  # noqa: E402


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
    def test_observed_owned_skill_names_exist_in_catalog(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "assets", "skills", "catalog.json"), encoding="utf-8") as f:
            catalog_names = {row["name"] for row in json.load(f)["skills"]}
        self.assertTrue(skills.OBSERVED_OWNED_SKILLS <= catalog_names)
        self.assertIn("요혼격", skills.OBSERVED_OWNED_SKILLS)
        self.assertNotIn("요호격", skills.OBSERVED_OWNED_SKILLS)

    def test_every_measured_skill_has_a_transparent_icon(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_dir = os.path.join(root, "assets", "skills")
        with open(os.path.join(icon_dir, "index.json"), encoding="utf-8") as f:
            index = json.load(f)
        for row in skills.UNLOCKED_SKILLS:
            name = row[0]
            self.assertIn(name, index, f"도술 아이콘 색인 누락: {name}")
            path = os.path.join(icon_dir, index[name])
            self.assertTrue(os.path.isfile(path), f"도술 아이콘 파일 누락: {name}")
            with open(path, "rb") as image:
                header = image.read(26)
            self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
            width, height, _depth, color_type = struct.unpack(">IIBB", header[16:26])
            self.assertEqual((width, height, color_type), (64, 64, 6), name)

    def test_recommended_sets_fit_the_eight_game_slots(self):
        safe = next(row for row in skills.SETS if row["title"].startswith("안전한 광역 사냥"))
        self.assertTrue(all(len(row["skills"]) == 8 for row in skills.SETS))
        unlocked = {row[0] for row in skills.UNLOCKED_SKILLS}
        self.assertTrue(set(safe["skills"]) <= unlocked)
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "assets", "skills", "index.json"), encoding="utf-8") as f:
            icons = json.load(f)
        self.assertIn("대폭발", icons)
        self.assertIn("서리의 잔향", icons)  # 전체 도감 아이콘 자동 수집

    def test_newly_learned_aoe_set_uses_four_new_skills(self):
        learned = {"영혼 소용돌이", "공간 결계", "방패 치기", "생명의 축복"}
        build = next(row for row in skills.SETS if row["title"].startswith("신규 습득 광역 생존 사냥"))
        self.assertEqual(len(build["skills"]), 8)
        self.assertTrue(learned <= set(build["skills"]))
        self.assertEqual({row[0] for row in skills.NEWLY_LEARNED_SKILLS}, learned)
        space = next(row for row in skills.NEWLY_LEARNED_SKILLS if row[0] == "공간 결계")
        self.assertEqual(space[3:6], ("19.9%×4", "15초", "5.5"))
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "assets", "skills", "index.json"), encoding="utf-8") as f:
            icons = json.load(f)
        self.assertTrue(learned <= set(icons))

    def test_kkaebigg_builds_and_owned_adaptations(self):
        self.assertEqual(skills.KKAEBIGG_META["skills"], 105)
        self.assertEqual(skills.KKAEBIGG_META["styles"], 12)
        self.assertGreaterEqual(len(skills.KKAEBIGG_BUILDS), 5)
        self.assertTrue(all(len(row["skills"]) == 8 for row in skills.KKAEBIGG_BUILDS))
        self.assertTrue(all(row["likes"] > 0 for row in skills.KKAEBIGG_BUILDS))
        owned = {row[0] for row in skills.UNLOCKED_SKILLS} | {row[0] for row in skills.NEWLY_LEARNED_SKILLS}
        adapted = [row for row in skills.SETS if row["title"].startswith("깨비지지")]
        self.assertEqual(len(adapted), 2)
        self.assertTrue(all(len(row["skills"]) == 8 for row in adapted))
        self.assertTrue(all(set(row["skills"]) <= owned for row in adapted))

    def test_tab_renamed_and_has_recommendation_subtabs(self):
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
        self.assertEqual([rec.tabText(i) for i in range(rec.count())],
                         ["스킬", "무과금", "보유 스킬", "구매 추천", "보물찾기"])
        from PySide6.QtWidgets import QPushButton
        buttons = [button.text() for button in rec.widget(0).findChildren(QPushButton)]
        self.assertIn("CPU 시뮬레이션 다시 실행", buttons)
        from PySide6.QtWidgets import QTextBrowser
        skill_browser = rec.widget(0).findChild(QTextBrowser)
        self.assertIsInstance(skill_browser, gui.InstantTooltipBrowser)
        skill_html = skill_browser.toHtml()
        self.assertIn("유형:", skill_html)
        self.assertIn("기본 공격계수:", skill_html)
        self.assertIn("HP 부족 시 교체", skill_html)
        self.assertIn("상세 보기", skill_browser.toPlainText())
        self.assertNotIn("사용 순서 설정", skill_browser.toPlainText())
        self.assertIn("생명의 축복", skill_html)
        self.assertIn("예상 데미지:", skill_html)
        self.assertIn("8개 교체안 중 데미지 손실 최소", skill_html)
        self.assertIn(">궁<", skill_html)
        self.assertIn("도술: 폭주", skill_html)
        self.assertIn("파멸", skill_html)
        from PySide6.QtCore import QUrl
        skill_browser._open_anchor(QUrl("skilldetail:d0"))
        self.assertIn("사용 순서 설정", skill_browser.toPlainText())
        self.assertIn("도술 성장 우선순위", skill_browser.toPlainText())
        f2p_html = rec.widget(1).findChild(QTextBrowser).toPlainText()
        self.assertIn("무과금 집중 육성 시뮬레이션", f2p_html)
        self.assertIn("2계열 집중", f2p_html)
        self.assertIn("3계열 집중", f2p_html)
        self.assertEqual(len(w.skill_owned_checks), 105)
        self.assertEqual(len(w.skill_owned_rows), 12)
        self.assertEqual(w.skill_owned_order["절명"][:4],
                         ["찌르기", "연속 찌르기", "약점 베기", "삼단 찌르기"])
        self.assertEqual(w.skill_owned_order["연타"][:3],
                         ["난타", "회전 난격", "집중 연타"])
        self.assertEqual(w.skill_owned_order["강타"][:3],
                         ["대검 강타", "돌풍 강격", "내려치기(강타)"])
        self.assertEqual(w.skill_owned_order["척살"][:3],
                         ["묵영점정", "용사비등", "먹 베기"])
        self.assertEqual(w.skill_owned_order["저격"][:3],
                         ["꿰뚫는 저격", "사격", "이중 사격"])
        self.assertEqual(w.skill_owned_order["주술"][:2],
                         ["영혼 소용돌이", "부적 던지기"])
        self.assertEqual(w.skill_owned_order["파멸"][:3],
                         ["화염 분출", "붕괴하는 대지", "번개 폭풍"])
        self.assertEqual(w.skill_owned_order["강인"][:3],
                         ["방패 치기", "전투의 함성", "방패 밀기"])
        self.assertTrue(w.skill_owned_checks["찌르기"].isChecked())
        self.assertTrue(w.skill_owned_checks["부적 던지기"].isChecked())
        self.assertEqual(w.skill_owned_checks["찌르기"].text(), "")
        self.assertIn("찌르기", w.skill_owned_checks["찌르기"].toolTip())
        self.assertFalse(w.skill_owned_checks["찌르기"].badge.isHidden())
        self.assertAlmostEqual(w.skill_owned_checks["찌르기"].icon_opacity.opacity(), 1.0)
        self.assertTrue(w.skill_owned_checks["은신"].badge.isHidden())
        self.assertAlmostEqual(w.skill_owned_checks["은신"].icon_opacity.opacity(), 0.28)
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        owned_icon = w.skill_owned_checks["찌르기"]
        QTest.mouseClick(owned_icon, Qt.LeftButton, pos=owned_icon.rect().center())
        self.assertFalse(owned_icon.isChecked())
        self.assertTrue(owned_icon.badge.isHidden())
        self.assertAlmostEqual(owned_icon.icon_opacity.opacity(), 0.28)
        QTest.mouseClick(owned_icon, Qt.LeftButton, pos=owned_icon.rect().center())
        self.assertTrue(owned_icon.isChecked())
        self.assertFalse(owned_icon.badge.isHidden())
        self.assertAlmostEqual(owned_icon.icon_opacity.opacity(), 1.0)
        for checkbox in w.skill_owned_regulars["절명"]:
            checkbox.setChecked(False)
        ultimate = w.skill_owned_ultimates["절명"]
        self.assertFalse(ultimate.isEnabled())
        self.assertFalse(ultimate.isChecked())
        for checkbox in w.skill_owned_regulars["절명"][:3]:
            checkbox.setChecked(True)
        self.assertTrue(ultimate.isChecked())
        w.skill_owned_regulars["절명"][0].setChecked(False)
        self.assertFalse(ultimate.isChecked())
        purchase_html = rec.widget(3).findChild(QTextBrowser).toHtml()
        self.assertIn("현자 단각 구매 추천", purchase_html)
        self.assertIn("상점 구매 우선순위", purchase_html)
        self.assertIn("기동덱 비교", purchase_html)
        w.close()
        w.deleteLater()


if __name__ == "__main__":
    unittest.main()
