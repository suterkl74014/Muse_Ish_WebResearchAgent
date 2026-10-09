from __future__ import annotations
import json, threading, time, hashlib
from pathlib import Path
import httpx
from .base import Completion, ModelInfo, RateLimitError, ContextLimitError, AuthenticationError, TransientProviderError, ModelUnavailableError
from .rate_limits import estimate_tokens, retry_after_seconds, is_rate_limit, RollingTokenLimiter, DailyAttemptLedger

# Maintained WebAgent free-tier allowlists. Live discovery is intersected with
# these lists for Groq/Gemini because their model-list endpoints do not provide
# reliable per-model free-pricing metadata.
GROQ_FREE_CHAT_MODELS = {
    "openai/gpt-oss-120b", "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b", "qwen/qwen3.6-27b",
}
GEMINI_FREE_MODELS = {
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
    "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
    "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
}

class ResilientKeyPool:
    def __init__(self, keys:list[str], diagnostics=None, cooldown_default:float=15.0, disabled_fingerprints=None):
        self.keys=[k for k in keys if k]; self.cursor=0; self.cooldowns={}; self.disabled=set(); self.denied=set(); self.in_use=set(); self.lock=threading.RLock(); self.diagnostics=diagnostics; self.cooldown_default=float(cooldown_default)
        disabled=set(disabled_fingerprints or [])
        for i,k in enumerate(self.keys):
            if self.fingerprint(k) in disabled:self.disabled.add(i)
    @staticmethod
    def fingerprint(key:str): return hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]
    def key_label(self,i): return f"key {i+1} [{self.fingerprint(self.keys[i])}]"
    def available_indices(self,model:str):
        with self.lock:
            now=time.monotonic(); n=len(self.keys); out=[]
            for off in range(n):
                i=(self.cursor+off)%n
                if i in self.disabled or i in self.in_use or (i,model) in self.denied: continue
                if self.cooldowns.get((i,model),0)>now: continue
                out.append(i)
            return out
    def acquire(self,i,model):
        with self.lock:
            now=time.monotonic()
            if i in self.disabled or i in self.in_use or (i,model) in self.denied or self.cooldowns.get((i,model),0)>now:return False
            self.in_use.add(i); return True
    def release(self,i):
        with self.lock:self.in_use.discard(i)
    def set_enabled_fingerprints(self,disabled_fingerprints):
        with self.lock:
            fps=set(disabled_fingerprints or []); self.disabled={i for i,k in enumerate(self.keys) if self.fingerprint(k) in fps}
    def disable(self,i):
        with self.lock:self.disabled.add(i); self.in_use.discard(i)
    def deny(self,i,model):
        with self.lock:self.denied.add((i,model)); self.cursor=(i+1)%max(1,len(self.keys)); self.in_use.discard(i)
    def cooldown(self,i,model,seconds):
        with self.lock:self.cooldowns[(i,model)]=time.monotonic()+max(.5,float(seconds)); self.cursor=(i+1)%max(1,len(self.keys)); self.in_use.discard(i)
    def success(self,i,model):
        with self.lock:self.cooldowns.pop((i,model),None); self.cursor=(i+1)%max(1,len(self.keys)); self.in_use.discard(i)
    def next_wait(self,model):
        now=time.monotonic(); waits=[until-now for (i,m),until in self.cooldowns.items() if m==model and i not in self.disabled and until>now]
        return max(.0,min(waits)) if waits else None
    def state(self,model=None):
        now=time.monotonic(); return {'keys':len(self.keys),'enabled':len(self.keys)-len(self.disabled),'disabled':len(self.disabled),'in_use':len(self.in_use),'cooling':sum(1 for (i,m),u in self.cooldowns.items() if u>now and (model is None or m==model)),'denied_pairs':sum(1 for i,m in self.denied if model is None or m==model)}
    def key_rows(self):
        with self.lock:
            return [{'index':i,'label':f'Key {i+1}','fingerprint':self.fingerprint(k),'enabled':i not in self.disabled,'in_use':i in self.in_use} for i,k in enumerate(self.keys)]
    def replace_keys(self, keys:list[str], disabled_fingerprints=None):
        with self.lock:
            self.keys=[k for k in keys if k]; self.cursor=0; self.cooldowns={}; self.denied=set(); self.in_use=set(); self.disabled=set()
            disabled=set(disabled_fingerprints or [])
            for i,k in enumerate(self.keys):
                if self.fingerprint(k) in disabled:self.disabled.add(i)

