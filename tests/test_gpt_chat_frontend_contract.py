import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GPT_CHAT_HTML = ROOT / "static" / "gpt-chat.html"
I18N_JS = ROOT / "static" / "js" / "i18n.js"


class GptChatFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = GPT_CHAT_HTML.read_text(encoding="utf-8")

    def test_bundled_markdown_parser_and_sanitizer_are_loaded(self):
        self.assertIn("/static/vendor/js/marked-15.0.12.min.js", self.source)
        self.assertIn("/static/vendor/js/dompurify-3.2.6.min.js", self.source)
        self.assertTrue((ROOT / "static/vendor/js/marked-15.0.12.min.js").is_file())
        self.assertTrue((ROOT / "static/vendor/js/dompurify-3.2.6.min.js").is_file())

    def test_assistant_messages_render_sanitized_markdown(self):
        self.assertIn("function renderAssistantMarkdown(target, content)", self.source)
        self.assertIn("marked.parse(source, {gfm:true, breaks:true})", self.source)
        self.assertIn("DOMPurify.sanitize(parsed", self.source)
        self.assertIn("FORBID_TAGS:['style','iframe','object','embed','form','input','button','textarea','select','option']", self.source)
        self.assertIn("text.classList.add('markdown-body')", self.source)

    def test_streaming_and_history_use_the_same_markdown_renderer(self):
        self.assertGreaterEqual(self.source.count("renderAssistantMarkdown("), 3)
        self.assertIn("renderAssistantMarkdown(assistantBubble.text, fullText)", self.source)

    def test_user_messages_remain_plain_text(self):
        self.assertIn("text.textContent = msg.type === 'image'", self.source)

    def test_markdown_content_has_readable_layout(self):
        self.assertIn(".bubble-text.markdown-body", self.source)
        self.assertIn(".markdown-body blockquote", self.source)
        self.assertIn(".markdown-body pre", self.source)
        self.assertIn(".markdown-body table", self.source)
        self.assertIn("html.studio-theme-dark .markdown-body", self.source)

    def test_images_open_in_an_accessible_in_page_preview(self):
        self.assertIn('id="chatImagePreview"', self.source)
        self.assertIn('role="dialog" aria-modal="true"', self.source)
        self.assertIn("function openImagePreview(url, alt='', opener=null)", self.source)
        self.assertIn("function closeImagePreview()", self.source)
        self.assertIn("bindPreviewableImage(img, img.alt)", self.source)
        self.assertIn("bindPreviewableImage(thumb, thumb.alt)", self.source)
        self.assertNotIn("window.open(msg.image_url", self.source)
        translations = I18N_JS.read_text(encoding="utf-8")
        self.assertEqual(translations.count("'chat.previewImage':"), 2)

    def test_image_preview_supports_escape_and_zoom(self):
        self.assertIn("if(event.key === 'Escape')", self.source)
        self.assertIn("setImagePreviewZoom(imagePreviewState.zoom + .25)", self.source)
        self.assertIn("imagePreviewStage.addEventListener('wheel'", self.source)
        self.assertIn("imagePreviewImage.addEventListener('dblclick'", self.source)


if __name__ == "__main__":
    unittest.main()
