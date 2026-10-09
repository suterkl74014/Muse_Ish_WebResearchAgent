from pathlib import Path
import types
import httpx
from webagent.providers.rate_limits import retry_after_seconds, RollingTokenLimiter, DailyAttemptLedger
from webagent.providers.http_providers import GroqProvider
from webagent.providers.base import ModelInfo

def test_retry_after_header_and_text():
    assert retry_after_seconds({'Retry-After':'7'},'',15)==7
    assert retry_after_seconds({},'Please try again in 2 minutes',15)==120

def test_rolling_token_limiter():
    lim=RollingTokenLimiter(100,60); rid=lim.reserve(60); assert lim.snapshot()['remaining_tokens']==40
    assert lim.wait_seconds(50)>0; lim.settle(rid,20); assert lim.wait_seconds(50)==0

def test_daily_ledger_persists(tmp_path):
    p=tmp_path/'usage.json'; l=DailyAttemptLedger(p,2); key='secret'
    assert l.reserve(key)['allowed']; assert l.reserve(key)['allowed']; assert not l.reserve(key)['allowed']
    assert 'secret' not in p.read_text()

def test_groq_rotates_key_after_429(monkeypatch):
    provider=GroqProvider(['k1','k2'],['openai/gpt-oss-20b'],token_limit=7000,max_output_tokens=20)
    provider._model_info['openai/gpt-oss-20b']=ModelInfo('groq','openai/gpt-oss-20b',context_tokens=10000,free=True)
    calls=[]
    class Resp:
        def __init__(self,status,key): self.status_code=status; self.headers={'Retry-After':'3'}; self._key=key
        def raise_for_status(self):
            if self.status_code>=400:
                req=httpx.Request('POST','https://api.groq.com')
                raise httpx.HTTPStatusError('429 rate limit',request=req,response=httpx.Response(self.status_code,request=req,headers=self.headers,text='rate limit'))
        def json(self): return {'choices':[{'message':{'content':'ok'}}],'usage':{'total_tokens':10}}
    def post(url,headers=None,**kwargs):
        key=headers['Authorization'].split()[-1]; calls.append(key); return Resp(429 if key=='k1' else 200,key)
    monkeypatch.setattr(httpx,'post',post)
    out=provider.complete('hello','openai/gpt-oss-20b')
    assert out.text=='ok' and calls==['k1','k2']
    assert provider.pool.next_wait('openai/gpt-oss-20b') is not None

def test_shared_ledger_sees_previous_instance(tmp_path):
    path=tmp_path/'quota.sqlite3'; key='same-key'
    a=DailyAttemptLedger(path,limit=3); b=DailyAttemptLedger(path,limit=3)
    assert a.reserve(key)['allowed']
    assert b.used(key)==1
    assert b.reserve(key)['allowed']
    assert a.used(key)==2


def test_external_usage_counts_against_same_hard_cap(tmp_path):
    ledger=DailyAttemptLedger(tmp_path/'quota.sqlite3',limit=5); key='k'
    assert ledger.reserve(key,external_used=3)['allowed']
    second=ledger.reserve(key,external_used=3)
    assert second['allowed'] and second['status']=='DAILY_LOCKED'
    blocked=ledger.reserve(key,external_used=3)
    assert not blocked['allowed'] and blocked['combined_used']>=5