class HTTPProviderBase:
    name='http'
    def __init__(self,keys,configured_models=None,*,diagnostics=None,cooldown_default=15,unavailable_cooldown=3600,max_output_tokens=1800,disabled_fingerprints=None):
        self.pool=ResilientKeyPool(keys,diagnostics,cooldown_default,disabled_fingerprints=disabled_fingerprints); self.configured=list(configured_models or []); self.diagnostics=diagnostics; self.cooldown_default=float(cooldown_default); self.unavailable_cooldown=float(unavailable_cooldown); self.default_max_output=int(max_output_tokens); self._model_info={}; self._catalog_at=0.0
    def _log(self,event,**data):
        if self.diagnostics:self.diagnostics.log(event,provider=self.name,**data)
    def _ordered_models(self,requested=None):
        if requested:return [requested]
        vals=[]
        for m in self.configured+self.models():
            if m and m not in vals:vals.append(m)
        return vals
    def model_info(self,model):
        if model not in self._model_info:self.models()
        return self._model_info.get(model,ModelInfo(self.name,model,source='unknown'))
    def _fit_check(self,prompt,system,model,max_out=None):
        info=self.model_info(model); estimated=estimate_tokens(((system+'\n\n') if system else '')+prompt)
        # Reserve/generate the configured request budget, not the model's entire theoretical output ceiling.
        # A model advertising 65k output tokens must not make a 400-character planning prompt reserve 65k TPM.
        if max_out is not None:
            out=int(max_out)
        else:
            out=int(self.default_max_output)
        if info.max_output_tokens:
            out=min(out,int(info.max_output_tokens))
        out=max(1,out)
        if info.context_tokens and estimated+out>info.context_tokens:
            raise ContextLimitError(f"Estimated request {estimated}+{out} output tokens exceeds {self.name}/{model} context limit {info.context_tokens}",provider=self.name,model=model,kind='context_limit')
        return estimated,min(out,info.max_output_tokens or out)
    @staticmethod
    def _status(exc):
        if isinstance(exc,httpx.HTTPStatusError): return exc.response.status_code
        return None
    @staticmethod
    def _body(exc):
        try:return exc.response.text[-4000:]
        except Exception:return str(exc)
    def health(self):
        now=time.monotonic(); return {'provider':self.name,'keys':len(self.pool.keys),'enabled_keys':len(self.pool.keys)-len(self.pool.disabled),'disabled_keys':len(self.pool.disabled),'in_use_keys':len(self.pool.in_use),'cooldowns':[{'key':i+1,'model':m,'seconds':round(u-now,1)} for (i,m),u in self.pool.cooldowns.items() if u>now]}
    def replace_keys(self,keys,disabled_fingerprints=None):
        self.pool.replace_keys(list(keys),disabled_fingerprints=disabled_fingerprints)

