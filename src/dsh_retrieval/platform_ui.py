"""Bootstrap existing pages only after authenticated project selection."""
import re


def platform_html(html):
    html = re.sub(r'<script src="([^"]+)"[^>]*></script>',
                  r'<script type="application/x-platform-script" data-src="\1"></script>', html)
    html = html.replace('固定体验范围：demo / default。页面尚不支持切换项目或子维度。', '当前项目由上方项目选择器确定；切换项目后会重新加载页面。')
    html = html.replace('当前使用模拟身份和本地账本。', '当前使用平台个人身份及项目授权。')
    return html.replace('</body>', '<script src="/platform.js"></script></body>')


PLATFORM_JS = r'''(async()=>{
'use strict';
const nativeFetch=window.fetch.bind(window),nativeGet=Storage.prototype.getItem,nativeSet=Storage.prototype.setItem,nativeRemove=Storage.prototype.removeItem;
let context,project,closed=false;const pending=new Set();
function unavailable(message){closed=true;for(const c of pending)c.abort();pending.clear();document.body.replaceChildren();const p=document.createElement('p');p.setAttribute('role','alert');p.textContent=message;document.body.append(p)}
try{
 const response=await nativeFetch('/v1/platform/context',{credentials:'same-origin',cache:'no-store'});
 if(!response.ok)throw new Error('请先通过数据平台完成登录，再打开检索 Agent。');
 context=await response.json();
 if(!Array.isArray(context.projects)||!context.projects.length)throw new Error('当前没有可访问的项目，请联系平台项目管理员。');
 const explicit=new URLSearchParams(location.search).get('project');
 project=explicit||nativeGet.call(sessionStorage,'platform-project:'+context.user_id)||String(context.projects[0].id);
 if(!context.projects.some(p=>String(p.id)===project))throw new Error('当前项目不可访问，请从平台重新进入。');
 nativeSet.call(sessionStorage,'platform-project:'+context.user_id,project);
 const prefix='platform:'+encodeURIComponent(context.tenant_id)+':'+encodeURIComponent(context.user_id)+':'+project+':';
 for(const [name,fn] of [['getItem',nativeGet],['setItem',nativeSet],['removeItem',nativeRemove]])Storage.prototype[name]=function(key,...args){return fn.call(this,prefix+key,...args)};
 window.fetch=async(input,options={})=>{
  const request=new Request(input instanceof Request?input:new URL(input,location.href),options),url=new URL(request.url,location.href);
  if(url.origin!==location.origin)throw new Error('禁止向外部地址发送项目请求');
  if(!url.pathname.startsWith('/v1/'))return nativeFetch(request);
  if(closed)throw new Error('项目上下文已失效');
  url.pathname=url.pathname.replace('/v1/retrieval/projects/demo/','/v1/retrieval/projects/'+project+'/');
  const headers=new Headers(request.headers);headers.delete('X-User-Id');headers.delete('X-Tenant-Id');headers.delete('Authorization');headers.set('x-app-id',project);headers.set('X-Agent-User',context.user_id);headers.set('X-CSRF-Token',context.csrf);
  const controller=new AbortController();pending.add(controller);
  const abort=()=>controller.abort();if(request.signal.aborted)abort();else request.signal.addEventListener('abort',abort,{once:true});
  try{const r=await nativeFetch(url,{method:request.method,headers,credentials:'same-origin',cache:'no-store',body:['GET','HEAD'].includes(request.method)?undefined:await request.blob(),signal:controller.signal});
   if(r.status===401||r.status===403)unavailable('登录或项目授权已失效，请从数据平台重新进入。');return r;
  }finally{pending.delete(controller);request.signal.removeEventListener('abort',abort)}
 };
 const bar=document.createElement('nav');bar.setAttribute('aria-label','当前项目');
 const label=document.createElement('label');label.textContent='当前项目 ';const select=document.createElement('select');
 for(const item of context.projects){const option=document.createElement('option');option.value=String(item.id);option.textContent=item.alias||item.name||String(item.id);select.append(option)}select.value=project;
 select.onchange=()=>{closed=true;for(const c of pending)c.abort();nativeSet.call(sessionStorage,'platform-project:'+context.user_id,select.value);location.replace(location.pathname+'?project='+encodeURIComponent(select.value))};label.append(select);bar.append(label);
 const logout=document.createElement('button');logout.textContent='退出本应用';logout.onclick=async()=>{await nativeFetch('/v1/platform/logout',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':context.csrf}});unavailable('已退出本应用。后台任务按项目授权继续管理。')};bar.append(logout);document.body.prepend(bar);
 document.addEventListener('click',event=>{const a=event.target.closest('a[href]');if(!a)return;const url=new URL(a.href,location.href);if(url.origin===location.origin&&!url.pathname.startsWith('/v1/')){url.searchParams.set('project',project);a.href=url.href}});
 const timer=setInterval(async()=>{try{const r=await window.fetch('/v1/platform/check');if(!r.ok)unavailable('项目授权不可用，请重新进入。')}catch{unavailable('无法确认当前项目权限，请重新进入。')}},30000);
 addEventListener('pagehide',()=>{closed=true;clearInterval(timer);for(const c of pending)c.abort()},{once:true});
 const imageInput=document.getElementById('query-image-file');if(imageInput&&!context.capabilities?.reference_image_search){imageInput.disabled=true;imageInput.closest('.query-image-input').classList.add('hidden')}
 for(const placeholder of document.querySelectorAll('script[data-src]')){if(closed)break;await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=placeholder.dataset.src;script.onload=resolve;script.onerror=()=>reject(new Error('页面资源加载失败'));placeholder.replaceWith(script)})}
}catch(error){unavailable(error.message||'平台接入暂不可用')}
})();'''
