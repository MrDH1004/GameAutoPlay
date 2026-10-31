"""NPC 대화·컷신 버튼 종류별 입력 검증."""
import types
import unittest
from unittest.mock import patch

from bot2.brain import Bot


def make_bot(method="key"):
    calls = []
    bot = types.SimpleNamespace(
        cfg={"dialog": {"method": method, "key": "esc", "gap": 0.6}},
        _last_dialog_click=0.0,
        _fails=2,
        _press=lambda label, key: calls.append(("key", label, key)),
        _click_frame=lambda label, x, y: calls.append(("click", label, x, y)),
    )
    bot.on_dialog = types.MethodType(Bot._on_dialog, bot)
    return bot, calls


def snap(name):
    return types.SimpleNamespace(
        dialog_name=name,
        dialog=types.SimpleNamespace(score=0.95, x=100, y=50),
    )


class DialogActionTest(unittest.TestCase):
    def test_skip_uses_esc_only_after_template_match(self):
        bot, calls = make_bot()
        bot.on_dialog(snap("대화 넘기기"), 1.0)
        self.assertEqual(calls, [("key", "대화 넘기기", "esc")])

    def test_cutscene_uses_esc_even_if_dialog_method_is_click(self):
        bot, calls = make_bot(method="click")
        bot.on_dialog(snap("컷신 스킵"), 1.0)
        self.assertEqual(calls, [("key", "컷신 스킵", "esc")])

    def test_next_button_is_clicked(self):
        bot, calls = make_bot()
        bot.on_dialog(snap("대화 다음"), 1.0)
        self.assertEqual(calls, [("click", "대화 다음", 100, 50)])

    @patch("tools.ocr.read_text")
    def test_scene_skip_checks_today_and_confirms(self, read_text):
        read_text.return_value = [
            {"text": "장면 넘기기", "x": 800, "y": 370, "w": 120, "h": 25},
            {"text": "오늘 하루 안 보기", "x": 900, "y": 590, "w": 150, "h": 24},
            {"text": "확인", "x": 1035, "y": 655, "w": 45, "h": 24},
        ]
        calls = []
        bot = types.SimpleNamespace(
            _last_scene_skip_confirm=0.0,
            _click_frame=lambda label, x, y: calls.append((label, x, y)),
            window=types.SimpleNamespace(grab=lambda: object()),
            _scene_checkbox_checked=lambda _frame, _x, _y: True,
        )
        handled = Bot._try_scene_skip_confirm(bot, object(), 10.0)
        self.assertTrue(handled)
        self.assertEqual(calls, [
            ("오늘 하루 안 보기", 868, 602),
            ("장면 넘기기 확인", 1057, 667),
        ])

    @patch("tools.ocr.read_text")
    def test_scene_skip_does_not_confirm_until_checkbox_is_checked(self, read_text):
        read_text.return_value = [
            {"text": "장면 넘기기", "x": 800, "y": 370, "w": 120, "h": 25},
            {"text": "오늘 하루 안 보기", "x": 900, "y": 590, "w": 150, "h": 24},
            {"text": "확인", "x": 1035, "y": 655, "w": 45, "h": 24},
        ]
        calls = []
        bot = types.SimpleNamespace(
            _last_scene_skip_confirm=0.0,
            _click_frame=lambda label, x, y: calls.append(label),
            window=types.SimpleNamespace(grab=lambda: object()),
            _scene_checkbox_checked=lambda _frame, _x, _y: False,
        )
        self.assertTrue(Bot._try_scene_skip_confirm(bot, object(), 10.0))
        self.assertEqual(calls, ["오늘 하루 안 보기", "오늘 하루 안 보기"])


if __name__ == "__main__":
    unittest.main()