class GroqProvider(HTTPProviderBase):
    name='groq'
    def __init__(self,keys,configured_models=None,**kw):
        token_limit=int(kw.pop('token_limit',7000)); super().__init__(keys,configured_models,**kw); self.token_limit_default=token_limit; self.token_limiters={i:RollingTokenLimiter(token_limit,60) for i in range(len(self.pool.keys))}
    def replace_keys(self,keys,disabled_fingerprints=None):
        super().replace_keys(keys,disabled_fingerprints=disabled_fingerprints); self.token_limiters={i:RollingTokenLimiter(self.token_limit_default,60) for i in range(len(self.pool.keys))}
    def health(self):
        row=super().health(); row['token_budgets']=[dict(key=i+1,**lim.snapshot()) for i,lim in self.token_limiters.items()]; return row
    def models(self):
        if not self.pool.keys:return list(self.configured)
        if self._model_info and time.monotonic()-self._catalog_at<600:return list(self._model_info)
        try:
            infos={}; rows=[]
            for i,key in enumerate(self.pool.keys):
                if i in self.pool.disabled: continue
                try:
                    r=httpx.get('https://api.groq.com/openai/v1/models',headers={'Authorization':f'Bearer {key}'},timeout=10); r.raise_for_status(); rows=r.json().get('data',[]); break
                except Exception: continue
            for x in rows:
                mid=x.get('id');
                if not mid or mid not in GROQ_FREE_CHAT_MODELS:continue
                ctx=x.get('context_window') or x.get('context_length'); maxout=x.get('max_completion_tokens') or x.get('max_output_tokens')
                infos[mid]=ModelInfo(self.name,mid,int(ctx) if ctx else None,int(maxout) if maxout else None,free=True,source='live-groq+webagent-free-registry',raw=x)
            if infos:self._model_info=infos; self._catalog_at=time.monotonic()
        except Exception as e:self._log('model_catalog_error',error=str(e))
        return sorted(self._model_info) if self._model_info else [m for m in self.configured if m in GROQ_FREE_CHAT_MODELS]
    def complete(self,prompt,model=None,system=None):
        if not self.pool.keys:raise RuntimeError('No Groq API keys configured')
        if model and model not in GROQ_FREE_CHAT_MODELS: raise ModelUnavailableError(f"Groq model is not in WebAgent's verified free-model registry: {model}",provider=self.name,model=model,kind='not_free')
        last=None; models=self._ordered_models(model)
        for mid in models:
            if mid not in GROQ_FREE_CHAT_MODELS:
                self._log('model_skipped_not_free',model=mid); continue
            try: est,max_out=self._fit_check(prompt,system,mid)
            except ContextLimitError as e:
                last=e; self._log('model_skipped_context',model=mid,error=str(e));
                if model:raise
                continue
            candidates=self.pool.available_indices(mid)
            if not candidates:
                last=RateLimitError(f'All Groq keys for {mid} are cooling down',provider=self.name,model=mid,retry_after=self.pool.next_wait(mid),kind='rate_limit')
                continue
            for i in candidates:
                if not self.pool.acquire(i,mid): continue
                key=self.pool.keys[i]; label=self.pool.key_label(i); reserve=est+max_out; rid=None
                try:
                    limiter=self.token_limiters.setdefault(i,RollingTokenLimiter(self.token_limit_default,60))
                    wait=limiter.wait_seconds(reserve)
                    if wait>0:
                        self._log('token_budget_wait',model=mid,api_key=label,seconds=round(wait,2),**limiter.snapshot()); time.sleep(wait)
                    rid=limiter.reserve(reserve)
                    msgs=[]
                    if system:msgs.append({'role':'system','content':system})
                    msgs.append({'role':'user','content':prompt})
                    self._log('request_attempt',model=mid,api_key=label,estimated_input_tokens=est,reserved_tokens=reserve,rolling=limiter.snapshot())
                    r=httpx.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {key}'},json={'model':mid,'messages':msgs,'temperature':0.1,'max_tokens':max_out},timeout=75)
                    if r.status_code>=400:r.raise_for_status()
                    data=r.json(); usage=data.get('usage') or {}; total=int(usage.get('total_tokens') or (int(usage.get('prompt_tokens') or est)+int(usage.get('completion_tokens') or 0)))
                    try:
                        live_limit=int(r.headers.get('x-ratelimit-limit-tokens') or 0)
                        if live_limit>0:limiter.set_limit(live_limit)
                    except Exception:pass
                    limiter.settle(rid,total); self.pool.success(i,mid)
                    self._log('rate_headers',model=mid,api_key=label,limit_tokens=r.headers.get('x-ratelimit-limit-tokens'),remaining_tokens=r.headers.get('x-ratelimit-remaining-tokens'),reset_tokens=r.headers.get('x-ratelimit-reset-tokens'),limit_requests=r.headers.get('x-ratelimit-limit-requests'),remaining_requests=r.headers.get('x-ratelimit-remaining-requests'),reset_requests=r.headers.get('x-ratelimit-reset-requests'))
                    return Completion(data['choices'][0]['message']['content'],self.name,mid,{k:int(v) for k,v in usage.items() if isinstance(v,(int,float))})
                except Exception as e:
                    status=self._status(e); body=self._body(e); last=e
                    if rid is not None:self.token_limiters.setdefault(i,RollingTokenLimiter(self.token_limit_default,60)).release(rid)
                    if status in (401,403):
                        self.pool.disable(i); self._log('key_rejected',model=mid,api_key=label,status=status); last=AuthenticationError(f'Groq rejected {label} (HTTP {status})',provider=self.name,model=mid,status=status,kind='auth'); continue
                    if is_rate_limit(status,body):
                        sec=retry_after_seconds(getattr(getattr(e,'response',None),'headers',None),body,self.cooldown_default); self.pool.cooldown(i,mid,sec); self._log('rate_limit',model=mid,api_key=label,status=status,retry_after=sec); last=RateLimitError(f'Groq {mid} {label} rate limited for {sec:.1f}s',provider=self.name,model=mid,status=status,retry_after=sec,kind='rate_limit'); continue
                    if status==404:
                        self.pool.cooldown(i,mid,self.unavailable_cooldown); self._log('model_unavailable',model=mid,api_key=label,status=status); last=ModelUnavailableError(f'Groq model unavailable: {mid}',provider=self.name,model=mid,status=status,kind='model_unavailable'); break
                    if status in (408,409) or status is None or (status and status>=500):
                        self.pool.cooldown(i,mid,min(15,self.cooldown_default)); self._log('transient_failure',model=mid,api_key=label,status=status,error=body[-500:]); last=TransientProviderError(f'Groq transient failure on {label}: {body[-300:]}',provider=self.name,model=mid,status=status,kind='transient'); continue
                    self.pool.release(i); raise
        if isinstance(last,Exception):raise last
        raise RuntimeError('No usable Groq model/key candidate')

