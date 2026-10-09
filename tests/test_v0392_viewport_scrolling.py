from pathlib import Path
from webagent.browser.manager import BrowserManager


class Mouse:
    def __init__(self): self.calls=[]
    def wheel(self,x,y): self.calls.append((x,y))


class Page:
    def __init__(self):
        self.mouse=Mouse(); self.url='https://example.test/'
    def evaluate(self,js):
        if 'Math.max(200, Math.round(window.innerHeight * 0.85))' in js:
            return 680
        return {
            'url':self.url,'title':'Example','text':'viewport content',
            'elements':[],'dialogs':[],
            'scroll':{'y':800,'viewport_height':800,'document_height':2400,'percent':50,'at_top':False,'at_bottom':False}
        }


class Context:
    pages=[]


def test_default_scroll_uses_viewport_height(monkeypatch, tmp_path):
    monkeypatch.setattr('webagent.browser.manager.time.sleep', lambda _: None)
    b=BrowserManager(Path(tmp_path)/'profile', Path(tmp_path)/'downloads')
    b.page=Page(); b.context=Context()
    result=b._scroll()
    assert b.page.mouse.calls == [(0,680)]
    assert result['scroll']['percent'] == 50


def test_observer_is_viewport_aware_in_source():
    src=Path(__file__).parents[1].joinpath('webagent/browser/manager.py').read_text()
    assert 'distanceToViewport' in src
    assert 'r.bottom>0&&r.top<vh' in src
    assert 'document_height' in src
    assert 'at_bottom' in src
