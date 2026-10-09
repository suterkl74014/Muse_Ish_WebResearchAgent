from webagent.agent.runner import AgentRunner
from webagent.agent.planner import Planner

class DB:
    def __init__(self, rows): self.rows=rows; self.calls=[]
    def messages(self, chat_id, limit=500):
        self.calls.append((chat_id,limit))
        return [r for r in self.rows if r.get('chat_id')==chat_id]

class Dummy:
    pass

def make_runner(rows):
    return AgentRunner(Dummy(),Dummy(),DB(rows),Dummy())

def test_conversation_context_is_same_chat_and_excludes_current_turn():
    rows=[
        {'chat_id':1,'role':'user','content':'Find three mice under $10.'},
        {'chat_id':1,'role':'assistant','content':'I found three candidates.'},
        {'chat_id':2,'role':'user','content':'SECRET OTHER CHAT'},
        {'chat_id':1,'role':'user','content':'Which one has the best battery life?'},
    ]
    r=make_runner(rows)
    text=r._conversation_context(1,'Which one has the best battery life?')
    assert 'Find three mice under $10.' in text
    assert 'I found three candidates.' in text
    assert 'Which one has the best battery life?' not in text
    assert 'SECRET OTHER CHAT' not in text


def test_conversation_context_is_bounded_to_recent_turns_and_chars():
    rows=[{'chat_id':1,'role':'user' if i%2==0 else 'assistant','content':f'turn-{i}-' + ('x'*300)} for i in range(30)]
    r=make_runner(rows)
    text=r._conversation_context(1,'',max_turns=6,max_chars=1200)
    assert 'turn-0-' not in text
    assert 'turn-29-' in text
    assert len(text) <= 1200

class CaptureBroker:
    def __init__(self): self.prompt=''
    def complete(self,prompt,provider,model,system=''):
        self.prompt=prompt
        return type('C',(),{'text':'{"summary":"ok","tasks":[{"key":"t1","title":"research","type":"research","depends_on":[],"success":"ok"}]}','provider':provider,'model':model})()


def test_planner_receives_conversation_context():
    b=CaptureBroker(); p=Planner(b)
    p.plan('compare the second one',prefer='openrouter',model='m',mode='manual',conversation_context='USER: Find three mice\n\nASSISTANT: A, B, C')
    assert 'Prior conversation from this same chat' in b.prompt
    assert 'ASSISTANT: A, B, C' in b.prompt
    assert 'Current goal: compare the second one' in b.prompt
