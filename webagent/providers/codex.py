from __future__ import annotations
import re, subprocess
from .base import Completion, ModelInfo, RateLimitError, ContextLimitError, AuthenticationError, TransientProviderError
from .rate_limits import estimate_tokens, retry_after_seconds, is_rate_limit

# ChatGPT/Codex plan limits are not exposed by the CLI as stable machine-readable model metadata.
# Keep context unknown rather than inventing a number; provider errors are classified at runtime.
class CodexProvider:
    name='codex'
    def __init__(self,distro,codex_path,configured_models=None,diagnostics=None):
        self.distro=distro; self.codex_path=codex_path; self.configured=configured_models or ['gpt-5.6-luna']; self.diagnostics=diagnostics
    def models(self): return list(dict.fromkeys(self.configured))
    def model_info(self,model): return ModelInfo(self.name,model,source='codex-cli; limits managed by ChatGPT plan/CLI')
    def health(self): return {'provider':self.name,'authenticated_cli':bool(self.codex_path),'models':self.models()}
    def complete(self,prompt,model=None,system=None):
        if not self.distro or not self.codex_path: raise RuntimeError('Authenticated Codex CLI was not discovered in AgentSmith WSL')
        model=model or self.configured[0]; full=((system+'\n\n') if system else '')+prompt
        if self.diagnostics:self.diagnostics.log('request_attempt',provider=self.name,model=model,estimated_input_tokens=estimate_tokens(full))
        cmd=['wsl.exe','-d',self.distro,'--',self.codex_path,'--ask-for-approval','never','--sandbox','read-only','exec','--skip-git-repo-check','--model',model,'-']
        p=subprocess.run(cmd,input=full,text=True,capture_output=True,encoding='utf-8',errors='replace',timeout=240)
        if p.returncode!=0:
            err=((p.stderr or '')+'\n'+(p.stdout or '')).strip()[-5000:]; low=err.lower()
            if is_rate_limit(None,err):
                sec=retry_after_seconds(None,err,15); raise RateLimitError(f'Codex CLI rate/usage limited: {err[-500:]}',provider=self.name,model=model,retry_after=sec,kind='rate_limit')
            if any(x in low for x in ('context window','maximum context','too many tokens','context length')):
                raise ContextLimitError(f'Codex context limit: {err[-500:]}',provider=self.name,model=model,kind='context_limit')
            if any(x in low for x in ('login','authentication','unauthorized','401')):
                raise AuthenticationError(f'Codex authentication error: {err[-500:]}',provider=self.name,model=model,kind='auth')
            raise TransientProviderError(f'Codex CLI exited {p.returncode}: {err[-1000:]}',provider=self.name,model=model,kind='transient')
        text=(p.stdout or '').strip()
        if not text: raise TransientProviderError('Codex CLI returned no output',provider=self.name,model=model,kind='transient')
        return Completion(text,self.name,model)
