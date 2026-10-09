from pathlib import Path
from webagent.agent.runner import AgentRunner
from webagent.research import ResearchEngine
from webagent.files.workspace import WorkspaceManager

class DB:
    def add_artifact(self,*a,**k): pass

def test_nested_action_normalization():
    a={'action':{'action':{'action':'click','element_id':32}}}
    assert AgentRunner._normalize_action(a)=={'action':'click','element_id':32}

def test_bing_redirect_decode():
    import base64, urllib.parse
    dest='https://example.com/truck?id=7'
    enc=base64.urlsafe_b64encode(dest.encode()).decode().rstrip('=')
    u='https://www.bing.com/ck/a?u='+urllib.parse.quote('a1'+enc)
    assert ResearchEngine._clean_url(u)==dest

def test_temp_workspace_is_per_run(tmp_path):
    w=WorkspaceManager(tmp_path,DB())
    a=w.write_temp('run-A','scratch/notes.txt','hello')
    b=w.write_temp('run-B','scratch/notes.txt','other')
    assert w.read_temp('run-A','scratch/notes.txt')=='hello'
    assert w.read_temp('run-B','scratch/notes.txt')=='other'
    assert '.temp' in a['relative_path'] and a['path'] != b['path']
    w.cleanup_temp('run-A')
    assert not (tmp_path/'.temp'/'run-A').exists()
