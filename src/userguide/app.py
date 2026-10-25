"""app — the studio page: workflow dashboard on the left, agent chat on the right, one page, no terminal.

Left: an iframe showing the current stage page (follows the case when the agent or Submit moves it).
Right: chat with the agent (chat.py). Tool calls appear as collapsible cards. When the agent edits the
draft, the left panel reloads unless the user has unsaved edits in the form (then a banner asks).
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>WRF-Agent Studio</title>
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
#wrap{display:flex;height:100vh}
#left{flex:0 0 62%;min-width:30%;position:relative}#left iframe{width:100%;height:100%;border:0}
#bar{flex:0 0 6px;cursor:col-resize;background:#d7dde4}#bar:hover{background:#9fb3c8}
#rail{display:none;flex:0 0 34px;background:#1f3b57;color:#fff;cursor:pointer;writing-mode:vertical-rl;text-align:center;padding-top:14px;font-size:13px;letter-spacing:1px;user-select:none}
#rail:hover{background:#2b5279}
#wrap.collapsed #right,#wrap.collapsed #bar{display:none}#wrap.collapsed #rail{display:block}#wrap.collapsed #left{flex:1 1 auto !important}
#right{flex:1;display:flex;flex-direction:column;min-width:300px;background:#fafbfc}
#rh{background:#1f3b57;color:#fff;padding:10px 14px;font-size:14px;display:flex;justify-content:space-between;align-items:center}
#rh small{opacity:.8}#rh button{background:#ffffff22;color:#fff;border:0;border-radius:4px;padding:3px 8px;cursor:pointer}
#msgs{flex:1;overflow-y:auto;overflow-x:hidden;padding:12px 16px 12px 12px}
.m{margin:8px 0;max-width:90%;padding:8px 11px;border-radius:10px;white-space:pre-wrap;line-height:1.45;font-size:14px;overflow-wrap:anywhere}
.u{background:#1f3b57;color:#fff;margin-left:auto}.a{background:#fff;border:1px solid #e3e7ec}
.t{font-size:12px;margin:4px 0;max-width:92%}.t summary{cursor:pointer;color:#555}.t pre{background:#f1f3f5;padding:6px;border-radius:6px;white-space:pre-wrap;max-height:260px;overflow:auto}
.t .ok{color:#2e7d32}.t .bad{color:#c62828}
#warn{background:#fff3cd;padding:6px 12px;font-size:13px;display:none}
#in{display:flex;gap:6px;padding:10px;border-top:1px solid #e3e7ec;background:#fff}
#in textarea{flex:1;resize:none;height:64px;padding:8px;border:1px solid #ccd;border-radius:6px;font:inherit}
#in button{background:#2e7d32;color:#fff;border:0;border-radius:6px;padding:0 16px;cursor:pointer}
#in button:disabled{background:#999}
#banner{display:none;position:absolute;top:8px;left:50%;transform:translateX(-50%);background:#ffd166;padding:6px 12px;border-radius:6px;font-size:13px;box-shadow:0 2px 6px #0003;z-index:5}
.dots:after{content:'…';animation:d 1s steps(3) infinite}@keyframes d{0%{content:'.'}50%{content:'..'}100%{content:'...'}}
code{background:#eef1f4;padding:0 3px;border-radius:3px}
.mt{display:block;max-width:100%;overflow-x:auto;border-collapse:collapse;margin:4px 0;font-size:13px;white-space:normal}.mt td,.mt th{border:1px solid #dde2e8;padding:3px 6px;text-align:left}.mt th{background:#f1f4f7}
</style></head><body>
<div id="wrap">
 <div id="left"><div id="banner">The agent changed the draft. <button id="reload">Reload form</button> (your unsaved edits will be replaced)</div>
  <iframe id="dash" src="/__CURRENT__"></iframe></div>
 <div id="bar"></div>
 <div id="rail" title="Open the agent panel">⟨ Agent <span id="unread"></span></div>
 <div id="right">
  <div id="rh"><span><b>Agent</b> <small id="model"></small></span><span><button id="clear" title="Hide the conversation from view (the log is kept)">New view</button> <button id="fold" title="Collapse the agent panel">⟩</button></span></div>
  <div id="warn">No model API key is configured (WRF_AGENT_LLM_API_KEY). Start with <code>wrf-agent-studio</code>.</div>
  <div id="msgs"></div>
  <div id="in"><textarea id="ta" placeholder="Describe the simulation you want, or ask about any parameter… (Enter to send, Shift+Enter for a new line)"></textarea><button id="send">Send</button></div>
 </div>
</div>
<script>
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const inl=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>');
function md(s){const out=[];let tb=[];const flush=()=>{if(!tb.length)return;const rows=tb.filter(r=>!/^\s*\|?\s*:?-{2,}/.test(r)).map(r=>r.trim().replace(/^\||\|$/g,'').split('|'));
 out.push('<table class="mt">'+rows.map((c,i)=>'<tr>'+c.map(x=>`<${i?'td':'th'}>${inl(x.trim())}</${i?'td':'th'}>`).join('')+'</tr>').join('')+'</table>');tb=[];};
 for(const line of String(s).split('\n')){if(/^\s*\|/.test(line)){tb.push(line);continue;}flush();
  const h=line.match(/^#{1,4}\s+(.*)/);out.push(h?`<b>${inl(h[1])}</b>`:inl(line));}flush();return out.join('\n').replace(/\n?(<table)/g,'$1').replace(/(<\/table>)\n?/g,'$1');}
let since=0;
function row(r){const box=$('#msgs');
 if(r.role==='user')box.insertAdjacentHTML('beforeend',`<div class="m u">${esc(r.content)}</div>`);
 else if(r.role==='assistant'){if(r.content)box.insertAdjacentHTML('beforeend',`<div class="m a">${md(r.content)}</div>`);}
 else if(r.role==='tool'){const res=r.result||{};const verdict=res.check?res.check.verdict:(res.verdict||'');
  const tag=res.error?'<span class="bad">error</span>':verdict?`<span class="${verdict==='PASS'?'ok':'bad'}">${esc(verdict)}</span>`:'';
  box.insertAdjacentHTML('beforeend',`<details class="t"><summary>🔧 ${esc(r.name)} ${tag}</summary><pre>${esc(JSON.stringify(r.args,null,1))}</pre><pre>${esc(JSON.stringify(res,null,1))}</pre></details>`);}
 box.scrollTop=box.scrollHeight;}
async function load(){const j=await (await fetch('/api/chat')).json();$('#model').textContent=j.settings.model;
 $('#warn').style.display=j.settings.has_key?'none':'block';$('#msgs').innerHTML='';(j.rows||[]).slice(since).forEach(row);}
async function send(){const t=$('#ta').value.trim();if(!t)return;$('#ta').value='';$('#send').disabled=true;
 row({role:'user',content:t});const p=document.createElement('div');p.className='m a dots';p.textContent='Working';$('#msgs').append(p);
 try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t})});
  p.remove();if(!r.ok){row({role:'assistant',content:'⚠ '+await r.text()});}else{(await r.json()).rows.filter(x=>x.role!=='user').forEach(row);if($('#wrap').classList.contains('collapsed'))$('#unread').textContent='●';}}
 catch(e){p.remove();row({role:'assistant',content:'⚠ '+e});}
 $('#send').disabled=false;poll();}
$('#send').onclick=send;$('#ta').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();send();}};
$('#clear').onclick=async()=>{const j=await (await fetch('/api/chat')).json();since=(j.rows||[]).length;$('#msgs').innerHTML='';};
// left panel follows the case
let ver=null,stage=null,lastPage=null;const frame=$('#dash');
function reloadLeft(page){$('#banner').style.display='none';const w=frame.contentWindow;if(page)frame.src='/'+page;else w.location.reload();}
$('#reload').onclick=()=>reloadLeft();
async function poll(){try{const v=await (await fetch('/api/version')).json();
 const w0=frame.contentWindow;if(ver!==null&&v.version!==ver&&w0&&w0.__selfVersion===v.version){ver=v.version;lastPage=v.page;stage=v.stage;return;}
 if(ver!==null&&v.version!==ver){const w=frame.contentWindow;const onPage=(w.location.pathname||'').replace('/','');
  const moved=v.stage!==stage||v.page!==lastPage;   // the case itself moved on
  const userNavigated=lastPage!==null&&onPage!==lastPage; // user browsed another stage by hand
  if(moved&&!userNavigated){reloadLeft(v.page);}
  else if(w.__dirty&&onPage===v.page){$('#banner').style.display='block';}
  else if(onPage===v.page)reloadLeft();}
 ver=v.version;stage=v.stage;lastPage=v.page;}catch(e){}}
function fold(c){$('#wrap').classList.toggle('collapsed',c);localStorage.setItem('wrfAgentFold',c?'1':'');if(!c){$('#unread').textContent='';$('#ta').focus();}}
$('#fold').onclick=()=>fold(true);$('#rail').onclick=()=>fold(false);
document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='j'){e.preventDefault();fold(!$('#wrap').classList.contains('collapsed'));}});
fold(!!localStorage.getItem('wrfAgentFold'));
setInterval(poll,2000);poll();load();
// draggable splitter
$('#bar').onmousedown=e=>{e.preventDefault();frame.style.pointerEvents='none';const mv=ev=>{$('#left').style.flexBasis=Math.max(30,Math.min(80,ev.clientX/innerWidth*100))+'%';};
 const up=()=>{frame.style.pointerEvents='';removeEventListener('mousemove',mv);removeEventListener('mouseup',up);};addEventListener('mousemove',mv);addEventListener('mouseup',up);};
</script></body></html>
"""


def page(current_page: str) -> str:
    return PAGE.replace("__CURRENT__", current_page)
