from webagent.browser.manager import BrowserManager
from webagent.agent.runner import AgentRunner
import webagent.agent.runner as runner_module


def test_detects_textual_human_verification_challenge():
    state = BrowserManager._classify_human_verification(
        'Security check',
        'Please verify you are human to continue.',
        [],
        [],
    )
    assert state['detected'] is True
    assert state['reason'].startswith('text:')


def test_detects_visible_recaptcha_iframe_without_page_text():
    state = BrowserManager._classify_human_verification(
        'Checkout',
        'Continue',
        ['https://www.google.com/recaptcha/api2/anchor?k=test'],
        [{'src': 'https://www.google.com/recaptcha/api2/anchor?k=test', 'title': 'reCAPTCHA', 'visible': True}],
    )
    assert state == {
        'detected': True,
        'provider': 'recaptcha',
        'reason': 'visible_recaptcha_challenge',
    }


def test_ignores_invisible_recaptcha_library_on_normal_page():
    state = BrowserManager._classify_human_verification(
        'Account settings',
        'Change your profile and notification settings.',
        ['https://www.google.com/recaptcha/api2/anchor?k=test'],
        [{'src': 'https://www.google.com/recaptcha/api2/anchor?k=test', 'title': 'reCAPTCHA', 'visible': False}],
    )
    assert state['detected'] is False


class _DB:
    def __init__(self): self.events=[]
    def event(self, task, kind, msg): self.events.append((task, kind, msg))


class _Browser:
    def __init__(self):
        self.calls=[]
        self.observations=[
            {'url':'https://example.test','human_verification':{'detected':True,'provider':'turnstile','reason':'visible_turnstile_challenge'}},
            {'url':'https://example.test/ok','human_verification':{'detected':False,'provider':'','reason':''}},
            {'url':'https://example.test/ok','human_verification':{'detected':False,'provider':'','reason':''}},
            {'url':'https://example.test/ok','human_verification':{'detected':False,'provider':'','reason':''}},
        ]
    def call(self, name, *args, **kwargs):
        self.calls.append(name)
        if name == '_bring_to_front': return True
        if name == '_observe': return self.observations.pop(0)
        raise AssertionError(name)


def test_runner_waits_for_three_clear_observations_then_resumes(monkeypatch):
    monkeypatch.setattr(runner_module.time, 'sleep', lambda _: None)
    db=_DB(); browser=_Browser(); emitted=[]
    runner=AgentRunner(None,browser,db,None,lambda k,m: emitted.append((k,m)))
    initial={'url':'https://example.test','human_verification':{'detected':True,'provider':'turnstile','reason':'text:verify you are human'}}
    result=runner._await_human_verification(7,initial)
    assert result['url'].endswith('/ok')
    assert browser.calls[0] == '_bring_to_front'
    assert browser.calls.count('_observe') == 4
    assert emitted[0][0] == 'human_verification'
    assert emitted[-1][0] == 'human_verification_done'
