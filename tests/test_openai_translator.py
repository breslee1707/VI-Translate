import os
import unittest
from unittest.mock import MagicMock, patch

from pdf2zh.translator import OpenAITranslator


class OpenAITranslatorTests(unittest.TestCase):
    def test_openai_translator_initialization(self):
        translator = OpenAITranslator(
            lang_in="en",
            lang_out="vi",
            model="gpt-4o-mini",
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        )
        self.assertEqual(translator.name, "openai")
        self.assertEqual(translator.model, "gpt-4o-mini")
        self.assertEqual(translator.api_key, "test-key")
        self.assertEqual(translator.base_url, "https://api.openai.com/v1")

    @patch("requests.Session.post")
    def test_openai_translator_do_translate_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Xin chào thế giới <b0></b0>"}}
            ]
        }
        mock_post.return_value = mock_response

        translator = OpenAITranslator(
            lang_in="en",
            lang_out="vi",
            api_key="test-key",
            ignore_cache=True,
        )

        result = translator.do_translate("Hello world <b0></b0>")
        self.assertEqual(result, "Xin chào thế giới <b0></b0>")
        mock_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
