from types import SimpleNamespace
from webagent.agent.runner import AgentRunner, RunSettings
from webagent.agent.planner import Planner
from webagent.providers.http_providers import HTTPProviderBase
from webagent.providers.base import Completion, ModelInfo

class FakeBroker:
    def __init__(self): self.calls=[]
    def complete(self,prompt,provider,model='',system=None):
        self.calls.append(('complete',provider,model))
        return Completion('{"action":"done","answer":"ok"}',provider,model,{})
    def auto_complete(self,*args,**kwargs):
        self.calls.append(('auto',kwargs.get('prefer'),kwargs.get('model_hint')))
        return Completion('{"action":"done","answer":"auto"}','auto','auto',{})

class Dummy: pass

def settings(mode='manual'):
    return RunSettings(mode=mode,primary_provider='openrouter',primary_model='or-free',browser_provider='groq',browser_model='g-model',final_provider='codex',final_model='c-model',max_steps=1)

def test_manual_uses_primary_for_every_stage_no_fallback():
    b=FakeBroker(); r=AgentRunner(b,Dummy(),Dummy(),Dummy())
    s=settings()
    r._choose('x',s,'browser'); r._choose('x',s,'final'); r._choose('x',s,'primary')
    assert b.calls == [('complete','openrouter','or-free'),('complete','openrouter','or-free'),('complete','openrouter','or-free')]

def test_manual_planner_uses_exact_primary():
    class B(FakeBroker):
        def complete(self,prompt,provider,model='',system=None):
            self.calls.append(('complete',provider,model))
            return Completion('{"summary":"x","tasks":[{"key":"t1","title":"x","type":"research","depends_on":[],"success":"x"}]}',provider,model,{})
    b=B(); Planner(b).plan('goal','standard','openrouter','or-free','manual')
    assert b.calls == [('complete','openrouter','or-free')]

def test_fit_check_uses_configured_output_budget_not_model_ceiling():
    p=HTTPProviderBase([],[],max_output_tokens=800)
    p.name='x'; p._model_info={'m':ModelInfo('x','m',context_tokens=100000,max_output_tokens=65000)}
    est,out=p._fit_check('hello',None,'m')
    assert out == 800
    assert est < 10
