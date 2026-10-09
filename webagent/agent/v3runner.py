from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from .planner import Planner
from .runner import AgentRunner, RunSettings

@dataclass
class V3Settings:
    base: RunSettings
    research_depth: str='standard'
    max_workers: int=3

class V3Runner(AgentRunner):
    """Planner/task-graph runner with task lineage, evidence aggregation and executable synthesis."""
    def __init__(self,*args,research=None,**kwargs):
        super().__init__(*args,**kwargs); self.research=research; self.planner=Planner(self.broker)

    def _research_task(self,parent_task:int,node:dict,depth:str):
        query=node.get('query') or node.get('title'); self._emit(parent_task,'research',f"Searching: {query}")
        results=self.research.search(query,5 if depth=='quick' else 8 if depth=='standard' else 12); collected=[]; browser_required=[]
        for result in results:
            if self.cancel.is_set(): break
            try:
                page=self.research.fetch(result['url'])
                if page.get('status')=='ok' and page.get('text'):
                    text=page['text'][:16000]; eid=self.db.evidence(parent_task,page['url'],page.get('title') or result['title'],text)
                    collected.append({'evidence_id':eid,'title':page.get('title') or result['title'],'url':page['url'],'text':text[:5000]})
                elif page.get('status') in ('browser_required','blocked'):
                    browser_required.append({'title':result['title'],'url':result['url'],'snippet':result.get('snippet',''),'status':page.get('status')})
            except Exception as e:self._emit(parent_task,'warning',f"Research fetch failed: {result['url']} — {e}")
        self._emit(parent_task,'research',f"{node.get('title')}: collected {len(collected)} usable sources; {len(browser_required)} need browser")
        if self.diagnostics:self.diagnostics.log('research_task_result',task_id=parent_task,node_key=node.get('key'),query=query,search_results=len(results),usable=len(collected),browser_required=len(browser_required))
        return {'collected':collected,'browser_required':browser_required,'search_results':len(results)}

    def _criteria_met(self,criteria:str,result:str,evidence_rows:list[dict],settings:RunSettings)->tuple[bool,str]:
        # Fail closed when a branch produced neither evidence nor a substantive result.
        if not evidence_rows and not (result or '').strip(): return False,'No evidence or result was produced.'
        evidence='\n\n'.join(f"SOURCE {e['id']}: {e.get('title','')}\nURL: {e.get('url','')}\n{e.get('text','')[:2200]}" for e in evidence_rows[:30])
        prompt=f'''Evaluate whether this subtask actually met its success criteria. Return ONLY JSON {{"satisfied":true|false,"reason":"..."}}. Do not give credit merely because an agent said it was done.\nSUCCESS CRITERIA: {criteria}\nSUBTASK RESULT: {result}\nEVIDENCE:\n{evidence[:30000]}'''
        try:
            comp=self._choose(prompt,settings,'final'); data=self._json(comp.text); return bool(data.get('satisfied')),str(data.get('reason',''))
        except Exception as e:return False,f'Criteria verification failed: {e}'

    def _browser_subtask(self,root_task:int,goal:str,node:dict,settings:V3Settings,chat_id:int,run_id:str):
        before={x['id'] for x in self.db.task_children(root_task)}
        sub=RunSettings(**settings.base.__dict__); sub.max_steps=min(16,settings.base.max_steps)
        browser_goal=f"Original goal: {goal}\nCurrent subtask: {node['title']}\nSuccess criteria: {node.get('success')}\nWork only on this subtask. Record useful source pages as you browse."
        try: result=super().run(browser_goal,sub,chat_id,parent_task_id=root_task,run_id=run_id)
        except Exception as e: result=f'Browser subtask exception: {e}'
        children=[x for x in self.db.task_children(root_task) if x['id'] not in before]; child=children[-1] if children else None
        evidence=self.db.evidence_for_task(child['id'],100) if child else []
        ok,reason=self._criteria_met(node.get('success','Useful evidence collected'),result,evidence,settings.base)
        self._emit(root_task,'verify',f"{node['title']}: {'PASSED' if ok else 'FAILED'} — {reason}")
        if self.diagnostics:self.diagnostics.log('subtask_verified',root_task_id=root_task,child_task_id=child['id'] if child else None,node_key=node.get('key'),satisfied=ok,reason=reason,evidence_count=len(evidence))
        return ok,result,child

    def _synthesis_subtask(self,root_task:int,goal:str,node:dict,settings:V3Settings,chat_id:int,run_id:str):
        evidence_rows=self.db.evidence_for_task_tree(root_task,limit=160)
        if not evidence_rows:
            return False,'No usable evidence was collected, so synthesis was not allowed to invent results.'
        evidence='\n\n'.join(f"EVIDENCE {e['id']}\nTITLE: {e.get('title','')}\nURL: {e.get('url','')}\nTEXT: {e.get('text','')[:3000]}" for e in evidence_rows)
        before={x['id'] for x in self.db.task_children(root_task)}
        sub=RunSettings(**settings.base.__dict__); sub.max_steps=max(10,min(24,settings.base.max_steps))
        synth_goal=f'''Original user request:\n{goal}\n\nCurrent synthesis task: {node.get('title')}\nSuccess criteria: {node.get('success')}\n\nVERIFIED/RECORDED EVIDENCE ONLY:\n{evidence[:65000]}\n\nUse ONLY the evidence above for factual claims. If requested files are part of the user request, CREATE THEM with the available file tools before returning done. Do not say file tools are unavailable unless a file action actually errors. If evidence is insufficient for a requested quantity, state that truthfully and still create requested files containing the verified results and limitations.'''
        try: result=super().run(synth_goal,sub,chat_id,parent_task_id=root_task,run_id=run_id)
        except Exception as e: result=f'Synthesis subtask exception: {e}'
        children=[x for x in self.db.task_children(root_task) if x['id'] not in before]; child=children[-1] if children else None
        artifacts=self.db.artifacts_for_task_tree(root_task)
        evidence_child=self.db.evidence_for_task(child['id'],100) if child else []
        # The synthesis success check sees artifacts explicitly.
        crit_result=result+'\nARTIFACTS:\n'+json.dumps([{'name':a['name'],'path':a['path'],'size':a['size']} for a in artifacts],ensure_ascii=False)
        ok,reason=self._criteria_met(node.get('success','Synthesis completed and requested deliverables created'),crit_result,evidence_child or evidence_rows[:20],settings.base)
        self._emit(root_task,'verify',f"{node['title']}: {'PASSED' if ok else 'FAILED'} — {reason}")
        return ok,result

    def run_v3(self,goal:str,settings:V3Settings,chat_id:int):
        self.cancel.clear(); self.pause.clear(); run_id=self.diagnostics.start_run(goal,chat_id,{**settings.base.__dict__,'research_depth':settings.research_depth,'max_workers':settings.max_workers}) if self.diagnostics else ''
        task=self.db.create_task(goal,chat_id,{**settings.base.__dict__,'research_depth':settings.research_depth,'max_workers':settings.max_workers},run_id=run_id); self._emit(task,'start',f"Goal: {goal}")
        conversation_context=self._conversation_context(chat_id,goal)
        try: plan=self.planner.plan(goal,settings.research_depth,settings.base.primary_provider or None,settings.base.primary_model or None,settings.base.mode,conversation_context=conversation_context)
        except Exception as e:
            self._emit(task,'warning',f"Planner unavailable; using browser loop: {e}"); self.db.finish(task,'delegated','')
            return super().run(goal,settings.base,chat_id,parent_task_id=task,run_id=run_id)
        self.db.save_plan(task,plan); self._emit(task,'plan',plan.get('summary','Plan created'))
        if self.diagnostics:self.diagnostics.log('plan_created',task_id=task,plan=plan)
        nodes=plan['tasks']; done=set(); synthesis_answers=[]
        while len(done)<len(nodes) and not self.cancel.is_set():
            ready=[n for n in nodes if n['key'] not in done and all(d in done for d in n.get('depends_on',[]))]
            if not ready: self._emit(task,'error','Task graph stalled: no ready tasks.'); break
            research_nodes=[n for n in ready if n.get('type')=='research']; research_out={}
            if research_nodes:
                # Manual mode is the reliability baseline: the selected provider/model drives the
                # visible browser directly, just like the known-good pre-regression runs. Do not
                # let the direct-search adapters intercept or satisfy research branches in Manual.
                if settings.base.mode=='manual':
                    for n in research_nodes:
                        browser_node=dict(n)
                        browser_node['title']=n.get('title') or 'Research'
                        ok,_,_=self._browser_subtask(task,goal,browser_node,settings,chat_id,run_id)
                        self.db.update_plan_task(task,n['key'],'completed' if ok else 'failed'); done.add(n['key'])
                else:
                    with ThreadPoolExecutor(max_workers=max(1,settings.max_workers)) as ex:
                        futs={ex.submit(self._research_task,task,n,settings.research_depth):n for n in research_nodes}
                        for fut in as_completed(futs):
                            n=futs[fut]
                            try: research_out[n['key']]=fut.result()
                            except Exception as e: research_out[n['key']]={'collected':[],'browser_required':[],'search_results':0,'error':str(e)}; self._emit(task,'error',f"{n['title']} failed: {e}")
                    for n in research_nodes:
                        out=research_out.get(n['key'],{}); collected=out.get('collected') or []
                        rows=self.db.evidence_by_ids([x.get('evidence_id') for x in collected if x.get('evidence_id')])
                        ok=False
                        if rows:
                            summary=f"Direct research collected {len(rows)} sources for: {n.get('title')}"
                            ok,reason=self._criteria_met(n.get('success','Useful evidence collected'),summary,rows,settings.base)
                            self._emit(task,'verify',f"{n['title']}: {'PASSED' if ok else 'FAILED'} — {reason}")
                        # If direct research did not meet the actual success criterion, use visible-browser fallback.
                        if not ok:
                            fallback=dict(n); fallback['title']='Browser fallback: '+n['title']; fallback['success']=n.get('success','Useful evidence collected')
                            ok,_,_=self._browser_subtask(task,goal,fallback,settings,chat_id,run_id)
                        self.db.update_plan_task(task,n['key'],'completed' if ok else 'failed'); done.add(n['key'])
            for n in [x for x in ready if x.get('type')=='browser']:
                ok,_,_=self._browser_subtask(task,goal,n,settings,chat_id,run_id); self.db.update_plan_task(task,n['key'],'completed' if ok else 'failed'); done.add(n['key'])
            for n in [x for x in ready if x.get('type')=='synthesis']:
                ok,answer=self._synthesis_subtask(task,goal,n,settings,chat_id,run_id); synthesis_answers.append(answer); self.db.update_plan_task(task,n['key'],'completed' if ok else 'failed'); done.add(n['key'])
        if self.cancel.is_set():
            self.db.finish(task,'stopped')
            if self.diagnostics:self.diagnostics.log('run_stopped',task_id=task,immediate=True)
            return 'Stopped.'
        source_rows=self.db.evidence_for_task_tree(task,limit=160); artifacts=self.db.artifacts_for_task_tree(task)
        if not source_rows:
            answer='Research did not collect any usable evidence. I am not going to invent results. Check the diagnostic log for search/browser failures and retry.'
        elif synthesis_answers:
            answer=synthesis_answers[-1]
        else:
            evidence_text='\n\n'.join(f"EVIDENCE {e['id']}\nSOURCE: {e['title']}\nURL: {e['url']}\n{e['text'][:3000]}" for e in source_rows)
            artifact_text=json.dumps([{'name':a['name'],'path':a['path'],'size':a['size']} for a in artifacts],ensure_ascii=False)
            synth=f'''Goal: {goal}\nPrior conversation (same chat; context only):\n{conversation_context or '(none)'}\nPlan: {json.dumps(plan,ensure_ascii=False)}\nRecorded evidence only:\n{evidence_text[:60000]}\nArtifacts actually created:\n{artifact_text}\n\nWrite a truthful final answer grounded ONLY in the numbered evidence above. Do not introduce any candidate, price, mileage, status, or fact that is not present in that evidence. Mention incomplete/failed branches. Mention files only if they appear in Artifacts actually created.'''
            try: answer=self._choose(synth,settings.base,'final').text.strip()
            except Exception as e: answer=f"Research completed, but final synthesis failed: {e}"
        status='completed' if all((self.db.plan_for_task(task) or {}).get('state',{}).get(n['key'])=='completed' for n in nodes) else 'partial'
        self.db.finish(task,status,answer); self._emit(task,'done',answer)
        if status=='completed':
            try:self.workspace.cleanup_temp(run_id)
            except Exception:pass
        if self.diagnostics:self.diagnostics.log('run_complete',task_id=task,status=status,evidence_count=len(source_rows),artifact_count=len(artifacts),task_tree=self.db.task_tree_ids(task))
        return answer
