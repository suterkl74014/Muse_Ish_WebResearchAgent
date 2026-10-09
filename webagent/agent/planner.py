from __future__ import annotations
import json,re

PLANNER_SYSTEM='''You are the planning engine for a web research/computer-use agent. Return ONLY valid JSON. Decompose the user's goal into a small executable task DAG. Prefer direct web research for ordinary public information; use browser only for interactive/authenticated/JS-heavy work; use synthesis last. Never invent completed work.'''

class Planner:
    def __init__(self,broker): self.broker=broker
    @staticmethod
    def _json(text):
        try:return json.loads(text)
        except Exception:
            m=re.search(r'\{.*\}',text,re.S)
            if not m: raise RuntimeError('Planner returned invalid JSON')
            return json.loads(m.group(0))
    def plan(self,goal:str,depth:str='standard',prefer:str|None=None,model:str|None=None,mode:str='automatic',conversation_context:str='') -> dict:
        prompt=f'''Current goal: {goal}\nPrior conversation from this same chat (context only):\n{conversation_context or '(none)'}\nResearch depth: {depth}\nThe current goal is authoritative. Use prior conversation only to resolve references and preserve continuity.\nReturn {{"summary":"...","tasks":[{{"key":"t1","title":"...","type":"research|browser|synthesis","query":"optional","depends_on":[],"success":"..."}}]}}. Use 2-4 tasks for quick, 4-7 for standard, 6-10 for deep. Independent research branches should have no dependency on each other.'''
        if mode=='manual':
            c=self.broker.complete(prompt,prefer or '',model or '',system=PLANNER_SYSTEM)
        elif mode=='hybrid' and prefer:
            try:c=self.broker.complete(prompt,prefer,model or '',system=PLANNER_SYSTEM)
            except Exception:c=self.broker.auto_complete(prompt,system=PLANNER_SYSTEM,prefer=prefer)
        else:
            c=self.broker.auto_complete(prompt,system=PLANNER_SYSTEM,prefer=prefer)
        try:
            data=self._json(c.text)
        except Exception as parse_err:
            repair_prompt=("Your previous planner response was invalid JSON. Return ONLY one valid JSON object matching the requested "
                           "planner schema, with no markdown or commentary. Preserve the intended plan. Previous response:\n"+c.text[:6000])
            repaired=self.broker.complete(repair_prompt,c.provider,c.model,system=PLANNER_SYSTEM)
            try:
                data=self._json(repaired.text)
            except Exception as repair_err:
                raise RuntimeError(f'Planner JSON repair failed after initial parse error ({parse_err}): {repair_err}') from repair_err
        tasks=data.get('tasks') or []
        if not tasks: raise RuntimeError('Planner returned no tasks')
        for i,t in enumerate(tasks):
            t.setdefault('key',f't{i+1}'); t.setdefault('title',t['key']); t.setdefault('type','research'); t.setdefault('depends_on',[]); t.setdefault('success','Useful evidence collected')
        return data