class GeminiProvider(HTTPProviderBase):
    name='gemini'
    def models(self):
        if not self.pool.keys:return list(self.configured)
        if self._model_info and time.monotonic()-self._catalog_at<600:return list(self._model_info)
        try:
            infos={}; rows=[]
            for i,key in enumerate(self.pool.keys):
                if i in self.pool.disabled: continue
                try:
                    r=httpx.get(f'https://generativelanguage.googleapis.com/v1beta/models?key={key}',timeout=12); r.raise_for_status(); rows=r.json().get('models',[]); break
                except Exception: continue
            for x in rows:
                if 'generateContent' not in (x.get('supportedGenerationMethods') or []):continue
                mid=str(x.get('name','')).removeprefix('models/');
                if not mid or mid not in GEMINI_FREE_MODELS:continue
                infos[mid]=ModelInfo(self.name,mid,int(x.get('inputTokenLimit') or 0) or None,int(x.get('outputTokenLimit') or 0) or None,free=True,source='live-gemini+webagent-free-registry',raw=x)
            if infos:self._model_info=infos;self._catalog_at=time.monotonic()
        except Exception as e:self._log('model_catalog_error',error=str(e))
        return sorted(self._model_info) if self._model_info else [m for m in self.configured if m in GEMINI_FREE_MODELS]
    def complete(self,prompt,model=None,system=None):
        if not self.pool.keys:raise RuntimeError('No Gemini API keys configured')
        if model and model not in GEMINI_FREE_MODELS: raise ModelUnavailableError(f'Gemini model is not in the verified free-tier registry: {model}',provider=self.name,model=model,kind='not_free')
        last=None
        for mid in self._ordered_models(model):
            if mid not in GEMINI_FREE_MODELS:
                self._log('model_skipped_not_free',model=mid); continue
            try:est,max_out=self._fit_check(prompt,system,mid)
            except ContextLimitError as e:
                last=e;self._log('model_skipped_context',model=mid,error=str(e));
                if model:raise
                continue
            candidates=self.pool.available_indices(mid)
            if not candidates:
                last=RateLimitError(f'All Gemini keys for {mid} are cooling down',provider=self.name,model=mid,retry_after=self.pool.next_wait(mid),kind='rate_limit');continue
            text=((system+'\n\n') if system else '')+prompt
            for i in candidates:
                if not self.pool.acquire(i,mid): continue
                key=self.pool.keys[i]; label=self.pool.key_label(i)
                try:
                    self._log('request_attempt',model=mid,api_key=label,estimated_input_tokens=est,max_output_tokens=max_out)
                    url=f'https://generativelanguage.googleapis.com/v1beta/models/{mid}:generateContent?key={key}'
                    r=httpx.post(url,json={'contents':[{'parts':[{'text':text}]}],'generationConfig':{'temperature':0.1,'maxOutputTokens':max_out}},timeout=90)
                    if r.status_code>=400:r.raise_for_status()
                    data=r.json(); parts=data['candidates'][0]['content']['parts']; usage=data.get('usageMetadata') or {}; self.pool.success(i,mid)
                    norm={'prompt_tokens':int(usage.get('promptTokenCount') or est),'completion_tokens':int(usage.get('candidatesTokenCount') or 0),'total_tokens':int(usage.get('totalTokenCount') or 0)}
                    return Completion(''.join(p.get('text','') for p in parts),self.name,mid,norm)
                except Exception as e:
                    status=self._status(e);body=self._body(e);last=e
                    if status in (401,403):self.pool.disable(i);self._log('key_rejected',model=mid,api_key=label,status=status);last=AuthenticationError(f'Gemini rejected {label} (HTTP {status})',provider=self.name,model=mid,status=status,kind='auth');continue
                    if is_rate_limit(status,body):
                        sec=retry_after_seconds(getattr(getattr(e,'response',None),'headers',None),body,self.cooldown_default);self.pool.cooldown(i,mid,sec);self._log('rate_limit',model=mid,api_key=label,status=status,retry_after=sec);last=RateLimitError(f'Gemini {mid} {label} rate limited for {sec:.1f}s',provider=self.name,model=mid,status=status,retry_after=sec,kind='rate_limit');continue
                    if status==404:self.pool.cooldown(i,mid,self.unavailable_cooldown);last=ModelUnavailableError(f'Gemini model unavailable: {mid}',provider=self.name,model=mid,status=status,kind='model_unavailable');break
                    if status in (408,409) or status is None or (status and status>=500):self.pool.cooldown(i,mid,min(15,self.cooldown_default));last=TransientProviderError(f'Gemini transient failure: {body[-300:]}',provider=self.name,model=mid,status=status,kind='transient');continue
                    self.pool.release(i); raise
        if isinstance(last,Exception):raise last
        raise RuntimeError('No usable Gemini model/key candidate')

