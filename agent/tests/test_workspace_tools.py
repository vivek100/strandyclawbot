import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "strands" not in sys.modules:
    strands_mod = types.ModuleType("strands")
    strands_mod.Agent = object
    strands_mod.tool = lambda fn: fn
    sys.modules["strands"] = strands_mod

if "strands.models.openai" not in sys.modules:
    strands_models_mod = types.ModuleType("strands.models")
    sys.modules["strands.models"] = strands_models_mod
    openai_mod = types.ModuleType("strands.models.openai")
    openai_mod.OpenAIModel = object
    sys.modules["strands.models.openai"] = openai_mod

import agent  # noqa: E402
import workspace  # noqa: E402


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prev_root = os.environ.get("AGENT_WORKSPACE_ROOT")
        os.environ["AGENT_WORKSPACE_ROOT"] = self.temp_dir.name
        self.token = workspace.THREAD_ID_CTX.set("thread-abc")

    def tearDown(self):
        workspace.THREAD_ID_CTX.reset(self.token)
        if self.prev_root is None:
            os.environ.pop("AGENT_WORKSPACE_ROOT", None)
        else:
            os.environ["AGENT_WORKSPACE_ROOT"] = self.prev_root
        self.temp_dir.cleanup()

    def test_provision_workspace_creates_core_files(self):
        payload = agent.provision_workspace("thread-abc")
        self.assertIn("workspace_path", payload)

        ws = workspace.get_workspace_path("thread-abc")
        self.assertTrue((ws / "AGENTS.md").exists())
        self.assertTrue((ws / "MEMORY.md").exists())
        self.assertTrue((ws / "skills").exists())

    def test_markdown_memory_write_get_search(self):
        agent.provision_workspace("thread-abc")
        result = agent.remember_note("User likes concise answers", target="daily")
        self.assertIn("Saved note to", result)

        combined = agent.memory_get(limit_chars=3000)
        self.assertIn("User likes concise answers", combined)

        search = agent.memory_search("concise", limit=3)
        self.assertIn("markdown match", search)

    def test_skills_are_loaded_from_workspace(self):
        agent.provision_workspace("thread-abc")
        ws = workspace.get_workspace_path("thread-abc")
        skill_dir = ws / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Demo Skill\n\nUse this flow.\n", encoding="utf-8")

        summary = agent.list_skills(limit=10)
        self.assertIn("demo-skill", summary)

        content = agent.read_skill("demo-skill")
        self.assertIn("Demo Skill", content)

    def test_create_skill_tool_creates_local_skill(self):
        agent.provision_workspace("thread-abc")
        result = agent.create_skill(
            "windows-demo",
            "# windows-demo\n\nUse powershell.\n",
            scope="local",
        )
        self.assertIn("Created skill 'windows-demo'", result)
        ws = workspace.get_workspace_path("thread-abc")
        self.assertTrue((ws / "skills" / "windows-demo" / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
