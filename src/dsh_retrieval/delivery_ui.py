"""Small explicit package approval surface, separate from cleaning previews."""

DELIVERY_HTML = '''<dialog id="delivery-dialog" aria-labelledby="delivery-title"><div class="settings-header"><h2 id="delivery-title">打包与交付</h2><button id="delivery-close" class="secondary">关闭</button></div><div id="delivery-content" aria-live="polite"></div></dialog>'''
DELIVERY_CSS = '''#delivery-dialog{width:min(820px,calc(100vw - 24px));max-height:90vh;overflow:auto;border:1px solid #dedfdb;border-radius:16px;padding:24px}#delivery-content{overflow-wrap:anywhere}#delivery-content .card{max-width:100%;width:100%}'''
DELIVERY_JS = r'''
let deliveryReadToken=0;
$('delivery-close').onclick=()=>{deliveryReadToken++;$('delivery-dialog').close()};
async function openDelivery(taskId){
  const token=++deliveryReadToken,dialog=$('delivery-dialog'),box=$('delivery-content');
  if(!dialog.open)dialog.showModal();box.textContent='正在读取当前版本交付状态…';
  const current=()=>token===deliveryReadToken&&dialog.open&&state.task?.task_id===taskId;
  const endpoint=`/v1/retrieval/tasks/${encodeURIComponent(taskId)}/delivery`;
  const action=(label,fn)=>{const button=document.createElement('button');button.textContent=label;button.onclick=async()=>{button.disabled=true;try{await fn()}catch(error){if(current()){const p=document.createElement('p');p.textContent='操作失败：'+error.message;box.append(p)}}finally{button.disabled=false}};box.append(button)};
  const refresh=async()=>{const task=await api(`/v1/retrieval/tasks/${taskId}`);if(!current())return;state.task=task;await refreshTaskSidebar();if(current())await openDelivery(taskId)};
  try{
    const [task,delivery]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}`),api(endpoint)]);
    if(!current())return;box.replaceChildren();
    if(!delivery.available){
      const results=await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=1`);if(!current())return;
      const text=document.createElement('p');text.textContent='生成待发布 ZIP，仅包含当前版本入选图片及可核对的 manifest.json；候补不进入包。生成后需发起人确认发布才可下载。合法停止时可部分交付，清单会保留目标、缺口和停止原因。';box.append(text);
      if(!results.available||results.counts.accepted===0){const note=document.createElement('p');note.textContent='当前没有可交付的有效素材，请修改方案后重新检索。';box.append(note);return}
      if(results.counts.gap!==0&&task.run_status!=='AWAITING_INPUT'){const note=document.createElement('p');note.textContent='补数尚未合法停止，暂不能部分交付。';box.append(note);return}
      card('打包范围',[['合格唯一素材',results.counts.eligible_unique],['入选图片',results.counts.accepted],['候补（不打包）',results.counts.reserve],['SHA-256 自动合并',results.counts.duplicate],...(results.group_progress||[]).map(row=>[row.label+' 文件数',`${row.accepted_count} / 目标 ${row.target_count}${row.gap?`（缺 ${row.gap}）`:''}`]),['去重规则','完全一致内容自动合并，无需人工复核'],['结果版本',results.result_set_digest]],[],box);
      action(results.counts.gap===0?'生成待发布包':'按当前有效素材生成部分交付包',async()=>{await api(endpoint+'/prepare',{method:'POST',body:{expected_task_version:task.task_version,result_set_digest:results.result_set_digest}});await refresh()});return;
    }
    const eligible=delivery.manifest.eligible_unique_count??((delivery.manifest.accepted_count??0)+(delivery.manifest.reserve_count??0));
    const duplicates=delivery.manifest.exact_duplicate_count??(delivery.manifest.members||[]).reduce((total,member)=>total+Math.max(0,(member.candidate_ids?.length||1)-1),0);
    const packaged=delivery.manifest.packaged_file_count??(delivery.manifest.members||[]).length;
    card(delivery.status==='published'?'交付已发布':'待发起人确认发布',[
      ['包 ID',delivery.package_id],['绑定版本',`revision ${delivery.revision} / generation ${delivery.generation}`],
      ['交付类型',delivery.manifest.completion==='partial'?'部分交付':'完整交付'],['合格唯一素材',eligible],['入选图片',delivery.manifest.accepted_count],['候补（不打包）',delivery.manifest.reserve_count],['SHA-256 自动合并',duplicates],['包内图片文件',packaged],...(delivery.manifest.groups||[]).map(row=>[row.label+' 目录',`${row.directory}/ · ${row.packaged_file_count} 个文件 / 目标 ${row.target_count}${row.gap?`（缺 ${row.gap}）`:''}`]),['缺口',delivery.manifest.gap??0],
      ['大小',`${(delivery.byte_count/1024).toFixed(1)} KiB`],['清单摘要',delivery.manifest_digest],['ZIP 摘要',delivery.zip_digest]],[],box);
    const list=document.createElement('details'),title=document.createElement('summary');title.textContent='查看包内成员';list.append(title);
    for(const member of delivery.manifest.members){const aliases=Math.max(0,(member.candidate_ids?.length||1)-1),row=document.createElement('p');row.textContent=member.file+' · '+member.candidate_id+' · '+member.content_digest+' · SHA-256 重复副本 '+aliases;list.append(row)}box.append(list);
    if(delivery.status==='staged')action('确认发布此包',async()=>{await api(endpoint+'/publish',{method:'POST',body:{package_id:delivery.package_id,manifest_digest:delivery.manifest_digest,expected_task_version:delivery.current_version,action:'confirm'}});await refresh()});
    else action('下载已发布 ZIP',async()=>{const response=await fetch(endpoint+'/'+encodeURIComponent(delivery.package_id)+'/download',{headers});if(!response.ok){const error=await response.json();throw new Error(error.message||'下载被拒绝')}const blob=await response.blob();if(!current())return;const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=delivery.package_id+'.zip';link.textContent='再次保存 ZIP';box.append(link);link.click();dialog.addEventListener('close',()=>{URL.revokeObjectURL(url);link.remove()},{once:true})});
  }catch(error){if(current())box.textContent='交付状态暂不可读：'+error.message}
}
'''
