from types import SimpleNamespace
from webagent.agent.planner import Planner
from webagent.agent.runner import AgentRunner, RunSettings

class Broker:
    def __init__(self, replies): self.replies=list(replies); self.calls=[]
    def complete(self,prompt,provider,model,system=''):
        self.calls.append((provider,model,prompt)); return SimpleNamespace(provider=provider,model=model,text=self.replies.pop(0))
    def auto_complete(self,*a,**k): raise AssertionError('no fallback')

def test_planner_repairs_with_same_model():
    b=Broker(['{"summary":"x","tasks":[{"key":"t1" "title":"bad"}]}','{"summary":"x","tasks":[{"key":"t1","title":"ok","type":"research","depends_on":[],"success":"yes"}]}'])
    p=Planner(b).plan('goal','standard','openrouter','free','manual')
    assert p['tasks'][0]['title']=='ok'
    assert [(x[0],x[1]) for x in b.calls]==[('openrouter','free'),('openrouter','free')]

class DB:
    def create_task(self,*a,**k): return 1
    def event(self,*a,**k): pass
    def finish(self,*a,**k): pass
    def evidence(self,*a,**k): pass
class Browser:
    def call(self,name,*args):
        if name=='_observe': return {'url':'','title':'','text':'','elements':[],'tabs':[]}
        raise AssertionError(name)
class WS:
    def list_files(self,*a): return []

def test_action_repairs_with_same_model_and_finishes():
    b=Broker(['{"action":"done" "answer":"ok"}','{"action":"done","answer":"ok"}'])
    r=AgentRunner(b,Browser(),DB(),WS())
    s=RunSettings('manual','openrouter','free','','','','',max_steps=2)
    assert r.run('goal',s,1)=='ok'
    assert [(x[0],x[1]) for x in b.calls]==[('openrouter','free'),('openrouter','free')]

def test_action_repairs_valid_json_missing_action():
    b=Broker(['{"status":"collected","sources":[{"title":"x"}]}','{"action":"done","answer":"ok"}'])
    r=AgentRunner(b,Browser(),DB(),WS())
    s=RunSettings('manual','openrouter','free','','','','',max_steps=2)
    assert r.run('goal',s,1)=='ok'
    assert len(b.calls)==2
    assert "missing 'action'" in b.calls[1][2]


def test_action_repairs_unknown_action_name():
    b=Broker(['{"action":"summarize","answer":"not supported"}','{"action":"done","answer":"ok"}'])
    r=AgentRunner(b,Browser(),DB(),WS())
    s=RunSettings('manual','openrouter','free','','','','',max_steps=2)
    assert r.run('goal',s,1)=='ok'
    assert len(b.calls)==2
    assert "unknown action 'summarize'" in b.calls[1][2]
