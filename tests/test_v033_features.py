from pathlib import Path
from webagent.integrations.agentsmith import AgentSmithEnvironment
from webagent.providers.http_providers import ResilientKeyPool
from webagent.providers.rate_limits import DailyAttemptLedger
from webagent.memory.db import Database
from webagent.files.workspace import WorkspaceManager


def test_unlimited_numbered_keys_are_imported():
    secrets={f'GROQ_API_KEY_{i}':f'k{i}' for i in range(1,151)}
    secrets['GROQ_API_KEY']='k1'
    env=AgentSmithEnvironment(secrets=secrets)
    assert len(env.groq_keys)==150
    assert env.groq_keys[0]=='k1'
    assert env.groq_keys[-1]=='k150'


def test_key_leases_spread_parallel_workers():
    pool=ResilientKeyPool(['a','b','c'])
    first=pool.available_indices('m')
    assert first==[0,1,2]
    assert pool.acquire(0,'m')
    assert 0 not in pool.available_indices('m')
    assert pool.acquire(1,'m')
    assert pool.state()['in_use']==2
    pool.release(0); pool.release(1)
    assert pool.state()['in_use']==0


def test_openrouter_hard_allocation_locks_before_provider_limit(tmp_path):
    ledger=DailyAttemptLedger(tmp_path/'usage.json',limit=3)
    key='secret'
    assert ledger.reserve(key)['allowed']
    assert ledger.reserve(key)['allowed']
    third=ledger.reserve(key)
    assert third['allowed'] and third['status']=='DAILY_LOCKED'
    fourth=ledger.reserve(key)
    assert not fourth['allowed'] and fourth['status']=='DAILY_LOCKED'
    assert ledger.used(key)==3


def test_html_artifact_creation(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    ws=WorkspaceManager(tmp_path/'work',db)
    chat=db.create_chat('html')
    art=ws.write_html(chat,None,'page','<!doctype html><title>ok</title>')
    assert art['name']=='page.html'
    assert Path(art['path']).read_text(encoding='utf-8').startswith('<!doctype html>')


def test_latest_root_task_ignores_child(tmp_path):
    db=Database(tmp_path/'db.sqlite')
    chat=db.create_chat('tasks')
    root=db.create_task('root',chat)
    child=db.create_task('child',chat,parent_task_id=root)
    assert db.latest_task_for_chat(chat)['id']==child
    assert db.latest_root_task_for_chat(chat)['id']==root

def test_openrouter_ledger_atomic_under_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    ledger=DailyAttemptLedger(tmp_path/'usage2.json',limit=7)
    key='parallel-secret'
    with ThreadPoolExecutor(max_workers=20) as ex:
        rows=list(ex.map(lambda _: ledger.reserve(key), range(20)))
    assert sum(1 for r in rows if r['allowed'])==7
    assert ledger.used(key)==7

def test_403_denies_model_pair_without_disabling_key(tmp_path, monkeypatch):
    import httpx
    from webagent.providers.http_providers import OpenRouterProvider
    from webagent.providers.base import ModelInfo
    provider=OpenRouterProvider(['k1'],['openrouter/free'],usage_path=tmp_path/'q.sqlite3',daily_limit=30,external_usage_sync=lambda:{})
    provider._model_info['openrouter/free']=ModelInfo('openrouter','openrouter/free',free=True)
    class Resp:
        status_code=403; headers={}
        def raise_for_status(self):
            req=httpx.Request('POST','https://openrouter.ai')
            raise httpx.HTTPStatusError('403',request=req,response=httpx.Response(403,request=req,text='not allowed'))
    monkeypatch.setattr(httpx,'post',lambda *a,**k:Resp())
    try: provider.complete('hello','openrouter/free')
    except Exception: pass
    assert 0 not in provider.pool.disabled
    assert (0,'openrouter/free') in provider.pool.denied


def test_openrouter_paid_model_fails_before_request(tmp_path, monkeypatch):
    import httpx
    from webagent.providers.http_providers import OpenRouterProvider
    from webagent.providers.base import ModelInfo, ModelUnavailableError
    provider=OpenRouterProvider(['k1'],['paid/model'],usage_path=tmp_path/'q.sqlite3',daily_limit=30,external_usage_sync=lambda:{})
    provider._model_info['paid/model']=ModelInfo('openrouter','paid/model',free=False)
    called=[]; monkeypatch.setattr(httpx,'post',lambda *a,**k:called.append(1))
    try: provider.complete('hello','paid/model')
    except ModelUnavailableError: pass
    assert not called
