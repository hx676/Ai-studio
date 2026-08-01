import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentManagementFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.settings = (ROOT / "static" / "settings.html").read_text(encoding="utf-8")
        cls.settings_js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
        cls.page = (ROOT / "static" / "agent-skills.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "static" / "js" / "agent-skills.js").read_text(encoding="utf-8")

    def test_agent_management_has_one_primary_entry(self):
        self.assertNotIn('data-route="agents"', self.index)
        self.assertNotIn('id="frame-agents"', self.index)
        self.assertIn("'settings/agents': { page:'settings', section:'agents' }", self.index)
        self.assertIn("'agents': 'settings/agents'", self.index)
        self.assertIn("'agent-skills': 'settings/agents'", self.index)
        self.assertEqual(self.settings.count('data-settings-section="agents"'), 2)
        self.assertIn('id="settings-frame-agents"', self.settings)
        self.assertIn("'agents'", self.settings_js.split("const SECTIONS", 1)[1].split(";", 1)[0])

    def test_management_page_exposes_agents_and_readonly_ai_workflows(self):
        self.assertIn('id="newAgentBtn"', self.page)
        self.assertNotIn('id="newSkillBtn"', self.page)
        self.assertIn("智能体与 AI 工作流", self.page)
        self.assertIn("内置 AI 工作流定义只读", self.script)
        self.assertIn("(skillData.skills || []).filter(item => !item.hidden)", self.script)
        self.assertNotIn("function createSkill", self.script)
        self.assertNotIn("function saveSkill", self.script)
        self.assertNotIn("function deleteSkill", self.script)
        self.assertIn("'/api/agents'", self.script)
        self.assertIn("'/api/skills'", self.script)
        self.assertIn("studio-agent-skills-updated", self.script)


if __name__ == "__main__":
    unittest.main()
