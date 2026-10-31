import unittest
from unittest.mock import MagicMock, patch

from bot2.notify import Notifier


class NotifyResourceTest(unittest.TestCase):
    def test_text_response_is_closed(self):
        notifier = Notifier({
            "enabled": True,
            "telegram": {"token": "token", "chat_id": "1"},
        })
        response = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = response

        with patch("bot2.notify.urllib.request.urlopen", return_value=context):
            notifier._send("확인", None)

        response.read.assert_called_once_with()
        context.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
