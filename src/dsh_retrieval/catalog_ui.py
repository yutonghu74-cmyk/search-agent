"""Local-only configuration UI, using the same demo identity as Chat."""
CATALOG_HTML = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>已有索引配置</title><link rel="stylesheet" href="/chat.css"></head><body><main>
<header><h1>已有索引配置</h1><a href="/">返回 Chat</a></header>
<section class="notice">本地模拟身份 · demo 项目。仅登记已有索引，不创建物理库、不执行检索。
文本将发送到所选 LLM，请勿填写密钥、内网地址或敏感数据。配置权限须单独授予。</section>
<section class="composer"><label for="catalog-model">配置 LLM</label><select id="catalog-model"></select>
<label for="catalog-text">描述库的用途和索引选择</label><textarea id="catalog-text" rows="5" maxlength="4000"
placeholder="例如：使用已有动物图片库查猫狗，增加宠物别名"></textarea>
<button id="catalog-preview" disabled>生成配置预览</button></section>
<section class="composer"><h2>待确认配置</h2><pre id="catalog-proposal"></pre>
<button id="catalog-save" disabled>确认保存配置</button></section>
<section class="composer"><h2>已保存配置</h2><button id="catalog-refresh">刷新</button><pre id="catalog-current"></pre></section>
<p id="catalog-status" role="status" aria-live="polite"></p>
</main><script src="/catalog.js"></script></body></html>'''

CATALOG_JS = ''''use strict';
const el=id=>document.getElementById(id);
const base='/v1/retrieval/projects/demo/catalog-config';
let pending=null, busy=false, available=false;
function reset(){pending=null;el('catalog-proposal').textContent='';el('catalog-save').disabled=true;}
function controls(){el('catalog-preview').disabled=busy||!available||!el('catalog-model').value;el('catalog-save').disabled=busy||!pending;el('catalog-refresh').disabled=busy;el('catalog-text').disabled=busy;el('catalog-model').disabled=busy;}
async function api(path,body){const response=await fetch(path,{method:body?'POST':'GET',cache:'no-store',headers:{'Content-Type':'application/json','X-Tenant-Id':'tenant-a','X-User-Id':'user-a'},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok){const error=new Error(`${data.code}: ${data.message}`);error.status=response.status;throw error;}return data;}
async function refresh(){try{const data=await api(base);available=true;el('catalog-current').textContent=data.config?JSON.stringify(data.config,null,2):'尚未保存配置';}catch(error){if(error.status===409){available=true;el('catalog-current').textContent='已保存配置已失效，请重新生成并确认。';}else{available=false;throw error;}}}
async function run(action){if(busy)return;busy=true;controls();try{await action();}catch(error){reset();el('catalog-status').textContent=error.message;}finally{busy=false;controls();}}
el('catalog-text').addEventListener('input',reset);el('catalog-model').addEventListener('change',()=>{reset();controls();});
el('catalog-preview').addEventListener('click',()=>run(async()=>{reset();const data=await api(base+'/preview',{text:el('catalog-text').value,llm_id:el('catalog-model').value});pending={proposal:data.proposal,proposal_digest:data.proposal_digest};el('catalog-proposal').textContent=JSON.stringify(data.proposal.entries,null,2);el('catalog-status').textContent='仅为预览，请核对后确认保存。';}));
el('catalog-save').addEventListener('click',()=>run(async()=>{const data=await api(base+'/confirm',pending);reset();el('catalog-status').textContent=`已保存配置版本 ${data.version}；未执行检索。`;await refresh();}));
el('catalog-refresh').addEventListener('click',()=>run(async()=>{reset();await refresh();el('catalog-status').textContent='已刷新保存配置。';}));
run(async()=>{const data=await api('/v1/retrieval/projects/demo/models');for(const model of data.items.filter(x=>x.kind==='llm')){const option=document.createElement('option');option.value=model.id;option.textContent=model.model+' ('+model.id+')';el('catalog-model').append(option);}await refresh();el('catalog-status').textContent=el('catalog-model').value?'可生成已有索引配置。':'未配置 LLM，暂不能生成。';});
'''
