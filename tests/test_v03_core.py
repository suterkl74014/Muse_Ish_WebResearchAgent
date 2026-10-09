from pathlib import Path
from tempfile import TemporaryDirectory
from webagent.memory.db import Database
from webagent.research import ResearchEngine


def test_plan_state_roundtrip():
    with TemporaryDirectory() as d:
        db=Database(Path(d)/'db.sqlite3')
        chat=db.create_chat('test'); task=db.create_task('goal',chat,{})
        plan={'summary':'x','tasks':[{'key':'a','title':'A','type':'research','depends_on':[]}]}
        db.save_plan(task,plan); db.update_plan_task(task,'a','completed')
        saved=db.plan_for_task(task)
        assert saved['plan']['tasks'][0]['key']=='a'
        assert saved['state']['a']=='completed'


def test_research_engine_constructs():
    r=ResearchEngine(timeout=1)
    assert r.timeout==1


def test_task_tree_aggregates_child_evidence_and_artifacts():
    with TemporaryDirectory() as d:
        db=Database(Path(d)/'db.sqlite3')
        chat=db.create_chat('test'); root=db.create_task('root',chat,{},run_id='r1'); child=db.create_task('child',chat,{},parent_task_id=root,run_id='r1')
        db.evidence(child,'https://example.com','Example','verified data')
        f=Path(d)/'out.txt'; f.write_text('x')
        db.add_artifact(chat,child,'out.txt',str(f),'text/plain',1)
        assert db.task_tree_ids(root)==[root,child]
        assert db.evidence_for_task_tree(root)[0]['title']=='Example'
        assert db.artifacts_for_task_tree(root)[0]['name']=='out.txt'
