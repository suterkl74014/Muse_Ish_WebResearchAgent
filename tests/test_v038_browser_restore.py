from pathlib import Path
from webagent.agent.runner import RunSettings
from webagent.agent.v3runner import V3Runner, V3Settings
from webagent.memory.db import Database


class NeverDirectResearch:
    def search(self, *args, **kwargs):
        raise AssertionError('Manual mode must not call direct research search adapters')


class DummyBroker:
    pass


class DummyWorkspace:
    def cleanup_temp(self, run_id):
        pass


class DummyBrowser:
    pass


class FixedPlanner:
    def plan(self, *args, **kwargs):
        return {
            'summary': 'browse visibly',
            'tasks': [
                {'key': 't1', 'title': 'Find trucks', 'type': 'research', 'query': 'truck query', 'depends_on': [], 'success': 'found trucks'}
            ],
        }


class ProbeRunner(V3Runner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.browser_nodes = []
        self.planner = FixedPlanner()

    def _browser_subtask(self, root_task, goal, node, settings, chat_id, run_id):
        self.browser_nodes.append(node['key'])
        return True, 'browser used', None


def test_manual_research_goes_directly_to_visible_browser(tmp_path):
    db = Database(Path(tmp_path) / 'db.sqlite3')
    chat = db.create_chat('manual browser restore')
    runner = ProbeRunner(DummyBroker(), DummyBrowser(), db, DummyWorkspace(), research=NeverDirectResearch())
    base = RunSettings(
        mode='manual',
        primary_provider='codex',
        primary_model='gpt-5.6-luna',
        browser_provider='groq',
        browser_model='should-not-be-used',
        final_provider='groq',
        final_model='should-not-be-used',
        max_steps=24,
    )
    runner.run_v3('find trucks', V3Settings(base=base, research_depth='standard', max_workers=3), chat)
    assert runner.browser_nodes == ['t1']
