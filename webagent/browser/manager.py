from __future__ import annotations
import threading, queue, time
from pathlib import Path
from playwright.sync_api import sync_playwright

class BrowserManager:
    """Playwright is confined to one worker thread; callers use synchronous RPC."""
    def __init__(self, profile_dir: Path, download_dir: Path, diagnostics=None):
        self.profile_dir=profile_dir; self.download_dir=download_dir; self.q=queue.Queue(); self.thread=None; self.ready=threading.Event(); self.error=None; self.diagnostics=diagnostics
    def _log(self,event,**data):
        if self.diagnostics:
            try:self.diagnostics.log(event,**data)
            except Exception:pass
    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.profile_dir.mkdir(parents=True,exist_ok=True); self.download_dir.mkdir(parents=True,exist_ok=True)
        self.thread=threading.Thread(target=self._run,daemon=True); self.thread.start(); self.ready.wait(25)
        if self.error: raise RuntimeError(self.error)
    def _run(self):
        try:
            with sync_playwright() as pw:
                self.context=pw.chromium.launch_persistent_context(str(self.profile_dir),headless=False,accept_downloads=True,downloads_path=str(self.download_dir),viewport={"width":1280,"height":850},args=["--disable-blink-features=AutomationControlled"])
                self.page=self.context.pages[0] if self.context.pages else self.context.new_page(); self.ready.set(); self._log('browser_started',profile=str(self.profile_dir))
                while True:
                    item=self.q.get()
                    if item is None: break
                    fn,args,kwargs,out=item; started=time.monotonic()
                    try:
                        val=getattr(self,fn)(*args,**kwargs); self._log('browser_call_ok',method=fn,elapsed_ms=round((time.monotonic()-started)*1000),url=getattr(self.page,'url','')); out.put((True,val))
                    except Exception as e:
                        self._log('browser_call_error',method=fn,elapsed_ms=round((time.monotonic()-started)*1000),url=getattr(self.page,'url',''),error=str(e)); out.put((False,e))
                self.context.close()
        except Exception as e:
            self.error=str(e); self._log('browser_fatal',error=str(e)); self.ready.set()
    def call(self,fn,*args,**kwargs):
        self.start(); out=queue.Queue(); self.q.put((fn,args,kwargs,out)); ok,val=out.get()
        if ok:return val
        raise val
    def stop(self):
        if self.thread and self.thread.is_alive(): self.q.put(None)
    def _active(self): return self.page
    def _navigate(self,url): self.page.goto(url,wait_until="domcontentloaded",timeout=45000); return self._observe()
    def _search(self,query):
        import urllib.parse
        return self._navigate("https://www.google.com/search?q="+urllib.parse.quote_plus(query))
    def _back(self): self.page.go_back(wait_until="domcontentloaded",timeout=30000); return self._observe()
    def _scroll(self,amount=None):
        # Default to one near-full viewport so movement is predictable across screen sizes.
        if amount is None:
            try: amount=self.page.evaluate("() => Math.max(200, Math.round(window.innerHeight * 0.85))")
            except Exception: amount=700
        self.page.mouse.wheel(0,int(amount)); time.sleep(.5); return self._observe()
    def _press(self,key): self.page.keyboard.press(key); time.sleep(.5); return self._observe()
    def _resolve(self,element_id):
        element_id=int(element_id); old=getattr(self,'_last_elements',{}).get(element_id,{})
        loc=self.page.locator(f'[data-webagent-id="{element_id}"]')
        if loc.count()<1:
            self._observe(); loc=self.page.locator(f'[data-webagent-id="{element_id}"]')
        if loc.count()<1 and old.get('text'):
            if old.get('tag') in ('input','textarea'): loc=self.page.get_by_placeholder(old['text'],exact=False)
            else: loc=self.page.get_by_text(old['text'],exact=False)
        if loc.count()<1: raise RuntimeError(f"Element {element_id} no longer exists after recovery")
        return loc.first,old
    def _dismiss_overlay(self):
        """Best-effort recovery for open dialogs/backdrops that intercept pointer events."""
        actions=[]
        try:
            dialogs=self.page.locator('[role="dialog"]:visible')
            if dialogs.count():
                d=dialogs.last
                for sel in ['button[aria-label*="close" i]','button[title*="close" i]','[data-testid*="close" i]','button:has-text("Close")','button:has-text("×")']:
                    b=d.locator(sel)
                    if b.count():
                        try:b.first.click(timeout=2500); actions.append('dialog_close_button'); self.page.wait_for_timeout(300); return actions
                        except Exception:pass
                try:self.page.keyboard.press('Escape'); actions.append('escape'); self.page.wait_for_timeout(300); return actions
                except Exception:pass
            # generic visible modal/backdrop fallback
            if self.page.locator('[aria-modal="true"]:visible, [data-state="open"][role="dialog"]:visible').count():
                self.page.keyboard.press('Escape'); actions.append('escape_modal'); self.page.wait_for_timeout(300)
        except Exception as e: self._log('overlay_recovery_error',error=str(e))
        if actions:self._log('overlay_recovery',actions=actions,url=self.page.url)
        return actions
    def _click(self,element_id):
        loc,old=self._resolve(element_id)
        try: loc.scroll_into_view_if_needed(timeout=3000)
        except Exception: pass
        before_url=self.page.url
        try:
            loc.click(timeout=7000)
        except Exception as e:
            text=str(e).lower()
            # A click can succeed and only time out while waiting for navigation. Treat URL/context change as progress.
            current_url=getattr(self.page,'url',before_url)
            if current_url != before_url or 'execution context was destroyed' in text or 'navigation' in text:
                self._log('click_navigation_recovery',element_id=element_id,before_url=before_url,current_url=current_url,error=str(e))
                try:self.page.wait_for_load_state('domcontentloaded',timeout=12000)
                except Exception:pass
                self.page.wait_for_timeout(500)
                return self._observe()
            if 'intercepts pointer events' not in text and 'not stable' not in text: raise
            self._log('click_recovery_start',element_id=element_id,target=old,error=str(e))
            self._dismiss_overlay(); self._observe(); loc,_=self._resolve(element_id)
            try: loc.scroll_into_view_if_needed(timeout=2500)
            except Exception:pass
            loc.click(timeout=7000)
            self._log('click_recovery_ok',element_id=element_id)
        self.page.wait_for_timeout(700); return self._observe()
    def _type(self,element_id,text,submit=False):
        loc,_=self._resolve(element_id); loc.fill(str(text))
        if submit: loc.press("Enter")
        self.page.wait_for_timeout(700); return self._observe()
    def _open_tab(self,url): self.page=self.context.new_page(); return self._navigate(url)
    def _switch_tab(self,index): self.page=self.context.pages[int(index)]; self.page.bring_to_front(); return self._observe()
    def _close_tab(self,index=None):
        p=self.page if index is None else self.context.pages[int(index)]; p.close(); self.page=self.context.pages[-1]; return self._observe()

    @staticmethod
    def _classify_human_verification(title:str, text:str, frame_urls:list[str]|None=None, iframe_meta:list[dict]|None=None) -> dict:
        """Detect visible human-verification challenges without attempting to solve them.

        Keep this deliberately conservative: invisible CAPTCHA libraries on otherwise normal
        pages must not stop the agent. A challenge is reported only when there is a strong
        textual indicator or a visible challenge iframe/provider surface.
        """
        title=(title or '').strip(); text=(text or '').strip(); frame_urls=frame_urls or []; iframe_meta=iframe_meta or []
        haystack=(title+'\n'+text[:16000]).lower()
        strong_phrases=(
            'verify you are human','verify that you are human','confirm you are human',
            'are you a human','prove you are human','human verification','security verification',
            'complete the security check','complete the captcha','enter the characters you see',
            'press and hold','checking your browser','our systems have detected unusual traffic',
            'unusual traffic from your computer network','verify you are not a robot',
            "verify you're not a robot",'i am not a robot',"i'm not a robot",
        )
        phrase=next((x for x in strong_phrases if x in haystack), '')

        providers={
            'recaptcha': ('recaptcha','google.com/recaptcha','gstatic.com/recaptcha'),
            'hcaptcha': ('hcaptcha.com','hcaptcha'),
            'turnstile': ('challenges.cloudflare.com','cf-turnstile','turnstile'),
            'arkose': ('arkoselabs.com','funcaptcha','arkose'),
            'geetest': ('geetest.com','geetest'),
        }
        visible_surfaces=[]
        for meta in iframe_meta:
            if not meta.get('visible'): continue
            src=(meta.get('src') or '').lower(); title_attr=(meta.get('title') or '').lower(); blob=src+' '+title_attr
            for provider,markers in providers.items():
                if any(marker in blob for marker in markers):
                    visible_surfaces.append({'provider':provider,'src':meta.get('src',''),'title':meta.get('title','')})
                    break
        # Cross-origin frame URLs are available from Playwright even when DOM access is denied.
        # Require a matching visible iframe when possible; otherwise a strong phrase can pair
        # with the frame URL.
        frame_provider=''
        for url in frame_urls:
            low=(url or '').lower()
            for provider,markers in providers.items():
                if any(marker in low for marker in markers):
                    frame_provider=provider; break
            if frame_provider: break

        detected=bool(phrase or visible_surfaces or (frame_provider and ('captcha' in haystack or 'challenge' in haystack or 'verification' in haystack)))
        provider=(visible_surfaces[0]['provider'] if visible_surfaces else frame_provider) if detected else ''
        reason=('text:'+phrase) if phrase else ('visible_'+provider+'_challenge' if visible_surfaces else ('frame_'+provider if detected else ''))
        return {'detected':detected,'provider':provider,'reason':reason}

    def _bring_to_front(self):
        self.page.bring_to_front()
        try:self.page.evaluate('() => window.focus()')
        except Exception:pass
        self._log('browser_bring_to_front',url=self.page.url)
        return True

    def _observe(self):
        js=r'''() => {
          document.querySelectorAll('[data-webagent-id]').forEach(e=>e.removeAttribute('data-webagent-id'));
          const vh=Math.max(1, window.innerHeight||document.documentElement.clientHeight||1);
          const vw=Math.max(1, window.innerWidth||document.documentElement.clientWidth||1);
          const sy=Math.max(0, window.scrollY||document.documentElement.scrollTop||0);
          const docH=Math.max(document.body?.scrollHeight||0, document.documentElement?.scrollHeight||0, vh);
          const atBottom=(sy+vh)>=docH-8;
          const sels='a[href],button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]';
          const visible=e=>{const r=e.getBoundingClientRect(); const st=getComputedStyle(e); return r.width>1&&r.height>1&&st.visibility!=='hidden'&&st.display!=='none';};
          const distanceToViewport=r => r.bottom < 0 ? -r.bottom : (r.top > vh ? r.top-vh : 0);
          const els=[...document.querySelectorAll(sels)].filter(visible).map((e,domIndex)=>({e,domIndex,r:e.getBoundingClientRect()}))
            .sort((a,b)=>{const da=distanceToViewport(a.r), db=distanceToViewport(b.r); return da-db || a.r.top-b.r.top || a.domIndex-b.domIndex;}).slice(0,180);
          const items=els.map((x,i)=>{const e=x.e,r=x.r,id=i+1;e.setAttribute('data-webagent-id',id);return {id,tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',text:(e.innerText||e.value||e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.getAttribute('title')||'').trim().replace(/\s+/g,' ').slice(0,180),href:(e.href||'').slice(0,500),type:e.getAttribute('type')||'',in_viewport:r.bottom>0&&r.top<vh,top:Math.round(r.top),bottom:Math.round(r.bottom)};});

          // Collect text that is actually in the current viewport instead of always taking
          // the first characters from the top of document.body.innerText.
          const textSelectors='h1,h2,h3,h4,h5,h6,p,li,td,th,label,a,button,figcaption,article,section';
          const candidates=[...document.querySelectorAll(textSelectors)].filter(e=>{if(!visible(e))return false;const r=e.getBoundingClientRect();return r.bottom>0&&r.top<vh;})
            .map(e=>({top:e.getBoundingClientRect().top,text:(e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\s+/g,' ')}))
            .filter(x=>x.text);
          candidates.sort((a,b)=>a.top-b.top);
          const parts=[]; let joined='';
          for(const x of candidates){
            if(parts.some(p=>p===x.text || (p.length>80 && p.includes(x.text)))) continue;
            if(x.text.length>1200) x.text=x.text.slice(0,1200);
            if((joined.length+x.text.length+1)>12000) break;
            parts.push(x.text); joined+=(joined?' ':'')+x.text;
          }
          // Sparse pages sometimes have no semantic text elements in-view; fall back to body text.
          if(!joined) joined=(document.body?.innerText||'').replace(/\s+/g,' ').slice(0,12000);
          const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible).map(e=>(e.innerText||'').replace(/\s+/g,' ').slice(0,1000)).slice(0,5);
          const iframes=[...document.querySelectorAll('iframe')].map(e=>({src:(e.getAttribute('src')||'').slice(0,1000),title:(e.getAttribute('title')||'').slice(0,300),visible:visible(e)})).slice(0,80);
          return {url:location.href,title:document.title,text:joined,elements:items,dialogs,iframes,scroll:{x:Math.round(window.scrollX||0),y:Math.round(sy),viewport_width:Math.round(vw),viewport_height:Math.round(vh),document_height:Math.round(docH),percent:Math.round(Math.min(100,(sy/Math.max(1,docH-vh))*100)),at_top:sy<=8,at_bottom:atBottom}};
        }'''
        data=self.page.evaluate(js)
        self._last_elements={int(e["id"]):e for e in data.get("elements",[])}
        data["tabs"]=[{"index":i,"title":p.title(),"url":p.url} for i,p in enumerate(self.context.pages)]
        frame_urls=[]
        try: frame_urls=[f.url for f in self.page.frames[1:]]
        except Exception: pass
        data["human_verification"]=self._classify_human_verification(data.get('title',''),data.get('text',''),frame_urls,data.get('iframes',[]))
        self._log('browser_observation',url=data.get('url'),title=data.get('title'),element_count=len(data.get('elements',[])),dialog_count=len(data.get('dialogs',[])),text_chars=len(data.get('text','')),scroll=data.get('scroll',{}),human_verification=data.get('human_verification',{}))
        return data

