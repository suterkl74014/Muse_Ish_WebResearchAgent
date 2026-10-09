from types import SimpleNamespace

from webagent.credentials import CredentialStore, SERVICE_NAME
from webagent.integrations.agentsmith import AgentSmithEnvironment
from webagent.providers.broker import ModelBroker


class MemoryBackend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def app_config():
    return SimpleNamespace(
        disabled_key_fingerprints={},
        openrouter_daily_allocation=30,
        provider_priority=['groq', 'gemini', 'openrouter', 'codex'],
    )


def test_webagent_store_accepts_hundreds_of_user_keys_without_env(tmp_path, monkeypatch):
    # Explicitly prove API-key environment variables are not consulted.
    monkeypatch.setenv('GROQ_API_KEY', 'must-not-be-used')
    backend = MemoryBackend()
    store = CredentialStore(tmp_path / 'credentials.json', backend=backend)
    keys = [f'user-key-{i}' for i in range(1, 251)]
    result = store.add_many('groq', keys, source='user')
    assert result['added'] == 250
    assert result['duplicates'] == 0
    assert store.count('groq') == 250
    assert store.keys('groq') == keys
    assert 'must-not-be-used' not in store.keys('groq')
    metadata = (tmp_path / 'credentials.json').read_text(encoding='utf-8')
    assert 'user-key-1' not in metadata
    assert len(backend.values) == 250


def test_duplicate_keys_are_not_stored_twice(tmp_path):
    store = CredentialStore(tmp_path / 'credentials.json', backend=MemoryBackend())
    first = store.add_many('gemini', ['abc', 'abc', 'def'])
    second = store.add_many('gemini', ['abc', 'def'])
    assert first['added'] == 2
    assert second['added'] == 0
    assert second['duplicates'] == 2
    assert store.keys('gemini') == ['abc', 'def']


def test_remove_deletes_os_secret_and_metadata(tmp_path):
    backend = MemoryBackend()
    store = CredentialStore(tmp_path / 'credentials.json', backend=backend)
    row = store.add('openrouter', 'secret')
    username = f"openrouter:{row['id']}"
    assert backend.get_password(SERVICE_NAME, username) == 'secret'
    assert store.remove(row['id'])
    assert backend.get_password(SERVICE_NAME, username) is None
    assert store.count('openrouter') == 0


def test_broker_uses_webagent_store_not_legacy_agentsmith_keys(tmp_path):
    backend = MemoryBackend()
    store = CredentialStore(tmp_path / 'credentials.json', backend=backend)
    store.add_many('groq', ['webagent-1', 'webagent-2'])
    env = AgentSmithEnvironment(secrets={
        'GROQ_API_KEY': 'legacy-should-not-load',
        'GEMINI_API_KEY': 'legacy-gemini-should-not-load',
    })
    env.webagent_config = app_config()
    broker = ModelBroker(env, data_dir=tmp_path, credential_store=store)
    assert broker.providers['groq'].pool.keys == ['webagent-1', 'webagent-2']
    assert broker.providers['gemini'].pool.keys == []
    assert 'legacy-should-not-load' not in broker.providers['groq'].pool.keys


def test_standalone_openrouter_does_not_require_agentsmith_quota_sync(tmp_path):
    store = CredentialStore(tmp_path / 'credentials.json', backend=MemoryBackend())
    store.add('openrouter', 'or-key')
    env = AgentSmithEnvironment(distro=None, config={})
    env.webagent_config = app_config()
    broker = ModelBroker(env, data_dir=tmp_path, credential_store=store)
    assert broker.providers['openrouter'].external_usage_sync is None
    assert 'openrouter' in broker.available()


def test_broker_reloads_after_keys_are_added_and_removed(tmp_path):
    store = CredentialStore(tmp_path / 'credentials.json', backend=MemoryBackend())
    env = AgentSmithEnvironment(config={})
    env.webagent_config = app_config()
    broker = ModelBroker(env, data_dir=tmp_path, credential_store=store)
    assert 'groq' not in broker.available()
    row = store.add('groq', 'new-key')
    broker.reload_provider_keys('groq')
    assert broker.providers['groq'].pool.keys == ['new-key']
    assert 'groq' in broker.available()
    store.remove(row['id'])
    broker.reload_provider_keys('groq')
    assert broker.providers['groq'].pool.keys == []
    assert 'groq' not in broker.available()