class OpenRouterProvider(HTTPProviderBase):
    name='openrouter'
    def __init__(self,keys,configured_models=None,*,usage_path:Path|None=None,daily_limit=30,legacy_usage_paths=None,external_usage_sync=None,**kw):
        super().__init__(keys,configured_models or ['openrouter/free'],**kw);self.ledger=DailyAttemptLedger(usage_path or Path.home()/'.webagent-openrouter-quota.sqlite3',daily_limit,legacy_json_paths=legacy_usage_paths); self.external_usage_sync=external_usage_sync
    def models(self):
        if self._model_info and time.monotonic()-self._catalog_at<600:return list(self._model_info)
        try:
            r=httpx.get('https://openrouter.ai/api/v1/models',timeout=12);r.raise_for_status();infos={}
            for x in r.json().get('data',[]):
                mid=x.get('id');pricing=x.get('pricing') or {}
                if not mid:continue
                free=False
                try:free=float(pricing.get('prompt',1))==0 and float(pricing.get('completion',1))==0
                except Exception:pass
                top=x.get('top_provider') or {}; maxout=top.get('max_completion_tokens')
                if not free: continue
                infos[mid]=ModelInfo(self.name,mid,int(x.get('context_length') or 0) or None,int(maxout or 0) or None,supports_tools=(('tools' in (x.get('supported_parameters') or [])) or ('tool_choice' in (x.get('supported_parameters') or []))),free=True,source='live-openrouter-free',raw=x)
            infos.setdefault('openrouter/free',ModelInfo(self.name,'openrouter/free',None,None,supports_tools=True,free=True,source='openrouter-router'))
            if infos:self._model_info=infos;self._catalog_at=time.monotonic()
        except Exception as e:self._log('model_catalog_error',error=str(e))
        # Free-only catalog. Paid/restricted models never enter the selectable pool.
        all_models=sorted(mid for mid,info in self._model_info.items() if info.free is True)
        if 'openrouter/free' in all_models:
            all_models.remove('openrouter/free'); all_models.insert(0,'openrouter/free')
        return all_models if all_models else ['openrouter/free']
    def complete(self,prompt,model=None,system=None):
        if not self.pool.keys:raise RuntimeError('No OpenRouter API keys configured')
        if model:
            info=self.model_info(model)
            if model!='openrouter/free' and info.free is not True:
                raise ModelUnavailableError(f'OpenRouter model is not confirmed free: {model}',provider=self.name,model=model,kind='not_free')
        try:
            external_usage=self.external_usage_sync() if self.external_usage_sync else {}
        except Exception as e:
            self._log('external_quota_sync_failed',error=str(e))
            raise RateLimitError(f'OpenRouter safety ledger could not synchronize optional legacy usage; refusing request: {e}',provider=self.name,model=model or '',kind='quota_history_unavailable')
        last=None
        for mid in self._ordered_models(model):
            info=self.model_info(mid)
            if mid!='openrouter/free' and info.free is not True:
                self._log('model_skipped_not_free',model=mid); continue
            try:est,max_out=self._fit_check(prompt,system,mid)
            except ContextLimitError as e:
                last=e;self._log('model_skipped_context',model=mid,error=str(e));
                if model:raise
                continue
            candidates=[i for i in self.pool.available_indices(mid) if self.ledger.can_use(self.pool.keys[i],external_usage.get(hashlib.sha256(self.pool.keys[i].encode('utf-8')).hexdigest(),0))]
            if not candidates:
                last=RateLimitError(f'No OpenRouter key is currently eligible for {mid}',provider=self.name,model=mid,retry_after=self.pool.next_wait(mid),kind='rate_limit');continue
            msgs=[]
            if system:msgs.append({'role':'system','content':system})
            msgs.append({'role':'user','content':prompt})
            for i in candidates:
                if not self.pool.acquire(i,mid): continue
                key=self.pool.keys[i];label=self.pool.key_label(i);external_used=external_usage.get(hashlib.sha256(key.encode('utf-8')).hexdigest(),0);quota=self.ledger.reserve(key,external_used=external_used)
                if not quota['allowed']:
                    self.pool.release(i); continue
                try:
                    self._log('request_attempt',model=mid,api_key=label,estimated_input_tokens=est,max_output_tokens=max_out,local_daily_used=quota['used'],agentsmith_daily_used=quota.get('external_used',0),combined_daily_used=quota.get('combined_used',quota['used']),local_daily_limit=quota['limit'])
                    r=httpx.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':f'Bearer {key}','HTTP-Referer':'https://localhost/webagent','X-Title':'Web Agent'},json={'model':mid,'messages':msgs,'temperature':0.1,'max_tokens':max_out},timeout=90)
                    if r.status_code>=400:r.raise_for_status()
                    data=r.json();usage=data.get('usage') or {}
                    choices=data.get('choices') or []
                    message=(choices[0].get('message') or {}) if choices and isinstance(choices[0],dict) else {}
                    content=message.get('content')
                    if isinstance(content,list):
                        content=''.join(str(part.get('text','')) if isinstance(part,dict) else str(part) for part in content)
                    if content is None or not str(content).strip():
                        raise TransientProviderError(f'OpenRouter returned no text content for {mid}',provider=self.name,model=mid,kind='empty_response')
                    self.pool.success(i,mid)
                    return Completion(str(content),self.name,mid,{k:int(v) for k,v in usage.items() if isinstance(v,(int,float))})
                except Exception as e:
                    status=self._status(e);body=self._body(e);last=e
                    if status==401:
                        self.pool.disable(i);self._log('key_rejected',model=mid,api_key=label,status=status);last=AuthenticationError(f'OpenRouter rejected {label} (HTTP 401)',provider=self.name,model=mid,status=status,kind='auth');continue
                    if status==403:
                        self.pool.deny(i,mid);self._log('key_model_denied',model=mid,api_key=label,status=status,error=body[-500:]);last=ModelUnavailableError(f'OpenRouter denied {mid} for {label} (HTTP 403)',provider=self.name,model=mid,status=status,kind='model_denied');continue
                    if is_rate_limit(status,body):
                        sec=retry_after_seconds(getattr(getattr(e,'response',None),'headers',None),body,self.cooldown_default);self.pool.cooldown(i,mid,sec);self._log('rate_limit',model=mid,api_key=label,status=status,retry_after=sec);last=RateLimitError(f'OpenRouter {mid} {label} rate limited for {sec:.1f}s',provider=self.name,model=mid,status=status,retry_after=sec,kind='rate_limit');continue
                    if status==404:self.pool.cooldown(i,mid,self.unavailable_cooldown);last=ModelUnavailableError(f'OpenRouter model unavailable: {mid}',provider=self.name,model=mid,status=status,kind='model_unavailable');break
                    if status in (408,409) or status is None or (status and status>=500):self.pool.cooldown(i,mid,min(15,self.cooldown_default));last=TransientProviderError(f'OpenRouter transient failure: {body[-300:]}',provider=self.name,model=mid,status=status,kind='transient');continue
                    self.pool.release(i); raise
        if isinstance(last,Exception):raise last
        raise RuntimeError('No usable OpenRouter model/key candidate')
