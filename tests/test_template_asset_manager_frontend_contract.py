import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "asset-manager.html").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "js" / "asset-manager.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "asset-manager.css").read_text(encoding="utf-8")


class TemplateAssetManagerFrontendContractTests(unittest.TestCase):
    def test_global_asset_manager_exposes_template_tab(self):
        self.assertIn('data-tab="templates"', HTML)
        self.assertIn('模板资产', HTML)
        self.assertIn("activeTab === 'templates'", JS)
        self.assertIn('renderTemplateManager()', JS)

    def test_template_categories_stay_separate_from_media_categories(self):
        self.assertIn("(cat.type || 'image') === 'image'", JS)
        self.assertIn("(cat.type || '') === 'template'", JS)
        self.assertIn("type:'template'", JS)
        self.assertIn('默认模板文件夹不能删除', JS)

    def test_template_manager_uses_dedicated_latest_version_api(self):
        self.assertIn('/api/asset-library/templates/${encodeURIComponent(id)}', JS)
        self.assertIn('loadTemplateDetail(detail.id)', JS)
        self.assertIn('templateDetails.set(id, data)', JS)
        self.assertIn('templateDetailErrors.set(id', JS)

    def test_template_manager_supports_search_move_rename_and_delete(self):
        for marker in (
            'id="templateSearch"',
            'data-template-move-single',
            'data-template-inline-name',
            'data-template-delete-selected',
            'data-template-cat-new',
            'data-template-cat-rename',
        ):
            self.assertIn(marker, JS)
        self.assertIn('.template-json-card', CSS)
        self.assertIn('.template-reference-grid', CSS)

    def test_saved_templates_refresh_without_waiting_for_a_manual_reload(self):
        self.assertIn("data.type === 'asset_library_updated'", INDEX)
        self.assertIn("document.querySelectorAll('.stage > iframe[src]')", INDEX)
        self.assertIn("event.data?.type === 'asset_library_updated'", JS)
        self.assertIn("refreshAssetLibraryOnly()", JS)
        self.assertIn("event.data?.type === 'studio-route-active'", JS)


if __name__ == "__main__":
    unittest.main()
