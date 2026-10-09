from __future__ import annotations

import time
from pathlib import Path

from .http_providers import (
    GroqProvider,
    GeminiProvider,
    OpenRouterProvider,
    GROQ_FREE_CHAT_MODELS,
    GEMINI_FREE_MODELS,
)
from .codex import CodexProvider
from .base import ModelInfo
from webagent.config import OPENROUTER_LEDGER_PATH
from webagent.integrations.agentsmith import read_agentsmith_openrouter_usage


class ModelBroker:
    def __init__(self, env, diagnostics=None, data_dir: Path | None = None, credential_store=None):
        cfg = env.config or {}
        self.diagnostics = diagnostics
        self.cfg = cfg
        self.env = env
        self.credential_store = credential_store
        data_dir = Path(data_dir or Path.home() / '.webagent')

        common = dict(
            diagnostics=diagnostics,
            cooldown_default=float(cfg.get('rate_limit_fallback_seconds', 15) or 15),
            unavailable_cooldown=float(cfg.get('unavailable_model_cooldown_seconds', 3600) or 3600),
            max_output_tokens=int(cfg.get('max_completion_tokens', 800) or 800),
        )
        appcfg = getattr(env, 'webagent_config', None)
        disabled = (getattr(appcfg, 'disabled_key_fingerprints', {}) or {}) if appcfg else {}
        or_alloc = int(getattr(appcfg, 'openrouter_daily_allocation', 30) or 30) if appcfg else int(cfg.get('openrouter_daily_attempt_limit', 30) or 30)
        self.provider_priority = list(getattr(appcfg, 'provider_priority', []) or ['groq', 'gemini', 'openrouter', 'codex']) if appcfg else ['groq', 'gemini', 'openrouter', 'codex']

        # When a WebAgent credential store is supplied it is authoritative.
        # Legacy AgentSmith secrets are *not* silently consumed. They can be
        # explicitly imported from the provider settings UI.
        if credential_store is not None:
            groq_keys = credential_store.keys('groq')
            gemini_keys = credential_store.keys('gemini')
            openrouter_keys = credential_store.keys('openrouter')
        else:
            # Backward-compatible construction path for tests/legacy embedders.
            groq_keys = env.groq_keys
            gemini_keys = env.gemini_keys
            openrouter_keys = env.openrouter_keys

        external_or_usage = (lambda: read_agentsmith_openrouter_usage(env.distro)) if getattr(env, 'distro', None) else None
        self.providers = {
            'groq': GroqProvider(
                groq_keys,
                cfg.get('groq_enabled_models') or cfg.get('model_pool') or sorted(GROQ_FREE_CHAT_MODELS),
                token_limit=int(cfg.get('groq_tokens_per_minute_limit', 7000) or 7000),
                disabled_fingerprints=disabled.get('groq', []),
                **common,
            ),
            'gemini': GeminiProvider(
                gemini_keys,
                cfg.get('gemini_enabled_models') or sorted(GEMINI_FREE_MODELS),
                disabled_fingerprints=disabled.get('gemini', []),
                **common,
            ),
            'openrouter': OpenRouterProvider(
                openrouter_keys,
                cfg.get('openrouter_enabled_models') or ['openrouter/free'],
                usage_path=OPENROUTER_LEDGER_PATH,
                daily_limit=or_alloc,
                legacy_usage_paths=[data_dir / 'openrouter_usage.json', Path.home() / '.webagent-openrouter-usage.json'],
                external_usage_sync=external_or_usage,
                disabled_fingerprints=disabled.get('openrouter', []),
                **common,
            ),
            'codex': CodexProvider(
                env.distro,
                env.codex_path,
                cfg.get('codex_enabled_models') or [cfg.get('codex_preferred_model', 'gpt-5.6-luna')],
                diagnostics=diagnostics,
            ),
        }

    def available(self):
        out = []
        for name, p in self.providers.items():
            if name in ('groq', 'gemini', 'openrouter') and not p.pool.keys:
                continue
            if name == 'codex' and not p.codex_path:
                continue
            out.append(name)
        return out

    def models(self, provider):
        return self.providers[provider].models() if provider in self.providers else []

    def cached_models(self, provider):
        p = self.providers.get(provider)
        if p is None:
            return []
        if getattr(p, '_model_info', None):
            vals = list(p._model_info)
            if provider == 'openrouter':
                vals = [m for m in vals if p._model_info[m].free is True]
                if 'openrouter/free' in vals:
                    vals.remove('openrouter/free')
                    vals.insert(0, 'openrouter/free')
            return vals
        if provider == 'openrouter':
            return ['openrouter/free']
        if provider == 'groq':
            return [m for m in p.configured if m in GROQ_FREE_CHAT_MODELS]
        if provider == 'gemini':
            return [m for m in p.configured if m in GEMINI_FREE_MODELS]
        return list(getattr(p, 'configured', []) or [])

    def refresh_models(self, provider):
        p = self.providers.get(provider)
        if p is None:
            return []
        if hasattr(p, '_catalog_at'):
            p._catalog_at = 0.0
        if hasattr(p, '_model_info'):
            p._model_info = {}
        return p.models()

    def model_info(self, provider, model):
        return self.providers[provider].model_info(model) if provider in self.providers else ModelInfo(provider, model)

    def health(self):
        return {name: p.health() for name, p in self.providers.items() if name in self.available()}

    def key_count(self, provider):
        p = self.providers.get(provider)
        return len(p.pool.keys) if p is not None and hasattr(p, 'pool') else 0

    def key_rows(self, provider):
        p = self.providers.get(provider)
        if p is None or not hasattr(p, 'pool'):
            return []
        rows = p.pool.key_rows()
        if self.credential_store is None:
            return rows
        metadata = {r['fingerprint']: r for r in self.credential_store.records(provider)}
        for row in rows:
            meta = metadata.get(row['fingerprint']) or {}
            row['record_id'] = meta.get('id')
            row['label'] = meta.get('label') or row['label']
            row['source'] = meta.get('source') or 'local'
            row['created_at'] = meta.get('created_at') or ''
        return rows

    def reload_provider_keys(self, provider):
        if self.credential_store is None:
            return
        p = self.providers.get(provider)
        if p is None or not hasattr(p, 'replace_keys'):
            return
        appcfg = getattr(self.env, 'webagent_config', None)
        disabled = (getattr(appcfg, 'disabled_key_fingerprints', {}) or {}).get(provider, []) if appcfg else []
        p.replace_keys(self.credential_store.keys(provider), disabled_fingerprints=disabled)

    def reload_all_keys(self):
        for provider in ('groq', 'gemini', 'openrouter'):
            self.reload_provider_keys(provider)

    def set_key_enabled(self, provider, fingerprint, enabled: bool):
        p = self.providers.get(provider)
        if p is None or not hasattr(p, 'pool'):
            return
        rows = p.pool.key_rows()
        disabled = {r['fingerprint'] for r in rows if not r['enabled']}
        if enabled:
            disabled.discard(fingerprint)
        else:
            disabled.add(fingerprint)
        p.pool.set_enabled_fingerprints(disabled)

    def complete(self, prompt, provider, model='', system=None):
        started = time.monotonic()
        chosen = model or ''
        if self.diagnostics:
            self.diagnostics.log('provider_call_start', provider=provider, model=chosen, prompt_chars=len(prompt), system_chars=len(system or ''))
        try:
            c = self.providers[provider].complete(prompt, model or None, system)
            if self.diagnostics:
                self.diagnostics.log('provider_call_ok', provider=c.provider, model=c.model, elapsed_ms=round((time.monotonic() - started) * 1000), response_chars=len(c.text), usage=c.usage)
            return c
        except Exception as e:
            if self.diagnostics:
                self.diagnostics.log('provider_call_error', provider=provider, model=chosen, elapsed_ms=round((time.monotonic() - started) * 1000), error=str(e), kind=getattr(e, 'kind', 'error'), retry_after=getattr(e, 'retry_after', None))
            raise

    def auto_complete(self, prompt, model_hint='', system=None, prefer=None):
        order = []
        if prefer:
            order.append(prefer)
        order += list(self.provider_priority)
        seen = set()
        last = None
        cooldowns = []
        for name in order:
            if name in seen or name not in self.available():
                continue
            seen.add(name)
            try:
                return self.complete(prompt, name, model_hint if name == prefer else '', system)
            except Exception as e:
                last = e
                ra = getattr(e, 'retry_after', None)
                if ra is not None:
                    cooldowns.append(float(ra))
                if self.diagnostics:
                    self.diagnostics.log('provider_fallback', failed_provider=name, error=str(e), kind=getattr(e, 'kind', 'error'), retry_after=ra, next_candidates=[x for x in order if x not in seen])
        if cooldowns:
            wait = max(.5, min(cooldowns))
            max_wait = float(self.cfg.get('rate_limit_max_wait_seconds', 60) or 60)
            if wait <= max_wait:
                if self.diagnostics:
                    self.diagnostics.log('all_providers_cooling_wait', seconds=round(wait, 2))
                time.sleep(wait)
                for name in order:
                    if name not in self.available():
                        continue
                    try:
                        return self.complete(prompt, name, model_hint if name == prefer else '', system)
                    except Exception as e:
                        last = e
        raise RuntimeError(f'All providers failed: {last}')
