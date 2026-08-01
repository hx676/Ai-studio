import unittest
from unittest import mock

from custom_nodes.syncanvas_agent_skill import AgentNode
from custom_nodes.syncanvas_agent_skill import skill_runtime


class FakeExecutionContext:
    canvas_id = "canvas-agent"
    node_id = "node-agent"

    def __init__(self):
        self.progress_updates = []

    def progress(self, value, message=""):
        self.progress_updates.append((value, message))


class FakeSkillContext:
    last_user = None
    last_agent_id = ""
    last_expect_json = False

    def __init__(self, settings):
        self.settings = settings

    async def prepare_image(self, url):
        return f"prepared:{url}"

    async def call_agent(self, agent_id, user, expect_json=True):
        type(self).last_agent_id = agent_id
        type(self).last_user = user
        type(self).last_expect_json = expect_json
        return {
            "content": "done",
            "images": ["/output/agent.png", "data:image/png;base64,secret"],
            "authorization": "Bearer secret",
        }


class AgentExtensionNodeTests(unittest.IsolatedAsyncioTestCase):
    def test_v1_state_migration_normalizes_legacy_fields(self):
        migrated = AgentNode.STATE_MIGRATIONS[1]({
            "agent_id": "director",
            "aiProvider": "provider-a",
            "userInput": "legacy message",
            "expect_json": True,
        })
        self.assertEqual("director", migrated["agentId"])
        self.assertEqual("provider-a", migrated["providerId"])
        self.assertEqual("legacy message", migrated["message"])
        self.assertTrue(migrated["expectJson"])

    async def test_execute_combines_inputs_and_filters_unsafe_media(self):
        context = FakeExecutionContext()
        state = {
            "agentId": "director",
            "providerId": "provider-a",
            "textModel": "text-model",
            "visionModel": "vision-model",
            "message": "manual",
            "expectJson": True,
        }
        inputs = {
            "text": [
                {"kind": "text", "value": "connected text"},
                {"kind": "json", "value": {"scene": "night"}},
            ],
            "images": [{"kind": "image", "value": "/assets/reference.png"}],
        }
        with mock.patch.object(skill_runtime, "resolve_settings", return_value={"provider_id": "provider-a"}), mock.patch.object(skill_runtime, "SkillContext", FakeSkillContext):
            result = await AgentNode().execute(context, state, inputs)

        self.assertEqual("director", FakeSkillContext.last_agent_id)
        self.assertTrue(FakeSkillContext.last_expect_json)
        self.assertEqual("manual\n\nconnected text\n\n{\"scene\": \"night\"}", FakeSkillContext.last_user[0]["text"])
        self.assertEqual("prepared:/assets/reference.png", FakeSkillContext.last_user[1]["image_url"]["url"])
        self.assertEqual("done", result["outputs"]["text"]["value"])
        self.assertEqual([{"kind": "image", "value": "/output/agent.png"}], result["outputs"]["images"])
        self.assertNotIn("authorization", result["outputs"]["structured"]["value"])
        self.assertGreaterEqual(len(context.progress_updates), 3)


if __name__ == "__main__":
    unittest.main()
