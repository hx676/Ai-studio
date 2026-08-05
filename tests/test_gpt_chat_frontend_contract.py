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
        self.assertIn("text.textContent = msg.content || '';", self.source)

    def test_image_mode_uses_remembered_one_to_four_batch_count(self):
        for count in range(1, 5):
            self.assertIn(f'id="chat-count-{count}"', self.source)
            self.assertIn(f"setChatImageCount({count})", self.source)
        self.assertIn("localStorage.getItem('gpt_chat_image_count')", self.source)
        self.assertIn("localStorage.setItem('gpt_chat_image_count'", self.source)
        self.assertIn("count:chatImageCount", self.source)

    def test_image_mode_uses_batch_api_and_progressive_polling(self):
        self.assertIn("fetch('/api/chat/image-batches'", self.source)
        self.assertIn("fetch(`/api/image-batches/${batchId}`", self.source)
        self.assertIn("for(let index = 0; index < chatImageCount; index += 1)", self.source)
        self.assertIn("mergeChatBatchItems(batch, conversationId)", self.source)
        self.assertIn("['succeeded','partial','failed'].includes(batch.status)", self.source)
        self.assertNotIn("fetch('/api/chat',", self.source)

    def test_image_bubbles_keep_stable_message_ids_and_retry_in_place(self):
        self.assertIn("row.dataset.messageId = msg.id", self.source)
        self.assertIn("entry.id === item.message_id", self.source)
        self.assertIn("msg.image_status", self.source)
        self.assertIn("retryChatImage(msg.id)", self.source)
        self.assertIn("/messages/${messageId}/retry-image", self.source)
        self.assertIn("currentConversation = data.conversation", self.source)

    def test_batch_image_request_keeps_reference_images(self):
        self.assertIn("reference_images: pendingRefs", self.source)
        self.assertIn("refs = [...pendingRefs, ...refs].slice(0, 4)", self.source)

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
