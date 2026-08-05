import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ZIMAGE_HTML = ROOT / "static" / "zimage.html"


class ImageBatchFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = ZIMAGE_HTML.read_text(encoding="utf-8")

    def test_image_studio_remembers_one_to_four_batch_count(self):
        self.assertIn('id="batchCountOptions"', self.source)
        self.assertIn("[1,2,3,4].map(count =>", self.source)
        self.assertIn("onclick=\"setBatchCount(${count})\"", self.source)
        self.assertIn("localStorage.getItem('zimage_batch_count')", self.source)
        self.assertIn("localStorage.setItem('zimage_batch_count'", self.source)

    def test_image_studio_uses_batch_api_and_progressive_items(self):
        self.assertIn("fetch('/api/zimage-batches'", self.source)
        self.assertIn("fetch(`/api/image-batches/${encodeURIComponent(batchId)}`", self.source)
        self.assertIn("createBatchPlaceholders(selectedBatchCount, payload)", self.source)
        self.assertIn("item.status === 'succeeded'", self.source)
        self.assertIn("item.status === 'failed'", self.source)
        self.assertNotIn("fetch('/api/zimage-api-image'", self.source)

    def test_failed_image_can_retry_as_a_single_item(self):
        self.assertIn("function retryZimageFailed(placeholderId)", self.source)
        self.assertIn("count:1", self.source)
        self.assertIn("renderFailedPlaceholder", self.source)

    def test_history_cards_deduplicate_polling_and_websocket_results(self):
        self.assertIn("data?.record_id || data?.timestamp", self.source)
        self.assertIn("if (document.getElementById(cardId)) return", self.source)


if __name__ == "__main__":
    unittest.main()
