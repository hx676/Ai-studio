import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DigitalHumanComponentFrontendContractTests(unittest.TestCase):
    def test_manual_baidu_download_flow_is_present_and_safe(self):
        html = (ROOT / "static" / "digital-human.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "digital-human" / "component.js").read_text(encoding="utf-8")
        styles = (ROOT / "static" / "css" / "digital-human.css").read_text(encoding="utf-8")

        self.assertIn('id="component-manual-downloads"', html)
        self.assertIn('id="component-manual-list"', html)
        self.assertIn("renderDigitalHumanManualDownloads(status)", script)
        self.assertIn('openLink.rel = "noopener noreferrer"', script)
        self.assertIn("list.replaceChildren()", script)
        self.assertNotIn("innerHTML", script)
        self.assertIn(".component-manual-card", styles)

    def test_component_manifest_contains_the_uploaded_share_information(self):
        manifest = json.loads((ROOT / "components-manifest.json").read_text(encoding="utf-8"))
        artifacts = {item["id"]: item for item in manifest["component"]["artifacts"]}

        self.assertEqual([], artifacts["tts"]["urls"])
        self.assertEqual("ae7k", artifacts["tts"]["manual_download"]["extraction_code"])
        self.assertEqual("https://pan.baidu.com/s/1cUjx6OxSbBWPTCgJWbxUQQ", artifacts["tts"]["manual_download"]["share_url"])
        self.assertEqual([], artifacts["heygem"]["urls"])
        self.assertEqual("i5s8", artifacts["heygem"]["manual_download"]["extraction_code"])
        self.assertEqual("https://pan.baidu.com/s/1DD9SnWtn5hxG_BvZut_Pxg", artifacts["heygem"]["manual_download"]["share_url"])


if __name__ == "__main__":
    unittest.main()
