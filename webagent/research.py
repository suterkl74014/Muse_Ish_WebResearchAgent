from __future__ import annotations
import hashlib, urllib.parse, time
import httpx
from bs4 import BeautifulSoup

class ResearchEngine:
    def __init__(self, timeout:float=20.0, diagnostics=None):
        self.timeout=timeout; self.diagnostics=diagnostics
        self.headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/155 Safari/537.36"}
    def _log(self,event,**data):
        if self.diagnostics:
            try:self.diagnostics.log(event,**data)
            except Exception:pass
    @staticmethod
    def _clean_url(href:str)->str:
        if href.startswith('//'): href='https:'+href
        try:
            u=urllib.parse.urlparse(href)
            qs=urllib.parse.parse_qs(u.query)
            if 'uddg' in qs: return qs['uddg'][0]
            # Bing commonly wraps destinations in /ck/a?...&u=a1<base64url>. Decode it.
            if 'bing.com' in (u.netloc or '').lower() and 'u' in qs:
                raw=qs['u'][0]
                if raw.startswith('a1'):
                    import base64
                    enc=raw[2:]
                    enc += '=' * (-len(enc) % 4)
                    try:
                        dest=base64.urlsafe_b64decode(enc.encode()).decode('utf-8','ignore')
                        if dest.startswith('http'): return dest
                    except Exception: pass
                if raw.startswith('http'): return raw
        except Exception:pass
        return href
    def _ddg(self,query,limit):
        out=[]; url="https://html.duckduckgo.com/html/?q="+urllib.parse.quote_plus(query)
        r=httpx.get(url,headers=self.headers,timeout=self.timeout,follow_redirects=True); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
        candidates=soup.select('.result') or soup.select('.web-result')
        for res in candidates:
            a=res.select_one('.result__a') or res.select_one('a.result-link') or res.find('a',href=True)
            if not a: continue
            href=self._clean_url(a.get('href',''))
            if not href.startswith('http'): continue
            sn=res.select_one('.result__snippet') or res.select_one('.result-snippet')
            out.append({'title':a.get_text(' ',strip=True),'url':href,'snippet':sn.get_text(' ',strip=True) if sn else '', 'search_source':'duckduckgo'})
            if len(out)>=limit: break
        return out
    def _bing(self,query,limit):
        out=[]; url='https://www.bing.com/search?q='+urllib.parse.quote_plus(query)
        r=httpx.get(url,headers=self.headers,timeout=self.timeout,follow_redirects=True); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
        for li in soup.select('li.b_algo'):
            a=li.select_one('h2 a')
            if not a: continue
            href=self._clean_url(a.get('href',''))
            if not href.startswith('http'): continue
            sn=li.select_one('.b_caption p')
            out.append({'title':a.get_text(' ',strip=True),'url':href,'snippet':sn.get_text(' ',strip=True) if sn else '', 'search_source':'bing'})
            if len(out)>=limit: break
        return out
    def search(self, query:str, limit:int=8) -> list[dict]:
        self._log('research_search_start',query=query,limit=limit); errors=[]; merged=[]; seen=set(); started=time.monotonic()
        for name,fn in [('duckduckgo',self._ddg),('bing',self._bing)]:
            try:
                rows=fn(query,limit)
                self._log('research_search_adapter',adapter=name,query=query,result_count=len(rows))
                for row in rows:
                    u=row['url']
                    if u in seen: continue
                    seen.add(u); merged.append(row)
                    if len(merged)>=limit: break
                if len(merged)>=limit: break
            except Exception as e:
                errors.append(f'{name}: {e}'); self._log('research_search_adapter_error',adapter=name,query=query,error=str(e))
        self._log('research_search_done',query=query,result_count=len(merged),elapsed_ms=round((time.monotonic()-started)*1000),errors=errors)
        return merged[:limit]
    def fetch(self, url:str, max_chars:int=24000) -> dict:
        started=time.monotonic(); self._log('research_fetch_start',url=url)
        try:
            r=httpx.get(url,headers=self.headers,timeout=self.timeout,follow_redirects=True)
            ctype=(r.headers.get('content-type') or '').lower(); status_code=r.status_code
            if status_code in (401,403): result={'status':'browser_required','url':str(r.url),'http_status':status_code,'text':'','title':''}
            elif status_code==429: result={'status':'blocked','url':str(r.url),'http_status':status_code,'text':'','title':''}
            else:
                r.raise_for_status()
                if 'text/html' not in ctype and 'text/plain' not in ctype and 'application/xhtml' not in ctype:
                    result={'status':'binary','url':str(r.url),'http_status':status_code,'content_type':ctype,'text':'','title':''}
                else:
                    soup=BeautifulSoup(r.text,'html.parser')
                    for tag in soup(['script','style','noscript','svg']): tag.decompose()
                    title=soup.title.get_text(' ',strip=True) if soup.title else ''
                    text=' '.join(soup.stripped_strings)
                    low=(title+' '+text[:2000]).lower()
                    challenge=any(x in low for x in ('captcha','verify you are human','access denied','checking your browser','enable javascript'))
                    status='browser_required' if challenge or (len(text)<200 and '<script' in r.text.lower()) else 'ok'
                    result={'status':status,'url':str(r.url),'http_status':status_code,'title':title,'text':text[:max_chars],'hash':hashlib.sha256(text.encode('utf-8','ignore')).hexdigest()}
            self._log('research_fetch_done',url=url,final_url=result.get('url'),status=result.get('status'),http_status=result.get('http_status'),text_chars=len(result.get('text','')),elapsed_ms=round((time.monotonic()-started)*1000)); return result
        except Exception as e:
            self._log('research_fetch_error',url=url,error=str(e),elapsed_ms=round((time.monotonic()-started)*1000)); raise
