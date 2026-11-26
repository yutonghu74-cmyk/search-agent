"""Dependency-free local Chat preview assets. Never embed credentials or production endpoints."""

CHAT_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DSH 检索需求体验</title><link rel="stylesheet" href="/chat.css"></head>
<body><a class="skip-link" href="#main-content">跳到主要内容</a><main id="main-content">
  <nav class="topbar" aria-label="主导航"><div class="topbar-start"><button id="toggle-history" class="secondary mobile-history-toggle" type="button" aria-controls="history-panel" aria-expanded="false">历史 Chat</button><a class="brand" href="/" aria-label="DSH 生产线路首页"><span class="brand-mark" aria-hidden="true">D</span><span>DSH Retrieval</span></a></div><div class="topbar-start"><a class="nav-link" href="/production">生产线路</a></div></nav>
  <div class="app-shell"><aside class="history panel" id="history-panel" aria-labelledby="history-heading"><div class="history-title"><h2 id="history-heading">历史 Chat</h2><div class="history-actions"><button class="icon-button" id="refresh-history" aria-label="刷新历史 Chat" title="刷新历史 Chat">↻</button><button class="icon-button mobile-history-close" id="close-history" aria-label="关闭历史 Chat" type="button">×</button></div></div><button class="secondary history-new-chat" id="new-task" type="button"><span aria-hidden="true">＋</span> 新建 Chat</button><div id="history-list" aria-live="polite"></div><nav id="history-pager" class="history-pager" aria-label="历史 Chat 分页" hidden><button id="history-prev" type="button" aria-label="上一页历史 Chat">‹</button><span id="history-page-label" aria-live="polite"></span><button id="history-next" type="button" aria-label="下一页历史 Chat">›</button></nav><div class="history-footer" aria-label="设置与环境"><button id="open-model-panel" type="button" aria-haspopup="dialog"><span aria-hidden="true">⚙</span>模型设置</button><a id="open-catalog-settings" href="/catalog"><span aria-hidden="true">▦</span>索引来源设置</a><button id="open-environment-info" type="button" aria-haspopup="dialog"><span aria-hidden="true">ⓘ</span>体验环境说明</button></div></aside>
  <div class="chat">
    <section class="conversation" aria-labelledby="conversation-heading"><div class="section-heading conversation-heading"><div><span class="section-kicker">CONVERSATION</span><h2 id="conversation-heading">对话</h2></div><span id="conversation-status" class="status-badge">先沟通，再检索</span></div><div id="timeline" class="timeline" role="log" aria-live="polite" aria-relevant="additions"><article class="message system"><h2>新建数据需求</h2><p>告诉我想找什么；不清楚的地方我会先问，确认检索词后才开始。</p></article></div></section>
    <section class="chat-composer panel" id="new-request" aria-labelledby="request-heading"><h2 class="sr-only" id="request-heading">与检索 Agent 对话</h2><label class="sr-only" for="request-text">输入消息</label><textarea id="request-text" rows="3" placeholder="直接说想找什么，例如：找 20 张狗图片，横图，JPG 格式" required></textarea><div class="chat-composer-footer"><button id="insert-request-example" class="secondary small" type="button">参考说法</button><details class="advanced-request"><summary><span class="advanced-label-closed">更多设置</span><span class="advanced-label-open">收起设置</span></summary><div class="advanced-fields"><div class="field-grid"><div class="field"><label for="group-parent">上级分类 <span class="optional">可选</span></label><input id="group-parent" placeholder="例如：动物"></div><div class="field"><label for="target-count">目标数量 <span class="optional">也可在对话中说明</span></label><input id="target-count" type="number" min="1" max="10000" placeholder="例如：100"></div></div><div class="field"><label for="group-lines">交付分组 <span class="optional">可选，每行一组</span></label><textarea id="group-lines" rows="3" placeholder="狗 | 金毛，柯基&#10;猫 | 橘猫，布偶猫"></textarea></div><div class="field refill-settings"><div class="field"><strong>历史批次去重</strong><p id="history-dedup-status" class="helper">仅本批内部去重；未选择历史基准。</p><button type="button" id="history-load" class="secondary">读取可选历史交付</button><label for="history-references">明确选择已交付快照（可多选）</label><select id="history-references" multiple size="3"></select><button type="button" id="history-apply" class="secondary">应用历史选择</button><button type="button" id="history-clear" class="secondary">不与历史批次去重</button></div><label class="choice-row" for="auto-refill"><input id="auto-refill" type="checkbox"><span>自动补足目标数量</span></label><p class="helper">只会在下方明确选择的已配置素材库内继续检索、生成增量候选并清洗。</p><p id="refill-library-status" class="helper">正在读取已配置素材库…</p><div id="auto-refill-fields" class="hidden"><label for="refill-library-ids">允许补数的素材库</label><select id="refill-library-ids" multiple size="3" aria-describedby="refill-library-status"></select><div class="field-grid"><div class="field"><label for="refill-max-rounds">最多补数轮次 <span class="optional">留空不限</span></label><input id="refill-max-rounds" type="number" min="1" step="1" placeholder="不限"></div><div class="field"><label for="refill-max-vlm-calls">最多 VLM 调用 <span class="optional">留空不限</span></label><input id="refill-max-vlm-calls" type="number" min="1" step="1" placeholder="不限"></div><div class="field"><label for="refill-no-progress-rounds">连续无进展停止</label><input id="refill-no-progress-rounds" type="number" min="1" step="1" value="2"></div></div></div></div></div></details><span id="composer-model-note" class="helper">正在检查模型…</span><button id="stop-request" class="secondary hidden" type="button">暂停</button><button id="prepare-task" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <section class="chat-composer panel hidden" id="feedback-box" aria-labelledby="feedback-heading"><h2 class="sr-only" id="feedback-heading">继续对话</h2><label class="sr-only" for="feedback-text">输入消息</label><textarea id="feedback-text" rows="3" placeholder="输入消息"></textarea><span id="feedback-model-note" class="hidden" aria-hidden="true"></span><div class="chat-composer-footer"><a id="search-progress-link" class="search-progress-link" href="/production">查看检索进度 ↗</a><button id="prepare-feedback" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <p id="status" role="status" aria-live="polite"></p>
  </div>
  <aside class="right-rail" aria-label="Chat 操作与检索结果">
    <section class="rail-card" id="task-sidebar" aria-labelledby="task-sidebar-heading"><div class="rail-heading"><div><span class="section-kicker">WORKFLOW</span><h2 id="task-sidebar-heading">检索流程</h2></div></div><div class="task-identity hidden" id="task-sidebar-identity"><span>需求 ID</span><code id="task-sidebar-id"></code><button id="open-candidate-window" class="secondary" type="button">检索候选 / 正式结果</button></div><p id="task-sidebar-summary" class="helper"></p><div id="task-sidebar-nodes" class="progress-nodes" aria-label="五节点进度：扩检索词、检索、清洗、补数、打包"></div><p id="retrieval-live-status" class="helper" role="status"></p></section>
    <section class="rail-card hidden result-workspace" id="result-sidebar" aria-labelledby="result-sidebar-heading"><div class="rail-heading"><div><span class="section-kicker">RESULTS</span><h2 id="result-sidebar-heading">结果</h2></div></div><details id="result-details" class="result-details"><summary><span id="result-compact-counts">查看素材与复核</span><span class="result-details-hint">展开 / 收起</span></summary><div id="result-counts" class="result-counts" aria-label="结果计数"></div><div class="result-tabs" role="tablist" aria-label="结果分类"><button type="button" data-result-view="accepted" role="tab" aria-selected="true" disabled>可用素材</button><button type="button" data-result-view="review" role="tab" aria-selected="false" disabled>需要我判断</button><button type="button" data-result-view="failed" role="tab" aria-selected="false" disabled>规则淘汰</button><button type="button" data-result-view="duplicate" role="tab" aria-selected="false" disabled>重复合并</button><button type="button" data-result-view="historical" role="tab" aria-selected="false" disabled>历史排重</button><button type="button" data-result-view="user_excluded" role="tab" aria-selected="false" disabled>我已排除</button></div><div id="result-list" class="result-list"></div></details></section>
  </aside></div><button id="history-backdrop" class="history-backdrop" type="button" aria-label="关闭历史 Chat"></button>
  <dialog id="model-panel-dialog" class="sidebar-dialog" aria-labelledby="model-heading"><section class="rail-card model-config" id="model-config" aria-labelledby="model-heading"><div class="rail-heading"><div><span class="section-kicker">MODELS</span><h2 id="model-heading">模型设置</h2></div><button id="close-model-panel" class="secondary" type="button" aria-label="关闭模型设置">关闭</button></div><div id="model-controls" class="model-controls"><span id="chat-model-badge" class="status-badge neutral">正在连接</span>
      <div class="field compact-field"><label for="dialogue-llm-model-input">对话模型</label><input id="dialogue-llm-model-input" list="dialogue-llm-model-list" autocomplete="off" placeholder="留空时沿用扩词模型"><datalist id="dialogue-llm-model-list"></datalist><select id="dialogue-llm-model" hidden aria-hidden="true"><option value="">沿用扩词模型</option></select></div>
      <div class="field compact-field"><label for="llm-model-input">当前 Chat · 扩词模型</label><input id="llm-model-input" list="llm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="llm-model-list"></datalist><select id="llm-model" hidden aria-hidden="true"><option value="">未选择</option></select></div>
      <div class="field compact-field"><label for="vlm-model-input">当前 Chat · 清洗语义判定模型</label><input id="vlm-model-input" list="vlm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="vlm-model-list"></datalist><select id="vlm-model" hidden aria-hidden="true"><option value="">未选择</option></select></div>
      <p id="model-status" class="helper">正在加载可用模型…</p>
      <details class="api-config"><summary>默认模型 · 新 Chat 使用</summary><div class="api-fields"><div class="field-grid"><div class="field"><label for="default-llm-model-input">默认 LLM</label><input id="default-llm-model-input" list="default-llm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="default-llm-model-list"></datalist><select id="default-llm-model" hidden aria-hidden="true"></select></div><div class="field"><label for="default-vlm-model-input">默认 VLM</label><input id="default-vlm-model-input" list="default-vlm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="default-vlm-model-list"></datalist><select id="default-vlm-model" hidden aria-hidden="true"></select></div></div><button id="save-default-models" type="button">保存默认模型</button><p id="default-model-status" class="helper" role="status">一套默认模型，供所有新 Chat 使用。</p></div></details>
      <button id="open-model-settings" class="secondary" type="button">配置模型服务</button>
      <details class="api-config"><summary>测试当前模型</summary><div class="actions"><button id="test-llm-model" type="button" class="secondary">测试 LLM</button><button id="test-vlm-model" type="button" class="secondary">测试 VLM</button></div><p id="model-test-status" class="helper" role="status">测试会调用所选模型，可能产生费用。</p></details></div>
    </section>
  </dialog>
  <dialog id="environment-info-dialog" class="sidebar-dialog" aria-labelledby="environment-info-title"><div class="rail-heading"><h2 id="environment-info-title">体验环境说明</h2><button id="close-environment-info" class="secondary" type="button" aria-label="关闭体验环境说明">关闭</button></div><p class="helper">固定体验范围：demo / default。页面尚不支持切换项目或子维度。</p><p id="build-version" class="helper">正在读取运行版本…</p><p class="helper">当前使用模拟身份和本地账本。测试索引检索不等于真实素材授权或清洗通过；模型建议在确认前不会执行。</p></dialog>
  <dialog id="model-settings-dialog" aria-labelledby="settings-title"><div class="settings-header"><h2 id="settings-title">模型设置</h2><button id="close-model-settings" class="secondary" type="button" aria-label="关闭模型设置">关闭</button></div>
    <p id="api-config-status" class="settings-status" role="status" aria-live="polite"></p>
    <section class="settings-step"><h3>1. 连接模型服务</h3>
      <div class="field"><label for="saved-api-connection">已保存的连接</label><select id="saved-api-connection"><option value="">新建连接</option></select></div>
      <details class="api-config" id="new-api-connection" open><summary>使用 API 与密钥配置</summary><div class="api-fields">
        <input id="api-provider" type="hidden" value="openai-compatible">
        <div class="field"><label for="api-name">连接名称（可选）</label><input id="api-name" maxlength="80" placeholder="不填则使用服务域名"></div>
        <div class="field"><label for="api-base-url">API Base URL（HTTPS）</label><input id="api-base-url" type="url" placeholder="API 完整地址，含协议" list="approved-model-urls" spellcheck="false"><datalist id="approved-model-urls"></datalist></div>
        <div class="field"><label for="api-key">API 密钥</label><input id="api-key" type="password" placeholder="输入新密钥" autocomplete="new-password" spellcheck="false"><p class="helper">密钥不写入浏览器存储；仅后端加密保存，不回显。</p></div>
        <p class="helper">支持 OpenAI-compatible 接口。请从地址建议中选择已批准的服务；新地址需由管理员加入允许列表。</p>
        <button id="save-api-config" type="button">连接并获取模型</button>
      </div></details>
      <div id="saved-connection-actions" class="hidden"><div class="actions"><button id="refresh-api-models" class="secondary" type="button">刷新模型列表</button></div><details class="api-config"><summary>更换密钥</summary><div class="api-fields"><label for="replacement-api-key">新密钥</label><input id="replacement-api-key" type="password" autocomplete="new-password"><button id="rotate-api-key" class="secondary" type="button">更新密钥</button></div></details></div>
      <p id="connection-model-status" class="helper"></p>
      <details class="api-config"><summary>手动填写模型 ID</summary><div class="api-fields">
        <div class="field-grid"><div class="field"><label for="custom-llm-model">LLM 模型 ID</label><input id="custom-llm-model" list="discovered-models" placeholder="模型 ID" spellcheck="false"></div><div class="field"><label for="custom-vlm-model">VLM 模型 ID</label><input id="custom-vlm-model" list="discovered-models" placeholder="模型 ID" spellcheck="false"></div></div><datalist id="discovered-models"></datalist>
        <button id="bind-api-models" class="secondary" type="button">保存模型 ID</button><p class="helper">保存后可从左下角“模型设置”选择，密钥无需再输入。</p>
      </div></details>
    </section>
  </dialog>
  <dialog id="cleaning-results-dialog" aria-labelledby="cleaning-results-title"><div class="settings-header"><h2 id="cleaning-results-title">清洗结果与合格预览</h2><button id="close-cleaning-results" class="secondary" type="button">关闭</button></div><div id="cleaning-results-content" aria-live="polite"></div></dialog>
  <dialog id="result-feedback-dialog" aria-labelledby="result-feedback-title"><form method="dialog" id="result-feedback-form"><div class="settings-header"><h2 id="result-feedback-title">处理结果素材</h2><button id="close-result-feedback" class="secondary" value="cancel" type="button">关闭</button></div><p id="result-feedback-impact" class="feedback-impact"></p><div class="field"><label for="result-feedback-reason-choice">原因</label><select id="result-feedback-reason-choice"><option value="">请选择原因</option><option>不符合本次需求</option><option>主体或场景不准确</option><option>画面质量不合格</option><option>包含不需要的文字或水印</option><option>人工确认符合需求</option><option value="custom">其他</option></select></div><div class="field"><label for="result-feedback-reason">补充说明</label><textarea id="result-feedback-reason" rows="3" maxlength="500" placeholder="说明判断依据，便于后续审计"></textarea></div><div class="actions"><button id="submit-result-feedback" value="default" type="submit">确认</button><button class="secondary" value="cancel" type="button" id="cancel-result-feedback">取消</button></div></form></dialog>
</main><script src="/chat.js"></script></body></html>"""

CHAT_HTML = CHAT_HTML.replace(
    '</textarea><div class="chat-composer-footer">',
    '</textarea><div class="query-image-input"><input id="query-image-file" class="sr-only" '
    'type="file" accept="image/jpeg,image/png"><label id="query-image-choose" '
    'class="query-image-choose" for="query-image-file">＋ 添加参考图</label>'
    '<span class="helper">单张静态 JPEG/PNG，最大 20 MB</span>'
    '<div id="query-image-preview" class="query-image-preview hidden">'
    '<img id="query-image-thumbnail" alt="已上传的参考图"><div>'
    '<strong id="query-image-name"></strong><p id="query-image-status" class="helper" '
    'role="status"></p><label class="choice-row query-image-exclude" for="query-image-exclude">'
    '<input id="query-image-exclude" type="checkbox"><span>排除与参考图完全相同的内容</span></label></div>'
    '<label class="secondary small query-image-replace" '
    'for="query-image-file">替换</label><button id="query-image-remove" '
    'class="secondary small" type="button">移除</button></div></div>'
    '<div class="chat-composer-footer">', 1)

CHAT_CSS = """
#cleaning-results-dialog{width:min(1080px,calc(100vw - 24px));max-height:90vh;overflow:auto;border:1px solid #dedfdb;border-radius:16px;padding:20px;color:#202421}#cleaning-results-dialog .settings-header{position:sticky;top:-20px;background:white;padding:12px 0;z-index:2}#cleaning-results-content{overflow-wrap:anywhere}#cleaning-results-content summary{display:block;cursor:pointer;padding:14px;background:#edf7f0;border-radius:8px;font-weight:650}#cleaning-results-content details{margin:16px 0}#cleaning-results-content select{max-width:240px;margin:12px 0}.cleaning-items{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:16px}.cleaning-hit{min-width:0;border:1px solid #dedfdb;border-radius:10px;padding:12px!important;overflow-wrap:anywhere}.cleaning-hit strong{display:block;font-size:12px}.cleaning-hit img{width:100%;height:210px;object-fit:contain;background:#f4f6f3;margin-top:10px}.cleaning-execution{padding:12px;background:#f4f6f3;border-radius:10px;white-space:pre-wrap}.cleaning-counts{padding:14px;border-left:4px solid #246646;background:#edf7f0}.cleaning-head{display:flex;align-items:center;gap:8px;margin-bottom:8px}.cleaning-head strong{display:inline;font-size:12px;overflow-wrap:anywhere}.cleaning-badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;white-space:nowrap}.cleaning-badge-retained,.cleaning-badge-pass{background:#eaf5ec;color:#245b3e;border:1px solid #d4e5d8}.cleaning-badge-rejected,.cleaning-badge-fail{background:#fff0ee;color:#9f2525;border:1px solid #f0d4cf}.cleaning-badge-review,.cleaning-badge-unknown{background:#fff8e9;color:#795d30;border:1px solid #f0e0c0}.cleaning-status-retained{border-color:#d4e5d8}.cleaning-status-rejected{border-color:#f0d4cf}.cleaning-status-review{border-color:#f0e0c0}.cleaning-reasons{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}.cleaning-reasons li{display:grid;grid-template-columns:auto auto 1fr;align-items:start;gap:6px;font-size:11px;line-height:1.5}.cleaning-rule-id{color:var(--ink);font-weight:600;overflow-wrap:anywhere}.cleaning-reason-code{color:var(--muted);overflow-wrap:anywhere}.cleaning-reason-summary{grid-column:1/-1;color:var(--muted);overflow-wrap:anywhere;padding-left:2px}
#result-list .card>img{display:block;width:100%;height:220px;object-fit:contain;background:#f4f6f3;border-radius:10px;margin:12px 0}
.result-details>summary{cursor:pointer;font-size:12px;line-height:1.7;color:var(--ink)}.result-details-hint{display:block;margin-left:15px;font-size:11px;color:var(--muted)}.result-details[open]>summary{margin-bottom:12px}.result-details .result-list{max-height:55vh;overflow:auto;overscroll-behavior:contain}.result-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.result-count{padding:8px;border-radius:8px;background:#f4f6f3;text-align:center;font-size:11px;color:var(--muted)}.result-count strong{display:block;color:var(--ink);font-size:16px}.result-tabs{display:flex;gap:6px;overflow:auto;margin:2px 0 10px}.result-tabs button{white-space:nowrap;padding:7px 10px}.result-tabs button[aria-selected="false"]{background:white;color:var(--ink);border:1px solid #dedfdb}.feedback-impact{padding:12px;border-left:4px solid #246646;background:#edf7f0;border-radius:8px;white-space:pre-wrap}#result-feedback-dialog{width:min(520px,calc(100vw - 32px));border:1px solid #dedfdb;border-radius:16px;padding:22px;color:#202421}#result-feedback-form{display:grid;gap:14px}
dialog#model-settings-dialog{width:min(680px,calc(100vw - 32px));max-height:85vh;box-sizing:border-box;border:1px solid #dedfdb;border-radius:16px;padding:24px;color:#202421;box-shadow:0 20px 80px #0003}dialog::backdrop{background:#17221c66}.settings-header{display:flex;align-items:center;justify-content:space-between;gap:16px}.settings-header h2{margin:0;font-size:20px}.settings-step{display:grid;gap:12px;margin-top:22px}.settings-step h3{margin:0;font-size:15px}.settings-status{position:sticky;top:0;z-index:2;margin:12px 0 0;padding:12px;border-radius:8px;background:#edf7f0;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}.settings-status:empty{display:none}.settings-status[data-error="true"]{background:#fff0ee;color:#9f2525}.settings-step>button{justify-self:start}
:root{font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#202421;background:#f7f7f5;line-height:1.5;--ink:#202421;--muted:#6f756f;--line:#dedfdb;--panel:#fff;--green:#246646;--green-2:#347b55;--mint:#edf7f0;--shadow:0 10px 32px #1c2c2210}*{box-sizing:border-box}
body{margin:0;background:#f7f7f5}main{max-width:1500px;margin:auto;min-height:100vh;padding:16px 22px 64px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.skip-link{position:fixed;top:10px;left:10px;z-index:20;transform:translateY(-160%);background:#202421;color:white;padding:10px 14px;border-radius:8px}.skip-link:focus{transform:none}.topbar{display:flex;align-items:center;justify-content:space-between;height:44px}.brand{display:flex;align-items:center;gap:10px;color:var(--ink);font-weight:750;text-decoration:none}.brand-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#202421;color:white;font-family:Georgia,serif}.nav-link{color:#4c554f;text-decoration:none;font-size:13px;font-weight:650;padding:8px 10px;border-radius:8px}.nav-link:hover{background:#eceeea}.context-bar{display:flex;align-items:center;gap:9px;width:max-content;margin:8px auto 14px;background:white;border:1px solid var(--line);border-radius:10px;padding:7px 11px;font-size:12px}.context-note{color:var(--muted);padding-left:9px;border-left:1px solid var(--line)}.status-dot{flex:0 0 auto;width:7px;height:7px;border-radius:50%;background:#43a56e;box-shadow:0 0 0 3px #dff1e5}.project-divider{width:1px;height:14px;background:var(--line)}
.app-shell{display:grid;grid-template-columns:240px minmax(460px,1fr) 300px;gap:16px;align-items:start}.panel,.rail-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.history,.right-rail{position:sticky;top:14px;max-height:calc(100vh - 28px)}.history{padding:12px;overflow:auto}.history-title,.section-heading,.rail-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.history h2,.section-heading h2,.rail-heading h2{font-size:16px;line-height:1.25;margin:2px 0}.section-kicker{font-size:10px;font-weight:750;letter-spacing:.14em;color:#668071}.helper{font-size:11px;color:var(--muted);margin:6px 0}.history-section{font-size:10px;font-weight:750;letter-spacing:.1em;color:#7a817c;margin:16px 6px 5px;padding-top:10px;border-top:1px solid #ededea}.icon-button{display:grid;place-items:center;width:30px;height:30px;padding:0;border-radius:8px;background:#f0f1ee;color:#465049;font-size:16px}.history-entry{display:grid;grid-template-columns:minmax(0,1fr) 28px 28px;gap:3px;align-items:center;margin-top:4px}.history-item{display:block;width:100%;min-width:0;text-align:left;background:transparent;color:#303631;border:1px solid transparent;border-radius:9px;padding:9px;box-shadow:none}.history-item:hover,.rename-chat:hover,.delete-chat:hover{background:#f1f3ef}.history-item.active{background:#e9eee9;border-color:#d8ded9}.history-item span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-item small{display:block;color:var(--muted);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.rename-chat,.delete-chat{display:grid;place-items:center;width:28px;height:28px;padding:0;border:0;background:transparent;color:#777f79}.delete-chat{color:#9a4037}.empty{font-size:12px;color:var(--muted);padding:10px 7px}.chat{display:grid;gap:14px;min-width:0}
.conversation-heading{padding:2px 4px}.timeline{display:grid;gap:12px;margin-top:10px;padding:16px;border:1px solid var(--line);border-radius:14px;background:#f0f1ee;min-height:180px}.message{max-width:88%;padding:14px 16px;border:1px solid #e0e2de;border-radius:14px;background:white;box-shadow:0 5px 18px #33483b0a}.message.system{border-top-left-radius:4px}.message.assistant{border-left:3px solid #4f956b;background:#f8fcf9;border-top-left-radius:4px}.message.user{justify-self:end;background:#2f5945;color:white;border-color:#2f5945;border-top-right-radius:4px}.message h2{font-size:14px;margin:0 0 6px}.message p{font-size:13px;margin:0;white-space:pre-wrap}.analysis-details{margin-top:10px;border-top:1px solid #dce9df;padding-top:8px;font-size:12px}.analysis-details summary{cursor:pointer;color:#355a42;font-weight:700}.analysis-details ul{margin:8px 0 0;padding-left:18px}.analysis-details li{margin:5px 0;word-break:break-word}.card{border-left:3px solid #65a47c}.card dl{display:grid;grid-template-columns:118px minmax(0,1fr);gap:7px 12px;margin:10px 0 0;font-size:12px}.card dt{color:var(--muted)}.card dd{margin:0;word-break:break-word}.actions{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}
.composer{padding:18px;display:grid;gap:14px}.chat-composer{padding:12px;background:white}.chat-composer>textarea{border:0;box-shadow:none!important;min-height:74px;padding:8px}.chat-composer-footer{position:relative;display:flex;align-items:end;justify-content:space-between;gap:10px;border-top:1px solid #eceeeb;padding-top:9px}.advanced-request{flex:1;min-width:0}.advanced-label-open{display:none}.advanced-request[open] .advanced-label-closed{display:none}.advanced-request[open] .advanced-label-open{display:inline}.advanced-request summary{width:max-content;cursor:pointer;color:#5f6862;font-size:12px;font-weight:650;padding:6px 2px}.advanced-fields{position:absolute;z-index:20;bottom:calc(100% + 10px);left:0;right:0;display:grid;gap:12px;max-height:min(60dvh,480px);overflow-y:auto;overscroll-behavior:contain;padding:16px;border:1px solid var(--line);border-radius:12px;background:white;box-shadow:0 8px 32px #1c2c2226;text-align:left}.advanced-request>summary:focus-visible{outline:2px solid #2f5945;outline-offset:3px;border-radius:4px}.chat-composer-footer>button{border-radius:999px;padding:9px 14px}.field-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}.field{display:grid;gap:5px}.field label{font-size:12px;font-weight:700;color:#3b443e}.field label small{color:var(--muted);font-weight:600}.optional{color:#7a817c;font-weight:500;margin-left:4px}.form-footer,.preview-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}.preview-actions{justify-content:flex-start}.status-badge{display:inline-flex;align-items:center;border-radius:999px;padding:4px 8px;background:#e0f0e5;color:#245b3e;font-size:10px;font-weight:750;white-space:nowrap}.status-badge.neutral{background:#eef0ed;color:#69716c}
.search-diagnostics{border-top:1px solid #eceeeb;padding-top:8px}.chat-composer-footer .search-diagnostics{border-top:0;padding-top:0}.search-diagnostics summary{cursor:pointer;color:#6f756f;font-size:12px}.search-diagnostics .preview-actions{flex-wrap:wrap;justify-content:flex-start;margin-top:10px}
.search-choice{display:flex;align-items:center;gap:9px;margin-top:8px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:12px;cursor:pointer}.search-choice:focus-within{outline:2px solid var(--green);outline-offset:2px}.search-choice input{accent-color:var(--green)}
.progress-nodes{display:grid;grid-template-columns:1fr;gap:14px;padding:4px 1px 8px}.node-card{position:relative;background:white;border:1px solid #dce1dc;border-radius:12px;min-width:0;overflow:visible}.progress-nodes>.node-card:not(:last-child)::after{content:'↓';position:absolute;left:18px;bottom:-15px;color:#87a392;font-size:12px;font-weight:800;z-index:1}.node-card.workflow-active{border-color:#4f956b;box-shadow:0 0 0 2px #d9eddf}.node-card.workflow-done{background:#f3f8f4}.node-card.search-active{border-color:#78b18b;box-shadow:0 0 0 2px #d9eddf}.node-card.search-unavailable{border-color:#dfbd75;box-shadow:0 0 0 2px #f8ecd0}.node-card summary,.node-card .node-heading{display:grid;grid-template-columns:28px minmax(0,1fr) auto;align-items:center;gap:8px;padding:11px;list-style:none}.node-card summary{cursor:pointer}.node-card summary::-webkit-details-marker{display:none}.node-index{display:grid;place-items:center;width:26px;height:26px;border-radius:8px;background:var(--mint);color:var(--green);font-size:11px;font-weight:800}.node-title{min-width:0;font-size:12px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.node-status{font-size:11px;font-weight:750;color:#496257;background:#eef3ef;padding:3px 7px;border-radius:999px;white-space:nowrap}.node-body{border-top:1px solid #edf0ed;padding:10px;font-size:11px;color:var(--muted)}.node-body p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.node-body p+p{margin-top:8px}.node-terms{max-height:340px;overflow:auto}.node-term-group{padding:7px 0;border-top:1px solid #edf0ed}.node-term-group:first-child{border-top:0;padding-top:0}.node-term-group strong{display:block;color:var(--ink);font-size:11px;margin-bottom:3px}.node-candidates{max-height:340px;overflow:auto;margin-top:8px}.node-candidates .search-hit{padding:8px 0}
.right-rail{display:grid;gap:10px;overflow:auto}.task-identity{display:grid;gap:3px;font-size:11px;color:var(--muted)}.task-identity code{color:var(--ink);font-size:11px;overflow-wrap:anywhere}.rail-card{padding:14px;display:grid;gap:12px}.rail-heading{margin-bottom:2px}.compact-field select{font-size:12px}.api-config{border-top:1px solid #eceeeb;padding-top:10px}.api-config summary{cursor:pointer;font-size:12px;font-weight:700;color:#3f4b43}.api-fields{display:grid;gap:10px;padding-top:12px}.rail-note{display:flex;gap:10px;padding:12px;color:#5f6862;background:#f0f2ef;border-radius:12px}.rail-note strong{font-size:12px}.rail-note p{font-size:11px;margin:2px 0 0}.rail-note .status-dot{margin-top:5px}
textarea,input,select{width:100%;font:inherit;color:var(--ink);border:1px solid #c7ccc7;border-radius:9px;padding:9px 10px;background:white;outline:none;transition:border-color .15s,box-shadow .15s}textarea{resize:vertical;min-height:48px}textarea:hover,input:hover,select:hover{border-color:#98a39b}textarea:focus,input:focus,select:focus{border-color:var(--green-2);box-shadow:0 0 0 3px #347b5520}button{font:650 13px inherit;border:1px solid transparent;border-radius:9px;padding:9px 13px;background:var(--green);color:white;cursor:pointer;transition:transform .12s,background .12s}button:hover:not(:disabled){background:#1d5239;transform:translateY(-1px)}button.secondary{background:#edf1ed;color:#33453a;border-color:#dbe1dc}button.small{padding:6px 9px;font-size:11px}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #e4b83f;outline-offset:2px}button:disabled{opacity:.55;cursor:wait;transform:none}.hidden{display:none!important}#status{position:sticky;bottom:10px;z-index:4;color:#7f3026;background:#fff7f5;border-radius:9px;min-height:0;margin:0;padding:0 10px}#status:not(:empty){padding:8px 10px;border:1px solid #ecc7c1;box-shadow:0 8px 24px #6d2d2515}pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:#f4f6f3;padding:12px;border-radius:10px;border:1px solid #e1e5e1}.catalog-page{max-width:1000px}.catalog-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}.catalog-grid .wide{grid-column:1/-1}
.choice-row{display:flex;align-items:center;gap:8px;font-weight:650}.choice-row input{width:auto}.refill-settings{border-top:1px solid var(--line);padding-top:12px}.refill-settings select{min-height:74px}
@media(max-width:1180px){main{padding-inline:12px}.app-shell{grid-template-columns:170px minmax(0,1fr) 220px;gap:10px}.right-rail{grid-template-columns:1fr}.rail-note{display:none}.history{padding:9px}.history-entry{grid-template-columns:minmax(0,1fr) 26px}.history-item{padding:8px 6px}.rename-chat{width:26px}.rail-card{padding:11px}}
@media(max-width:900px){main{padding:14px 14px 56px}.app-shell{grid-template-columns:1fr}.chat{grid-column:1;order:1}.right-rail{position:static;max-height:none;grid-column:1;order:2;grid-template-columns:1fr}.history{position:static;max-height:none;grid-column:1;order:3}.context-bar{width:100%;justify-content:center}.context-note{display:none}.progress-nodes{grid-template-columns:1fr;overflow:visible}.node-card:not(:last-child)::after{display:none}}
@media(max-width:560px){.field-grid,.catalog-grid{grid-template-columns:1fr}.catalog-grid .wide{grid-column:auto}.message{max-width:100%}.timeline{padding:10px}.card dl{grid-template-columns:1fr;gap:2px}.card dd{margin-bottom:6px}.form-footer,.preview-actions{align-items:stretch;flex-direction:column}.form-footer button,.preview-actions button{width:100%}.section-heading{align-items:flex-start}.composer{padding:15px}.brand span:last-child{display:none}}
.advanced-settings{border-top:1px solid var(--line);padding-top:8px}.advanced-settings summary{cursor:pointer;color:var(--muted);font-size:12px}.advanced-settings>div{margin-top:10px}.message.assistant .actions{margin-top:12px}.message.assistant textarea{margin-top:10px;min-height:105px;font-size:12px}.search-hit{display:grid;gap:5px;padding:9px 0;border-top:1px solid #dce9df;font-size:12px}.search-hit strong{font-size:12px}.search-hit small{color:var(--muted)}.search-tags{display:flex;flex-wrap:wrap;gap:5px}.search-tag{display:inline-flex;max-width:100%;overflow-wrap:anywhere;padding:2px 7px;border:1px solid #d4e5d8;border-radius:999px;background:#eaf5ec;color:#245b3e;font-size:11px}
main:not(.catalog-page){height:100dvh;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding-bottom:16px}.topbar{flex:none}.topbar-start,.history-actions{display:flex;align-items:center;gap:8px}.context-bar{flex:none}.app-shell{flex:1;min-height:0;grid-template-rows:minmax(0,1fr)}.history,.right-rail{position:static;top:auto;max-height:none;height:100%;min-height:0;overflow:auto}.history{display:flex;flex-direction:column;overflow:hidden}.history-title{flex:none;background:white;padding:10px 0}.history-new-chat{flex:none;width:100%;display:flex;align-items:center;justify-content:flex-start;gap:6px;margin:0 0 10px;padding:9px 11px;border-radius:10px;font-size:13px;font-weight:600;box-shadow:none}#history-list{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain}.history-pager{flex:none;display:flex;align-items:center;justify-content:center;gap:12px;border-top:1px solid #ededea;margin-top:8px;padding:9px 0 2px;color:var(--muted);font-size:11px}.history-pager[hidden]{display:none}.history-pager button{display:grid;place-items:center;width:26px;height:26px;padding:0;border:1px solid transparent;border-radius:7px;background:transparent;color:#4c6052;font-size:20px;line-height:1;box-shadow:none}.history-pager button:hover:not(:disabled){background:#edf2ed;color:#214c32;transform:none}.history-pager button:disabled{opacity:.3;cursor:default}.right-rail{align-content:start;overscroll-behavior:contain}.chat{display:flex;flex-direction:column;height:100%;min-height:0}.conversation{display:flex;flex:1;flex-direction:column;min-height:0}.conversation-heading{flex:none;padding:4px 4px 10px;background:#f7f7f5}.conversation-heading>div{min-width:0}.conversation-heading h2{max-width:40vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.timeline{flex:1;min-height:0;overflow:auto;align-content:start;margin-top:0;overscroll-behavior:contain}.chat-composer{flex:none;box-shadow:0 8px 28px #1c2c2218}.search-progress-link{color:var(--accent,#2d5947);font-size:13px;text-decoration:none;padding:6px 0}.search-progress-link:hover{text-decoration:underline}.search-progress-link:focus-visible{outline:2px solid currentColor;outline-offset:4px}.right-rail{cursor:default}.right-rail :is(a,button:not(:disabled),summary){cursor:pointer}.right-rail button:disabled{cursor:default}.model-controls,.result-list{display:grid;gap:12px;min-width:0}.result-list .message{max-width:100%;min-width:0;box-shadow:none}.mobile-history-toggle,.mobile-history-close,.history-backdrop{display:none}
@media(max-width:900px){dialog.sidebar-dialog{left:16px;bottom:16px}main:not(.catalog-page){height:auto;min-height:100dvh;overflow:visible;padding:10px 14px 48px}.topbar{position:sticky;top:0;z-index:12;background:#f7f7f5}.context-bar{position:sticky;top:44px;z-index:11;margin:4px auto 10px}.app-shell{display:block;min-height:0;flex:none}.chat{height:auto;min-height:calc(100dvh - 150px)}.conversation{min-height:0}.timeline{min-height:260px;max-height:calc(100dvh - 300px)}.chat-composer{position:sticky;bottom:8px;z-index:8}.right-rail{height:auto;max-height:none;overflow:visible;margin-top:16px}.history{position:fixed;top:0;left:0;z-index:31;width:min(86vw,320px);height:100dvh;max-height:none;border-radius:0 14px 14px 0;transform:translateX(-110%);transition:transform .2s;box-shadow:0 0 30px #0003}.history.is-open{transform:translateX(0)}.history-backdrop.is-open{display:block;position:fixed;inset:0;z-index:30;border:0;border-radius:0;background:#17221c80}.mobile-history-toggle,.mobile-history-close{display:inline-flex}.mobile-history-toggle{width:auto;padding:6px 9px;font-size:11px}.history-title{top:-9px}.brand span:last-child{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
.search-hit img{display:block;width:min(100%,220px);height:140px;object-fit:contain;background:#f1f4f1;border:1px solid #dce9df;border-radius:8px}.search-hit .preview-note{color:#795d30}
.history-footer{flex:none;display:grid;gap:2px;margin-top:8px;padding-top:8px;border-top:1px solid #ededea}.history-footer button,.history-footer a{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;text-align:left;background:transparent;border:0;border-radius:8px;color:#47534b;font-size:12px;box-shadow:none;text-decoration:none;cursor:pointer}.history-footer button span,.history-footer a span{width:18px;text-align:center;font-size:16px;color:#68746b}.history-footer button:hover:not(:disabled),.history-footer a:hover{background:#f0f2ef;color:#263a2e;transform:none}
dialog.sidebar-dialog{position:fixed;top:auto;left:max(16px,calc((100vw - 1500px)/2 + 270px));bottom:16px;margin:0;width:min(420px,calc(100vw - 32px));max-height:min(80vh,680px);padding:16px;overflow:auto;border:1px solid #d6dbd6;border-radius:14px;background:white;color:var(--ink);box-shadow:0 18px 60px #17221c35}dialog.sidebar-dialog::backdrop{background:#17221c33}dialog.sidebar-dialog .model-config{border:0;box-shadow:none;padding:0;gap:12px}dialog.sidebar-dialog .model-controls{display:grid;gap:12px}dialog.sidebar-dialog .rail-heading h2{margin:0;font-size:16px}dialog.sidebar-dialog .helper{font-size:12px}
"""

CHAT_JS = r"""
const state={task:null,draft:null,feedback:null,feedbackReviewBubble:null,feedbackManualCard:null,expansionPreviewBubble:null,expansionPreviewToken:null,expansionPreviewPlan:null,confirmedRevision:null,conversationId:null,resumeDraft:null,connection:null,firstSearchPreview:null,firstSearchProposal:null,firstSearchChoices:[],candidateManifest:null,cleaningProposal:null,cleaningOperation:null,searchAttempt:null,targetCountTouched:false,modelChoiceTouched:false,proposedExpansion:null,confirmedCount:null,guidedResultBubble:null,guidedPhase:'input',dialogueId:null,dialogueVersion:0,dialogueStage:'request',requestController:null,expansionJob:null,expansionJobBubble:null,expansionPollTimer:null,refillLibrariesAvailable:false,eventTimer:null,eventController:null,eventTaskId:null,eventSequence:0,eventBusy:false};
function recordChatTiming(stage,ms){if(!Number.isFinite(ms)||ms<0)return;try{const key='dsh-chat-timing-v1',rows=JSON.parse(sessionStorage.getItem(key)||'[]');rows.push({stage,ms:Math.round(ms),at:Date.now()});sessionStorage.setItem(key,JSON.stringify(rows.slice(-60)))}catch{}}
function chatTimingSummary(){let rows=[];try{rows=JSON.parse(sessionStorage.getItem('dsh-chat-timing-v1')||'[]')}catch{}const result={};for(const stage of new Set(rows.map(row=>row.stage))){const values=rows.filter(row=>row.stage===stage&&Number.isFinite(row.ms)).map(row=>row.ms).sort((a,b)=>a-b);if(values.length)result[stage]={n:values.length,p50_ms:values[Math.ceil(values.length*.5)-1],p95_ms:values[Math.ceil(values.length*.95)-1]}}return result}
window.dshChatTimingSummary=chatTimingSummary;

const pipelineResponseTraces=new WeakMap();
function linkPipelineRender(target,source){if(target&&source&&typeof target==='object'&&typeof source==='object')pipelineResponseTraces.set(target,pipelineResponseTraces.get(source))}
function pipelineEndpoint(path){if(/\/(?:expansion-jobs|retrieval-agent\/terms)(?:\/|$)/.test(path))return 'expansion';if(/\/dialogue(?:\/|$)|\/dialogues(?:\/|$)/.test(path))return 'dialogue';if(/\/first-search(?:\/|$)/.test(path))return 'search';if(/\/workspace(?:\?|$)/.test(path))return 'workspace';if(/\/results(?:\?|$)/.test(path))return 'results';return 'workflow'}
function pipelineTimingRecords(){try{const rows=JSON.parse(sessionStorage.getItem('dsh-pipeline-timing-v1')||'[]');return Array.isArray(rows)?rows:[]}catch{return []}}
function recordPipelineTiming(row){try{const safe={stage:row.stage,at:Date.now(),outcome:row.outcome||'succeeded'};for(const key of ['ms','headers_ms','body_ms','server_ms','status'])if(Number.isFinite(row[key])&&row[key]>=0)safe[key]=Math.round(row[key]*1000)/1000;if(/^[a-f0-9]{32}$/.test(row.trace_id||''))safe.trace_id=row.trace_id;if(['expansion','dialogue','search','workspace','results','workflow'].includes(row.endpoint))safe.endpoint=row.endpoint;sessionStorage.setItem('dsh-pipeline-timing-v1',JSON.stringify([...pipelineTimingRecords(),safe].slice(-200)))}catch{}}
function measureChatRender(stage,callback,source){const started=performance.now(),trace_id=source&&pipelineResponseTraces.get(source);let outcome='failed';try{const result=callback();outcome='succeeded';return result}finally{recordPipelineTiming({stage,trace_id,outcome,ms:performance.now()-started});if(outcome==='succeeded'&&typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(()=>recordPipelineTiming({stage:stage+'_frame_wait',trace_id,ms:performance.now()-started})))}}
window.dshPipelineTimingRecords=pipelineTimingRecords;

const headers={'Content-Type':'application/json','X-Tenant-Id':'tenant-a','X-User-Id':'user-a'};
const $=id=>document.getElementById(id); const timeline=$('timeline');
const key=prefix=>prefix+'-'+crypto.randomUUID();
async function api(path,options={}){
 const method=(options.method||'GET').toUpperCase();
 if(method!=='GET'){invalidateTaskReads();try{return await apiRequest(path,options)}finally{invalidateTaskReads()}}
 // Only identical, uncancellable status reads share transport; never retain a completed response.
 if(options.signal||options.fresh||options.body||options.keyValue||options.retryFailed||options.asyncCommand||!/^\/v1\/retrieval\/tasks\/[^/?]+\/(workspace|progress)$/.test(path))return apiRequest(path,options);
 const identity=JSON.stringify([path,Object.entries(headers).sort(([a],[b])=>a.localeCompare(b))]);
 let pending=taskReads.get(identity);
 if(!pending){pending=apiRequest(path,options);taskReads.set(identity,pending)}
 try{const source=await pending,data=structuredClone(source);linkPipelineRender(data,source);for(const name of ['progress','results','items'])if(data?.[name]&&typeof data[name]==='object')linkPipelineRender(data[name],source[name]);return data}
 finally{if(taskReads.get(identity)===pending)taskReads.delete(identity)}
}
const taskReads=new Map();
function invalidateTaskReads(){taskReads.clear()}
async function apiRequest(path,{method='GET',body,keyValue,signal,retryFailed=false,asyncCommand=false}={}){
 const h={...headers};if(keyValue)h['Idempotency-Key']=keyValue;if(retryFailed)h['X-Conversation-Retry']='true';if(asyncCommand)h.Prefer='respond-async';let r,data;const started=performance.now();let headersAt=null,trace_id=null,outcome='failed',server_ms=null;
 try{r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined,signal});headersAt=performance.now();trace_id=r.headers?.get?.('X-Performance-Trace-Id');const server=r.headers?.get?.('Server-Timing')?.match(/(?:^|,\s*)pipeline;dur=([0-9.]+)/);if(server)server_ms=Number(server[1]);data=await r.json();outcome=r.ok?'succeeded':'failed';if(data&&typeof data==='object'){pipelineResponseTraces.set(data,trace_id);for(const name of ['progress','results','items'])if(data[name]&&typeof data[name]==='object')pipelineResponseTraces.set(data[name],trace_id)}}
 catch(cause){if(cause.name==='AbortError'){outcome='aborted';throw cause}const invalid=cause instanceof SyntaxError;
  const error=new Error(invalid?'服务返回了无法读取的响应，请稍后重试。':'与服务的连接中断，请在服务恢复后重试。');
  error.code=invalid?'INVALID_RESPONSE':'NETWORK_UNAVAILABLE';error.detail=error.message;throw error}
 finally{recordPipelineTiming({stage:'http',endpoint:pipelineEndpoint(path),trace_id,outcome,ms:performance.now()-started,headers_ms:headersAt===null?null:headersAt-started,body_ms:headersAt===null?null:performance.now()-headersAt,server_ms,status:r?.status})}
 if(!r.ok){const error=new Error(`${data.code||r.status}: ${data.message||'请求失败'}`);error.code=data.code||String(r.status);error.detail=data.message||'请求失败';throw error}return data;
}
function stopTaskEvents(){invalidateTaskReads();eventRefreshEpoch++;clearTimeout(eventRefreshTimer);eventRefreshTimer=null;eventRefreshDirty=false;eventRefreshResults=false;eventOutputVersions=null;if(state.eventTimer)clearTimeout(state.eventTimer);state.eventTimer=null;if(state.eventController)state.eventController.abort();state.eventController=null;state.eventTaskId=null;state.eventSequence=0;state.eventBusy=false}
let eventRefreshTimer=null,eventRefreshEpoch=0,eventRefreshDirty=false,eventRefreshResults=false,eventOutputVersions=null;
function scheduleTaskEventRefresh(taskId,event=null){
 if(state.eventTaskId!==taskId)return;invalidateTaskReads();eventRefreshDirty=true;
 const payload=event?.payload,versions=payload?.output_versions;
 if(!versions||!eventOutputVersions||['cleaning','packaging'].some(key=>versions[key]!==eventOutputVersions[key]))eventRefreshResults=true;
 if(versions)eventOutputVersions=versions;
 if(eventRefreshTimer===null&&!state.eventBusy)eventRefreshTimer=setTimeout(()=>{eventRefreshTimer=null;refreshTaskEventSnapshot(taskId).catch(()=>{$('retrieval-live-status').textContent='更新暂不可用，将在重连后重试'})},200);
}
async function refreshTaskEventSnapshot(taskId){
 if(state.eventBusy||state.eventTaskId!==taskId||state.task?.task_id!==taskId)return;
 const epoch=eventRefreshEpoch,needResults=eventRefreshResults;eventRefreshDirty=false;eventRefreshResults=false;state.eventBusy=true;
 const current=()=>epoch===eventRefreshEpoch&&state.eventTaskId===taskId&&state.task?.task_id===taskId;
 try{
  const summary=await api(`/v1/retrieval/tasks/${taskId}/workspace`);if(!current())return;
  const progress=summary.progress||await api(`/v1/retrieval/tasks/${taskId}/progress`);if(!current())return;
  const result=needResults?await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`):state.currentResults;if(!current())return;
  if(needResults){const latest=await api(`/v1/retrieval/tasks/${taskId}/workspace`,{fresh:true});if(!current())return;if(latest.task_version!==summary.task_version){eventRefreshDirty=true;eventRefreshResults=true;return}}
  state.task=summary;showProgress(progress,result);if(needResults)showResults(result,taskId);
  $('retrieval-live-status').textContent=`SSE 已恢复到事件序列 ${state.eventSequence} · ${new Date().toLocaleTimeString()}`;
 }catch(error){if(current()){if(['NOT_FOUND','PROJECT_ACCESS_DENIED','ACCESS_DENIED'].includes(error.code)){stopTaskEvents();return}eventRefreshDirty=true;eventRefreshResults=eventRefreshResults||needResults}throw error}
 finally{if(current()){state.eventBusy=false;if(eventRefreshDirty&&eventRefreshTimer===null)eventRefreshTimer=setTimeout(()=>{eventRefreshTimer=null;refreshTaskEventSnapshot(taskId).catch(()=>{})},500)}}
}
async function pollTaskEvents(){const taskId=state.eventTaskId;if(!taskId||state.eventBusy||document.hidden||state.task?.task_id!==taskId)return;try{const page=await api(`/v1/retrieval/tasks/${taskId}/events?after_sequence=${state.eventSequence}&limit=100&compact=true`);if(state.eventTaskId!==taskId||state.task?.task_id!==taskId)return;state.eventSequence=page.next_sequence;for(const event of page.events)scheduleTaskEventRefresh(taskId,event)}catch(error){if(error.code==='EVENT_HISTORY_EXPIRED'){state.eventSequence=0;$('retrieval-live-status').textContent='事件历史已过期，正在从权威快照恢复';return}if(['NOT_FOUND','REVISION_CONFLICT'].includes(error.code)){stopTaskEvents();return}$('retrieval-live-status').textContent='实时更新暂不可用；页面保留最后一次权威快照'}}
async function connectTaskEventStream(){const taskId=state.eventTaskId;if(!taskId||state.task?.task_id!==taskId)return;if(document.hidden){state.eventTimer=setTimeout(connectTaskEventStream,1000);return}const controller=new AbortController();state.eventController=controller;try{const response=await fetch(`/v1/retrieval/tasks/${taskId}/events/stream?after_sequence=${state.eventSequence}`,{headers:{...headers,Accept:'text/event-stream'},signal:controller.signal});if(!response.ok){const data=await response.json();const error=new Error(data.message||'SSE 连接失败');error.code=data.code||String(response.status);throw error}if(!response.body)throw new Error('浏览器不支持流式事件读取');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(state.eventTaskId===taskId){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true}).replace(/\r\n/g,'\n');let boundary,changed=false;while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);let kind='message',data='';for(const line of frame.split('\n')){if(line.startsWith('event:'))kind=line.slice(6).trim();else if(line.startsWith('data:'))data+=(data?'\n':'')+line.slice(5).trim()}if(kind==='stream-error'){const value=JSON.parse(data);const error=new Error(value.message||'SSE 流中断');error.code=value.code;throw error}if(kind==='task-event'&&data){const event=JSON.parse(data);if(Number.isInteger(event.sequence)&&event.sequence>state.eventSequence){state.eventSequence=event.sequence;changed=true;scheduleTaskEventRefresh(taskId,event)}}}}}catch(error){if(error.name==='AbortError'||state.eventTaskId!==taskId)return;if(error.code==='EVENT_HISTORY_EXPIRED'){state.eventSequence=0;$('retrieval-live-status').textContent='SSE 历史已过期，正在从权威快照恢复'}else if(['NOT_FOUND','REVISION_CONFLICT'].includes(error.code)){stopTaskEvents();return}else{$('retrieval-live-status').textContent='SSE 暂时断开，正在通过事件水位恢复'}await pollTaskEvents()}finally{if(state.eventController===controller)state.eventController=null;if(state.eventTaskId===taskId)state.eventTimer=setTimeout(connectTaskEventStream,1000)}}
function startTaskEvents(taskId,asOf=0){stopTaskEvents();state.eventTaskId=taskId;state.eventSequence=Number.isInteger(asOf)?asOf:0;connectTaskEventStream()}
function message(kind,title,text){const a=document.createElement('article');a.className='message '+kind;if(title){const h=document.createElement('h2');h.textContent=title;a.append(h)}const p=document.createElement('p');p.textContent=text;a.append(p);timeline.append(a);timeline.scrollTop=timeline.scrollHeight;return a}
function card(title,rows,actions=[],container=timeline){const a=document.createElement('article');a.className='message card';const h=document.createElement('h2');h.textContent=title;a.append(h);const dl=document.createElement('dl');for(const [name,value] of rows){const dt=document.createElement('dt');dt.textContent=name;const dd=document.createElement('dd');dd.textContent=String(value);dl.append(dt,dd)}a.append(dl);if(actions.length){const box=document.createElement('div');box.className='actions';for(const [label,fn,secondary] of actions){const b=document.createElement('button');b.textContent=label;if(secondary)b.className='secondary';b.addEventListener('click',fn);box.append(b)}a.append(box)}container.append(a);if(container===timeline)timeline.scrollTop=timeline.scrollHeight;return a}
function setHistoryOpen(open){$('history-panel').classList.toggle('is-open',open);$('history-panel').inert=window.matchMedia('(max-width:900px)').matches&&!open;$('history-backdrop').classList.toggle('is-open',open);$('toggle-history').setAttribute('aria-expanded',String(open))}
$('open-model-panel').addEventListener('click',()=>{setHistoryOpen(false);$('model-panel-dialog').showModal()});$('close-model-panel').addEventListener('click',()=>$('model-panel-dialog').close());
$('open-environment-info').addEventListener('click',()=>{setHistoryOpen(false);$('environment-info-dialog').showModal()});$('close-environment-info').addEventListener('click',()=>$('environment-info-dialog').close());
$('toggle-history').addEventListener('click',()=>setHistoryOpen(true));$('close-history').addEventListener('click',()=>setHistoryOpen(false));$('history-backdrop').addEventListener('click',()=>setHistoryOpen(false));
document.addEventListener('keydown',event=>{if(event.key==='Escape')setHistoryOpen(false)});window.addEventListener('resize',()=>setHistoryOpen(false));
function optionalPositive(id){const raw=$(id).value.trim();if(!raw)return null;const value=Number(raw);if(!Number.isInteger(value)||value<1)throw new Error('自动补数限制必须是正整数');return value}
function refillFormValue(){const enabled=$('auto-refill').checked;if(!enabled)return{enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}};const libraries=Array.from($('refill-library-ids').selectedOptions,option=>option.value);if(!libraries.length)throw new Error('启用自动补数前，请至少选择一个已配置素材库');const noProgress=optionalPositive('refill-no-progress-rounds');if(noProgress===null)throw new Error('请填写连续无进展停止轮数');return{enabled:true,libraries,limits:{max_rounds:optionalPositive('refill-max-rounds'),max_vlm_calls:optionalPositive('refill-max-vlm-calls'),no_progress_rounds:noProgress}}}
function refillSummary(value=refillFormValue()){if(!value.enabled)return'数量不足时接受减少，不自动检索补数。';const limit=(label,value)=>label+(value===null?'不限':value);return`自动补足目标数量\n批准素材库：${value.libraries.join('、')}\n${limit('最多轮次：',value.limits.max_rounds)}；${limit('VLM 调用：',value.limits.max_vlm_calls)}；连续 ${value.limits.no_progress_rounds} 轮无进展停止。`}
function draftBody(text,count,groupedExpansion,refill={enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}},queryImage=null,excludeExactContent=false){const automatic=refill.enabled===true;const query={schema_version:'query-spec@2',raw_request:text,media_type:'image',target_count:count,business_domain:'local_preview',intra_batch_dedup:{policy_ref:'exact-content',config_version:'1'},cross_batch_dedup:((typeof state!=='undefined'&&state.historyPolicy)||{scope:'none',references:[],comparison_methods:[],reference_digest:null}),refill_policy:{mode:automatic?'maintain_target':'input_only',target_count:count,approved_library_ids:automatic?refill.libraries:[],progression:automatic?'auto':'manual'},refill_limits:refill.limits};if(queryImage)query.query_input={type:'reference_image',query_image_id:queryImage.query_image_id,digest:queryImage.digest,exclude_exact_content:excludeExactContent===true};if(groupedExpansion)query.grouped_expansion=groupedExpansion;if(groupedExpansion?.model_binding)query.rules=[{rule_id:'semantic-match',predicate_ref:'image.semantic-match@1',operator:'eq',value:true,hardness:'hard',origin:'user_confirmed'}];return{dimension_id:'default',query_spec:query,node_modes:{expansion:'manual',retrieval:automatic?'auto':'manual',cleaning:automatic?'auto':'manual',refill:automatic?'auto':'manual',packaging:'manual'}}}

function groupPreviewBody(text,count,selection){const body=groupInputBody(text),llm=selection?.llm??$('llm-model')?.value,vlm=selection?.vlm??$('vlm-model')?.value;if(llm&&vlm){body.model_selection={llm,vlm};if(body.raw_request)body.target_count=count}return body}
function groupInputBody(text,lines=$('group-lines').value.trim()){if(!lines)return{raw_request:text,dimension_id:'default'};const groups=lines.split(/\n/).filter(line=>line.trim()).map(line=>{const parts=line.split('|');if(parts.length>2)throw new Error('每行仅使用一个 | 分隔主标签和召回词');return{primary_label:parts[0].trim(),auxiliary_terms:(parts[1]||'').split(/[，,、]/).map(t=>t.trim()).filter(Boolean)}});return{dimension_id:'default',parent_label:$('group-parent').value.trim()||null,groups}}
function editProposal(){if(!state.draft||state.task)return;state.draft=null;timeline.replaceChildren();$('new-request').classList.remove('hidden');$('request-text').focus()}
function retireDraftActions(){state.draft=null;timeline.querySelectorAll('.actions').forEach(box=>box.remove())}
function showSavedGroups(plan){if(!plan)return;for(const group of plan.groups){card('已确认分组 · '+group.primary_label,[['上级分类',plan.parent_label||'无'],['召回词',group.auxiliary_terms.join('、')||'仅使用主标签'],['交付设置',group.package_name+' · 独立包（待执行）'],['词来源',plan.generator==='llm@1'?'LLM 建议':plan.generator==='user-edited@1'?'用户编辑':'模板建议'],...(group.delivery_target==null?[]:[['目标数量',group.delivery_target+' 张']])])}}

function setBusy(v){document.querySelectorAll('button,#llm-model,#vlm-model,#llm-model-input,#vlm-model-input,#dialogue-llm-model,#dialogue-llm-model-input,#target-count,#auto-refill,#refill-library-ids,#refill-max-rounds,#refill-max-vlm-calls,#refill-no-progress-rounds').forEach(b=>{if(b.id!=='stop-request')b.disabled=v});if(!v&&!state.refillLibrariesAvailable&&!$('auto-refill').checked)$('auto-refill').disabled=true;const activity=typeof timeline==='undefined'?null:timeline;if(activity)activity.setAttribute('aria-busy',String(v));if(v)$('status').textContent=''}
function setDialogueBusy(v){$('prepare-task').disabled=v;if(v)$('status').textContent=''}
function setFollowupBusy(v){state.followupBusy=v;$('prepare-feedback').disabled=v;if(v)$('status').textContent=''}
function cancelTaskFollowup(){state.followupRequestToken=null;setFollowupBusy(false)}
function cancelDialogueRequest(){cancelTaskFollowup();const controller=state.requestController;if(controller){state.requestController=null;controller.abort();$('stop-request')?.classList?.add('hidden');setDialogueBusy(false)}}
function aggregateLocalCandidates(items){
  const groups=new Map();
  for(const item of items){
    // Only a resolved media unit may be folded across bindings. A missing or
    // conflicting mapping stays scoped to its physical index and vector ID.
    const mapped=item.status==='resolved'&&item.media_unit_id&&item.asset_version;
    const key=JSON.stringify(mapped?(item.candidate_id?['candidate',item.candidate_id,item.asset_version]:['media',item.media_unit_id,item.asset_version]):['vector',item.library_id,item.binding_id,item.index_version,typeof item.vector_id,item.vector_id,item.status]);
    let group=groups.get(key);
    if(!group){group={item,previewItem:null,sources:[],indexes:[],vectors:[],scores:[],metrics:new Set(),hitSequences:[],rawHits:0,_sourceKeys:new Set(),_indexKeys:new Set(),_vectorKeys:new Set(),_scoreKeys:new Set(),_hitKeys:new Set()};groups.set(key,group)}
    if(item.preview_status==='available'&&!group.previewItem)group.previewItem=item;
    group.rawHits++;
    const sourceKey=JSON.stringify([item.branch_id,item.query_text,item.query_role,item.source_term]);
    if(!group._sourceKeys.has(sourceKey)){group._sourceKeys.add(sourceKey);group.sources.push({query_text:item.query_text,query_role:item.query_role,source_term:item.source_term,branch_id:item.branch_id})}
    const indexKey=JSON.stringify([item.library_id,item.binding_id,item.index_version]);
    if(!group._indexKeys.has(indexKey)){group._indexKeys.add(indexKey);group.indexes.push(item.library_id+'/'+item.binding_id+'（'+item.index_version+'）')}
    const vectorKey=JSON.stringify([item.binding_id,item.index_version,typeof item.vector_id,item.vector_id]);
    if(!group._vectorKeys.has(vectorKey)){group._vectorKeys.add(vectorKey);group.vectors.push(item.binding_id+':'+item.vector_id)}
    if(typeof item.score==='number'){const scoreKey=JSON.stringify([item.score,item.metric]);if(!group._scoreKeys.has(scoreKey)){group._scoreKeys.add(scoreKey);group.scores.push({score:item.score,metric:item.metric});if(item.metric)group.metrics.add(item.metric)}}
    if(item.hit_sequence!=null&&!group._hitKeys.has(item.hit_sequence)){group._hitKeys.add(item.hit_sequence);group.hitSequences.push(item.hit_sequence)}
  }
  return [...groups.values()];
}
function appendLocalCandidateGroups(...args){return measureChatRender('candidate_render',()=>appendLocalCandidateGroupsImpl(...args),args[1])}
function appendLocalCandidateGroupsImpl(bubble,items){
  const groups=aggregateLocalCandidates(items);
  for(const group of groups){
    const item=group.item,row=document.createElement('div');row.className='search-hit';
    const title=document.createElement('strong');title.textContent=item.status==='resolved'?'素材：'+item.media_unit_id+'（'+item.asset_version+'）':'未映射向量：'+item.binding_id+' / '+item.vector_id;
    const tags=document.createElement('div');tags.className='search-tags';tags.setAttribute('aria-label','命中检索词');for(const source of group.sources){const tag=document.createElement('span');tag.className='search-tag';tag.textContent=source.query_text;tags.append(tag)}
    const detail=document.createElement('small');detail.textContent='词来源：'+group.sources.map(source=>source.query_role+' / '+source.source_term).join('；')+' · 索引：'+group.indexes.join('、')+' · 向量：'+group.vectors.join('、')+' · 原始命中 '+group.rawHits+' 条 · 映射：'+item.status;
    row.append(title,tags);
    if(group.previewItem){const img=document.createElement('img');img.alt='未清洗原始候选：'+item.media_unit_id;img.loading='lazy';row.append(img);observeCandidateImage(img,group.previewItem)}
    else{const note=document.createElement('small');note.className='preview-note';note.textContent='无可预览图片：该命中没有可读取的授权媒体文件。';row.append(note)}
    row.append(detail);bubble.append(row);
  }
  return groups.length;
}
function observeCandidateImage(img,item){
  const taskId=state.task?.task_id;if(!taskId)return;
  const load=async()=>{if(!img.isConnected)return;try{const response=await fetch(`/v1/retrieval/tasks/${encodeURIComponent(taskId)}/first-search/candidates/${encodeURIComponent(item.candidate_id)}/preview`,{headers});if(!response.ok)throw new Error('图片不可读取');const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('返回内容不是图片');if(!img.isConnected)return;const url=URL.createObjectURL(blob);img.addEventListener('load',()=>URL.revokeObjectURL(url),{once:true});img.addEventListener('error',()=>URL.revokeObjectURL(url),{once:true});img.src=url}catch(error){if(img.isConnected){const note=document.createElement('small');note.className='preview-note';note.textContent='图片预览失败：'+error.message;img.replaceWith(note)}}};
  if(typeof IntersectionObserver==='undefined'){load();return}
  const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();load()}},{rootMargin:'250px'});observer.observe(img);
}
async function showFirstSearchCandidates(){await openCandidateBrowser()}
async function viewFirstSearchCandidates(){setBusy(true);try{await showFirstSearchCandidates()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function runFirstSearch(){if(!state.task)return;setBusy(true);try{const taskId=state.task.task_id;const result=await api(`/v1/retrieval/tasks/${taskId}/first-search/tick`,{method:'POST',body:{max_requests:2}});card('本地检索批次',[['网络请求',result.network_attempts],['成员进度',result.members.map(m=>`${m.plan_id.slice(0,12)}: ${m.committed_pages} 页 / ${m.stop_reason||'可继续'}`).join('\n')],['说明',result.note]],result.pagination_stopped?[]:[['继续下一批',runFirstSearch,false]]);await showFirstSearchCandidates()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function resetTimeline(title='新建数据需求',text='说说你想找什么图片、需要多少张。'){clearExpansionPolling();clearTaskExpansionPolling();state.guidedResultBubble=null;state.feedback=null;state.feedbackReviewBubble=null;state.feedbackManualCard=null;state.expansionPreviewBubble=null;state.expansionPreviewToken=null;state.expansionPreviewPlan=null;state.confirmedRevision=null;state.searchAttempt=null;state.proposedExpansion=null;state.targetCountTouched=false;timeline.replaceChildren();renderPlanningWorkflow('request');resetResultWorkspace();$('retrieval-live-status').textContent='';setHistoryOpen(false);updateChatHeader('对话','先沟通，再检索');message('system',title,text)}
function updateChatHeader(title,status){$('conversation-heading').textContent=title;$('conversation-status').textContent=status}
function savedChatNames(){try{return JSON.parse(localStorage.getItem('dsh-chat-names')||'{}')}catch{return{}}}
function renameChat(id,current){const next=prompt('重命名 Chat',current);if(next===null)return;const value=next.trim();if(!value)return;const names=savedChatNames();names[id]=value.slice(0,80);localStorage.setItem('dsh-chat-names',JSON.stringify(names));refreshHistory()}
function chatStarted(value){if(!value)return'时间未知';return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))}
function currentDialogueSessionId(){if(state.dialogueId)return state.dialogueId;try{return JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')?.id||null}catch{return null}}
function visibleDialogueChats(items){return items.filter(item=>item.message_count>0)}
async function deleteHistoryChat(id,path,active,force){const prompt=force?'这条 Chat 已确认并进入检索，硬删除将一并移除关联正式任务、检索与清洗记录，且无法撤销。确定继续？':'删除这条尚未进入检索的 Chat？此操作无法撤销。';if(!confirm(prompt))return;try{await api(path,{method:'DELETE'});const names=savedChatNames();delete names[id];localStorage.setItem('dsh-chat-names',JSON.stringify(names));if(active)startNewChat();else await refreshHistory()}catch(error){$('status').textContent=error.detail||error.message}}
function historyEntry({id,title,meta,active,onOpen,onDelete}){const row=document.createElement('div');row.className='history-entry';const open=document.createElement('button');open.className='history-item'+(active?' active':'');const label=document.createElement('span');label.textContent=title;const detail=document.createElement('small');detail.textContent=meta;open.append(label,detail);open.addEventListener('click',onOpen);const rename=document.createElement('button');rename.className='rename-chat';rename.type='button';rename.textContent='✎';rename.title='重命名 '+title;rename.setAttribute('aria-label','重命名 '+title);rename.addEventListener('click',()=>renameChat(id,title));row.append(open,rename);if(onDelete){const remove=document.createElement('button');remove.className='delete-chat';remove.type='button';remove.textContent='×';remove.title='删除 '+title;remove.setAttribute('aria-label','删除 '+title);remove.addEventListener('click',onDelete);row.append(remove)}else{const spacer=document.createElement('span');spacer.setAttribute('aria-hidden','true');row.append(spacer)}return row}
let historyEpoch=0,historyCursor=null,historyPrevious=[],historyPage=0;
async function refreshHistory(cursor=null,page=0){
 const epoch=++historyEpoch,box=$('history-list'),pager=$('history-pager');
 try{
  const params=new URLSearchParams({limit:'20'});if(cursor)params.set('cursor',cursor);
  const data=await api('/v1/retrieval/projects/demo/chat-history?'+params);if(epoch!==historyEpoch)return;
  historyCursor=data.next_cursor;historyPage=page;if(page===0)historyPrevious=[];historyPrevious[page]=cursor;
  const names=savedChatNames();box.replaceChildren();box.scrollTop=0;if(!data.items.length){const p=document.createElement('p');p.className='empty';p.textContent='还没有历史 Chat';box.append(p)}
  for(const item of data.items){
   const id=item.id,task=item.task,title=names[id]||item.title,meta=`发起 ${chatStarted(item.created_at)} · `+(task?`revision ${task.confirmed_revision} · ${task.run_status}`:`${item.message_count} 条消息`);
   const active=item.kind==='dialogue'?currentDialogueSessionId()===id&&!state.task:item.kind==='conversation'?state.conversationId===id:state.task?.task_id===id;
   if(active)updateChatHeader(title,meta);
   const onOpen=item.kind==='dialogue'?()=>openDialogueSession(id,item.title):item.kind==='conversation'?()=>openConversation(id,item.title):()=>openTask(id);
   const force=item.kind==='task'||item.confirmation_count>0;
   const path=item.kind==='dialogue'?`/v1/retrieval/projects/demo/dialogue-sessions/${id}`:item.kind==='conversation'?`/v1/retrieval/conversations/${id}`:`/v1/retrieval/tasks/${id}`;
   box.append(historyEntry({id,title,meta,active,onOpen,onDelete:()=>deleteHistoryChat(id,path+(force?'?force=true':''),active,force)}));
  }
  pager.hidden=page===0&&!historyCursor;const prev=$('history-prev'),next=$('history-next');$('history-page-label').textContent=`第 ${page+1} 页`;prev.disabled=page===0;next.disabled=!historyCursor;
  prev.onclick=()=>refreshHistory(historyPrevious[page-1],page-1);next.onclick=()=>refreshHistory(historyCursor,page+1);
 }catch(e){if(epoch!==historyEpoch)return;pager.hidden=true;box.replaceChildren();const p=document.createElement('p');p.className='empty';p.textContent='历史记录加载失败：'+e.message;box.append(p);const retry=document.createElement('button');retry.textContent='重试历史列表';retry.onclick=()=>refreshHistory(cursor,page);box.append(retry)}
}
async function openDialogueSession(sessionId,previewText=null){sessionStorage.removeItem('dsh-fresh-chat');
  if(typeof cancelDialogueRequest==='function')cancelDialogueRequest();
  let previous=null;try{previous=JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')}catch{}
  sessionStorage.setItem('dsh-dialogue',JSON.stringify({id:sessionId,proposal:previous?.id===sessionId?previous.proposal:null}));
  localStorage.removeItem('dsh-task-id');state.task=null;state.conversationId=null;state.dialogueId=null;state.draft=null;state.resumeDraft=null;
  $('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden');
  await restoreDialogueSession(previewText);
}
function fillDraftForm(draft){const query=draft.proposal.query_spec;state.historyPolicy=query.cross_batch_dedup;showHistoryPolicy();$('request-text').value=query.raw_request;$('target-count').value=query.target_count;const grouped=query.grouped_expansion;if(grouped?.model_binding)restoreModelChoice(grouped.model_binding);$('group-parent').value=grouped?.parent_label||'';$('group-lines').value=grouped?.groups?.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n')||'';const refill=query.refill_policy||{};$('auto-refill').checked=refill.mode==='maintain_target';for(const library of refill.approved_library_ids||[]){let option=Array.from($('refill-library-ids').options).find(item=>item.value===library);if(!option){option=document.createElement('option');option.value=library;option.textContent=library+'（原草稿范围，当前配置中不可见）';$('refill-library-ids').append(option)}option.selected=true}const limits=query.refill_limits||{};$('refill-max-rounds').value=limits.max_rounds??'';$('refill-max-vlm-calls').value=limits.max_vlm_calls??'';$('refill-no-progress-rounds').value=limits.no_progress_rounds??2;syncRefillFields()}
function showHistoryLoading(label){cancelTaskFollowup();stopTaskEvents();clearExpansionPolling();clearTaskExpansionPolling();$('conversation-heading').textContent=label;$('conversation-status').textContent='读取中';timeline.replaceChildren();message('system','正在读取','先显示对话内容，随后补齐生产节点与结果。');$('task-sidebar').classList.add('hidden');resetResultWorkspace()}
function renderSavedTaskMessages(items){for(const item of items||[]){if(item.role==='user'||item.role==='assistant')message(item.role,'',item.text)}}
function restoreFollowupContext(task,items){if(state.followupContext?.taskId===task.task_id&&state.followupContext.version===task.task_version)return;state.followupContext={taskId:task.task_id,version:task.task_version,messages:(items||[]).filter(item=>(item.role==='user'||item.role==='assistant')&&typeof item.text==='string'&&!item.text.startsWith('本轮理解：')).slice(-20).map(item=>({role:item.role,text:item.text}))}}
async function openConversation(conversationId,previewText=null){sessionStorage.removeItem('dsh-fresh-chat');const token=state.historyOpenToken=(state.historyOpenToken||0)+1,started=performance.now();setBusy(true);showHistoryLoading('正在打开历史 Chat…');$('status').textContent='正在打开历史 Chat…';if(previewText){resetTimeline('历史需求摘要',previewText);timeline.dataset.firstTextMs=String(performance.now()-started);timeline.dataset.firstTextSource='history-summary'}try{const data=await api(`/v1/retrieval/conversations/${conversationId}`);if(token!==state.historyOpenToken)return;sessionStorage.removeItem('dsh-dialogue');state.dialogueId=null;state.conversationId=conversationId;state.task=null;state.resumeDraft=null;localStorage.removeItem('dsh-task-id');resetTimeline('历史 Chat','可以在下方继续这段对话。草稿消息不会进入业务执行时间线。');const clarification=data.clarification_messages||[];if(clarification.length)renderSavedTaskMessages(clarification);for(const item of clarification.length?data.messages.slice(1):data.messages){message('user','',item.text)}renderSavedTaskMessages(data.task_messages);timeline.dataset.fullTextMs=String(performance.now()-started);if(!previewText){timeline.dataset.firstTextMs=timeline.dataset.fullTextMs;timeline.dataset.firstTextSource='conversation'}if(data.confirmations.length){const latest=data.confirmations[data.confirmations.length-1];const binding=await api(`/v1/retrieval/tasks/${latest.task_id}/read-binding`);if(token!==state.historyOpenToken)return;if(binding.proposal.query_spec.media_type==='video'){await openVideoWorkspace(latest.task_id);return}const task=await api(`/v1/retrieval/tasks/${latest.task_id}/workspace`);if(token!==state.historyOpenToken)return;if(task.agent_retrieval){await openAgentWindowWorkspace(task);return}const [progress,results]=await Promise.all([task.progress||api(`/v1/retrieval/tasks/${latest.task_id}/progress`),api(`/v1/retrieval/tasks/${latest.task_id}/results?view=accepted&limit=20`)]);if(token!==state.historyOpenToken)return;state.task=task;restoreFollowupContext(task,data.task_messages);localStorage.setItem('dsh-task-id',task.task_id);showProgress(progress,results);showResults(results,task.task_id);startTaskEvents(task.task_id,progress.as_of_sequence);$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');refreshTaskSidebar();offerExistingSearch(task.task_id)}else{const last=data.messages[data.messages.length-1],draft=await api(`/v1/retrieval/projects/demo/drafts/${last.draft_id}`);if(token!==state.historyOpenToken)return;if(draft.proposal.query_spec.media_type==='video'){showVideoDraft(draft);$('open-video-draft').click();return}state.draft=draft;state.resumeDraft={draft_id:draft.draft_id,draft_version:draft.draft_version};fillDraftForm(draft);$('request-heading').textContent='继续这个 Chat';$('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden')}$('status').textContent='';refreshHistory()}catch(e){if(token===state.historyOpenToken)$('status').textContent=e.message}finally{if(token===state.historyOpenToken)setBusy(false)}}
function confirmedQuery(progress){const current=state.confirmedRevision;return current?.taskId===progress.task_id&&current.revision===progress.revision?current.proposal?.query_spec:null}
function nodeHeader(number,label,status,tag='summary'){const header=document.createElement(tag);const index=document.createElement('span');index.className='node-index';index.textContent=String(number).padStart(2,'0');const title=document.createElement('span');title.className='node-title';title.textContent=label;const badge=document.createElement('span');badge.className='node-status';badge.textContent=status;header.append(index,title,badge);return header}
function renderPlanningWorkflow(stage='request'){
 const labels=['扩检索词','检索','清洗','补数','打包'],active=stage==='search'?1:0,nodes=$('task-sidebar-nodes');$('task-sidebar').classList.remove('hidden');$('task-sidebar-identity').classList.add('hidden');$('task-sidebar-summary').textContent=stage==='request'?'诉求澄清中':'当前：'+labels[active];nodes.replaceChildren();
 labels.forEach((label,index)=>{const item=document.createElement('div');item.className='node-card';item.append(nodeHeader(index+1,label,stage==='request'?'未开始':index<active?'已完成':index===active?'进行中':'未开始','div'));nodes.append(item)});
}
function demandCard(progress){const query=confirmedQuery(progress);const card=document.createElement('details');card.className='node-card';card.append(nodeHeader(1,'诉求澄清',query?'已完成':'进行中'));const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');const labels=query?.grouped_expansion?.groups?.map(group=>group.primary_label).filter(Boolean)||[];summary.textContent=query?`目标 ${query.target_count} 张 · 内容 ${labels.join('、')||'见诉求原文'}`:'正在读取已确认诉求…';body.append(summary);if(query?.raw_request){const original=document.createElement('p');original.textContent='诉求原文：'+query.raw_request;body.append(original)}card.append(body);return card}
function refillStopLabel(reason){return ({TARGET_MET:'已达到目标',INPUT_SCOPE_COMPLETE:'固定输入范围已处理完，可部分交付',EXECUTION_LIMIT_REACHED:'已达到补数轮次上限',VLM_CALL_LIMIT_REACHED:'已达到 VLM 调用预算',NO_PROGRESS:'连续补数没有新增有效结果',SEARCH_STRATEGIES_EXHAUSTED:'当前批准的检索策略已耗尽',NEED_USER_INPUT:'需要发起人确认',REFILL_SCHEDULED:'已进入自动补数调度'})[reason]||reason||'无'}
function refillRoundOutcome(outcome){return ({pending:'进行中',result:'已出结果',empty:'无新增候选'})[outcome]||outcome||'未知'}
function refillRoundRows(execution){return (execution?.rounds||[]).map(round=>[`第 ${round.iteration} 轮 · ${refillRoundOutcome(round.outcome)}`,`返回候选 ${round.candidate_count??'未知'} · 新增有效 ${round.new_unique_accepted??'待结果'} · 累计有效 ${round.accepted_after??round.baseline_accepted??'未知'} · VLM 调用 ${round.vlm_calls??0}`])}
function refillExecutionSummary(plan){const execution=plan.execution||{};return `目标 ${plan.target_count} · 累计有效 ${plan.accepted_count} · 缺口 ${plan.gap} · ${plan.stop_reason?'停止原因 '+refillStopLabel(plan.stop_reason):plan.action==='wait'?'补数进行中':'尚未停止'} · 已发 ${execution.issued_rounds??0} 轮 · 已完成 ${execution.completed_rounds??0} 轮 · VLM 调用 ${execution.vlm_calls??0}${execution.remaining_vlm_calls!=null?` · 剩余调用预算 ${execution.remaining_vlm_calls}`:""}${execution.remaining_rounds!=null?` · 剩余轮数 ${execution.remaining_rounds}`:""}${execution.effective_yield!=null?` · 补数有效产出 ${(execution.effective_yield*100).toFixed(1)}%`:""}`}
async function loadRefillRounds(taskId,container,button,cursor=null,history=[]){const token=state.refillRoundsToken=(state.refillRoundsToken||0)+1;button.disabled=true;container.textContent='正在读取逐轮补数证据…';try{const params=new URLSearchParams({limit:'24'});if(cursor)params.set('cursor',cursor);const plan=await api(`/v1/retrieval/tasks/${taskId}/nodes/refill/items?${params}`);plan.execution={...plan.execution,rounds:plan.items};if(token!==state.refillRoundsToken||state.task?.task_id!==taskId)return;container.replaceChildren();const summary=document.createElement('p');summary.textContent=refillExecutionSummary(plan);container.append(summary);const rows=refillRoundRows(plan.execution);if(!rows.length){const empty=document.createElement('p');empty.textContent='尚未产生补数轮次。';container.append(empty)}for(const [title,detail] of rows){const row=document.createElement('p');row.textContent=`${title}：${detail}`;container.append(row)}const note=document.createElement('p');note.textContent='有效数量按 SHA-256 累计去重后的清洗通过结果计算，不把检索返回数计为有效。';container.append(note);const previous=document.createElement('button'),next=document.createElement('button');previous.textContent='上一页';previous.disabled=!history.length;previous.onclick=()=>loadRefillRounds(taskId,container,button,history[history.length-1],history.slice(0,-1));next.textContent='下一页';next.disabled=!plan.next_cursor;next.onclick=()=>loadRefillRounds(taskId,container,button,plan.next_cursor,[...history,cursor]);container.append(previous,next)}catch(error){if(token!==state.refillRoundsToken)return;container.textContent=error.code==='REFILL_NOT_CONFIGURED'?'当前方案未启用自动补数。':'补数证据读取失败：'+error.message}finally{button.disabled=false}}
function completedNodeOutput(node,progress,results){if(node.node==='cleaning'&&results?.available)return `有效 ${results.counts.accepted} · 淘汰 ${results.counts.failed} · 规则待复核 ${results.counts.pending_review} · SHA-256 精确重复已自动合并 ${results.counts.duplicate}`;if(node.node==='refill'&&results?.available)return `累计有效 ${results.counts.accepted} · 缺口 ${results.counts.gap} · 停止原因 ${refillStopLabel(results.stop_reason)}`;if(node.node==='packaging'&&results?.available)return `结果版本 ${results.result_set_digest} · 可交付 ${results.counts.accepted}`;return node.detail||'节点已完成，尚未发布独立输出。'}
function shardCoverageSummary(coverage){
  if(!coverage)return '';
  if(!coverage.known)return '切片覆盖未知：缺少完整执行快照，不能确认全库覆盖。';
  const rows=coverage.queries||[];
  if(!rows.length)return '切片覆盖未知：暂无查询范围。';
  return rows.map((row,index)=>`查询 ${index+1} · ${row.library_id}：总切片 ${row.total} · 已查询 ${row.visited} · 尚未查询 ${row.pending} · 失败 ${row.failed} · 已搜尽 ${row.exhausted} · ${row.complete?'该查询范围已搜尽':'该查询范围尚未搜尽'}`).join('\n')+'\n已查询表示至少成功返回一页；候选窗口结束、目标数量满足均不代表全库搜尽。';
}
function retrievalSourceSummary(candidates){const count=candidates?.sources?.length||0;return count?`数据来源：智能选择 · ${count} 个预制数据源`:'数据来源：智能选择'}
function nodeCard(node,index,progress,results,searchProgress=null,candidates=null,manifest=null){
  const authority=progress.workflow?.nodes.find(item=>item.node===node.node);
  const available=progress.workflow?.available_actions||[];
  const currentSearch=index===1&&searchProgress?.status==='approved'&&searchProgress.revision===progress.revision&&searchProgress.generation===progress.run_generation&&searchProgress.task_version===progress.task_version;
  const fixedSearch=index===1&&manifest?.confirmed&&manifest.candidate_count>=0;
  const currentManifest=index===1&&manifest&&!manifest.confirmed&&manifest.expected_task_version===progress.task_version;
  const pendingManifest=index===1&&(authority?available.some(action=>action.id==='confirm_candidates'):currentManifest&&manifest.candidate_count>0);
  const emptyManifest=currentManifest&&manifest.candidate_count===0;
  const attempt=index===1&&state.searchAttempt?.taskId===progress.task_id&&state.searchAttempt.revision===progress.revision?state.searchAttempt:null;
  const statusText=authority?.display_status||(node.node==='packaging'&&node.gate_status==='awaiting_approval'?'待发布':pendingManifest?'待确认':currentSearch?searchStatusLabel(searchProgress):fixedSearch?'已完成':attempt?.status==='unavailable'?'不可用':attempt?.status==='checking'?'进行中':index===0&&progress.revision>0&&node.node_status==='running'?'已完成':({pending:'未开始',running:'进行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'}[node.node_status]||'未开始'));
  const completed=statusText==='已完成',expandable=index<=1||completed||node.node==='cleaning'||(node.node==='refill'&&statusText!=='未开始'),card=document.createElement(expandable?'details':'div');card.className='node-card';
  if(expandable){const key=`${progress.task_id}:${node.node}`;card.dataset.node=node.node;const saved=state.nodeOpen?.[key];card.open=saved===undefined?Boolean(currentSearch&&state.searchNodeOpen!==false):saved;card.addEventListener('toggle',()=>{(state.nodeOpen??={})[key]=card.open;if(currentSearch)state.searchNodeOpen=card.open})}
  if(authority){if(authority.node_status==='failed')card.classList.add('search-unavailable');else if(index===1&&authority.node_status==='running')card.classList.add('search-active')}
  else if(currentSearch||attempt?.status==='checking')card.classList.add('search-active');else if(attempt?.status==='unavailable')card.classList.add('search-unavailable');
  const displayLabel=node.node==='expansion'?'扩检索词':node.label,header=nodeHeader(index+1,displayLabel,statusText,expandable?'summary':'div');if(!expandable)header.className='node-heading';card.append(header);
  if(node.node==='cleaning'){const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');summary.textContent=results?.available?`合格预览：入选 ${results.counts.accepted} · 候补 ${results.counts.reserve} · 规则待复核 ${results.counts.pending_review} · SHA-256 精确重复已自动合并 ${results.counts.duplicate}`:node.detail;const button=document.createElement('button');button.className='secondary';button.textContent='查看各步骤结果';const output=document.createElement('div');button.addEventListener('click',()=>loadCleaningSteps(progress.task_id,output,button));body.append(summary,button,output);card.append(body);return card}
  if(node.node==='packaging'){const body=document.createElement('div');body.className='node-body';const note=document.createElement('p');note.textContent=node.detail;const button=document.createElement('button');button.className='secondary';button.textContent='打包 / 发布 / 下载';button.onclick=()=>openDelivery(progress.task_id);body.append(note,button);card.append(body);return card}
  if(node.node==='refill'&&expandable){const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');summary.textContent=completed?completedNodeOutput(node,progress,results):(node.detail||'补数进行中。');const button=document.createElement('button');button.className='secondary';button.textContent='查看逐轮补数证据';const output=document.createElement('div');button.addEventListener('click',()=>loadRefillRounds(progress.task_id,output,button));body.append(summary,button,output);card.append(body);return card}
  if(index>1){if(completed){const body=document.createElement('div');body.className='node-body';const output=document.createElement('p');output.textContent=completedNodeOutput(node,progress,results);body.append(output);card.append(body)}return card}
  if(index===0){
    const body=document.createElement('div');body.className='node-body node-terms';
    const fill=groups=>{body.replaceChildren();for(const group of groups){const row=document.createElement('div'),label=document.createElement('strong'),terms=document.createElement('p');label.textContent=group.primary_label;terms.textContent=group.auxiliary_terms?.join('、')||'仅主标签';row.append(label,terms);body.append(row)}if(!groups.length)body.textContent='暂无已确认检索词。'};
    const groups=confirmedQuery(progress)?.grouped_expansion?.groups||[];let loaded=groups.length>0,loading=false;
    fill(groups);card.append(body);
    const read=async()=>{if(!card.open||loaded||loading)return;loading=true;body.textContent='正在读取检索词…';
      try{const data=await api(`/v1/retrieval/tasks/${progress.task_id}/read-binding`);if(state.task?.task_id!==progress.task_id||data.revision!==state.task.confirmed_revision)return;
        state.confirmedRevision={taskId:progress.task_id,revision:data.revision,proposal:data.proposal};loaded=true;fill(data.proposal.query_spec.grouped_expansion?.groups||[])}
      catch(error){body.textContent='检索词暂不可读：'+error.message}finally{loading=false}};
    card.addEventListener('toggle',read);if(card.open)void read();return card;
  }

  const body=document.createElement('div');body.className='node-body';const note=document.createElement('p');
  const autoCandidates=state.task?.retrieval_agent_job?.candidate_intake==='auto';
  note.textContent=authority?authority.detail:(pendingManifest?`${results?.available?'补检索已返回 · 本轮新增':'检索已完成 · 已校验'} ${manifest.candidate_count} 张候选，等待确认。`:currentSearch?`${retrievalSourceSummary(candidates)} · 原始候选 ${candidates?.raw_hit_count??'读取中'} 条 · ${searchProgress.pagination_stopped?'检索已完成':searchProgress.initial_window_complete?'首轮候选已就绪，后续位置留给自动补数':'检索进行中'}`:fixedSearch?`检索已完成 · 已固定 ${manifest.candidate_count} 张候选图片 · 候选清单 ${manifest.manifest_ref}`:emptyManifest&&node.node_status==='succeeded'?'检索已完成 · 本轮没有新增稳定候选，不会重复进入清洗。':node.node_status==='succeeded'?(results?.available?'检索已完成 · 已进入清洗并发布合格预览。':node.detail||'检索已完成。'):attempt?.status==='unavailable'?attempt.reason:attempt?.status==='checking'?'正在智能选择预制数据源…':'尚未开始检索。');body.append(note);
  if(autoCandidates&&pendingManifest)note.textContent='候选已就绪，后台将自动固定清单并进入现有清洗流程。';
  if(index===1&&searchProgress?.coverage){const coverage=document.createElement('p');coverage.className='helper';coverage.style.whiteSpace='pre-line';coverage.textContent=shardCoverageSummary(searchProgress.coverage);body.append(coverage)}
  if(!autoCandidates&&pendingManifest&&(authority||manifest?.candidate_count)){const approve=document.createElement('button');approve.className='secondary';approve.textContent=results?.available?'确认本轮新增候选':'确认候选清单';approve.addEventListener('click',showCandidateManifestReview);body.append(approve)}
  if(currentSearch){
    const counts=document.createElement('p');counts.textContent=searchSummary(searchProgress,candidates);body.append(counts);
    if(searchProgress.execution_budget){const budget=searchProgress.execution_budget,p=document.createElement('p');p.textContent=`检索调用 ${budget.calls_reserved}/${budget.max_calls} · 原始命中名额 ${budget.hit_slots_reserved}/${budget.max_hit_slots}`;body.append(p)}
    const snapshot=state.searchSnapshot;if(snapshot?.taskId===progress.task_id){const detail=document.createElement('p');detail.textContent=(snapshot.error?'结果读取失败：'+snapshot.error+'；保留上次状态。 ':'')+(snapshot.updated?'最近读取 '+snapshot.updated:'');body.append(detail)}
    const button=document.createElement('button');button.className='secondary';button.textContent='检索候选';button.addEventListener('click',openCandidateBrowser);body.append(button);
    const retry=document.createElement('button');retry.className='secondary';retry.textContent='刷新状态';retry.addEventListener('click',()=>refreshTaskSidebar());body.append(retry);
    for(const member of searchProgress.members||[]){if(member.last_error){const error=document.createElement('p');error.textContent='分支错误：'+(typeof member.last_error==='string'?member.last_error:JSON.stringify(member.last_error));body.append(error)}}
    if(!autoCandidates&&!pendingManifest&&!searchProgress.execution_budget?.stop_reason&&(searchProgress.pagination_stopped||searchProgress.initial_window_complete)&&candidates?.verified_count){const approve=document.createElement('button');approve.className='secondary';approve.textContent=state.candidateManifest?'候选清单已确认':'确认候选清单';approve.disabled=Boolean(state.candidateManifest);approve.addEventListener('click',showCandidateManifestReview);body.append(approve)}
  }
  if(index===1&&state.task?.coordinator_available&&typeof startPlatformCoordination==='function'){
    const arrange=document.createElement('button');arrange.className='secondary';arrange.textContent='安排下一步';arrange.addEventListener('click',()=>startPlatformCoordination());body.append(arrange);
  }
  if(index===1&&state.task?.retrieval_recall_available&&typeof showRecallAnalysis==='function'){
    const analyze=document.createElement('button');analyze.className='secondary';analyze.textContent='分析本次召回';analyze.addEventListener('click',showRecallAnalysis);body.append(analyze);
  }
  card.append(body);return card;
}
function cleaningReasonSummary(reason){
  if(reason.reason_summary)return reason.reason_summary+(reason.reason_truncated?'（摘要已截断）':'');
  return reason.verdict==='pass'?'':'未保存具体原因摘要';
}
function cleaningReasonLabel(code){const labels={MATCH:'符合条件',MISMATCH:'不符合条件',FACT_UNAVAILABLE:'证据不足，无法判定',DETECTOR_ERROR:'检测器执行异常，未作业务判定',DETECTOR_OUTPUT_INVALID:'检测结果格式无效，未作业务判定'};return labels[code]||code}
async function loadCleaningSteps(taskId,container,button){
  const dialog=$('cleaning-results-dialog');container=$('cleaning-results-content');
  const token=state.cleaningViewToken=(state.cleaningViewToken||0)+1;
  const binding=JSON.stringify([state.task?.task_id,state.task?.confirmed_revision,state.task?.run_generation]);
  const current=()=>token===state.cleaningViewToken&&binding===JSON.stringify([state.task?.task_id,state.task?.confirmed_revision,state.task?.run_generation])&&dialog.open;
  $('close-cleaning-results').onclick=()=>dialog.close();
  if(!dialog.open)dialog.showModal();
  button.disabled=true;container.textContent='正在读取步骤结果…';
  try{const [data,results]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}/cleaning/steps?summary=true`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=100`)]);if(!current())return;container.replaceChildren();
    const note=document.createElement('p');note.className='cleaning-counts';note.textContent=results.available?`${results.publication_kind==='delivery'?'已发布交付':'合格预览'} · 目标 ${results.target_count} · 合格去重 ${results.counts.eligible_unique} · 入选 ${results.counts.accepted} · 候补 ${results.counts.reserve} · SHA-256 精确重复已自动合并 ${results.counts.duplicate} · 规则待复核 ${results.counts.pending_review} · 缺口 ${results.counts.gap}${(results.group_progress||[]).map(row=>` · ${row.label} ${row.accepted_count}/${row.target_count}${row.gap?`（缺 ${row.gap}）`:''}`).join('')}。完全一致内容无需人工复核。${results.publication_kind==='delivery'?'可在打包节点下载。':'尚未正式交付。'}`:'尚无当前版本合格预览；本步保留不等于最终入选。';container.append(note);
    if(results.available){const members=document.createElement('details'),heading=document.createElement('summary'),body=document.createElement('div');heading.textContent='查看入选 / 候补清单';const selected=document.createElement('select');selected.setAttribute('aria-label','合格预览分类');for(const [value,label] of [['accepted','入选'],['reserve','候补']]){const option=document.createElement('option');option.value=value;option.textContent=label;selected.append(option)}let readToken=0;const read=async(cursor=null,append=false)=>{const request=++readToken;try{const page=await api(`/v1/retrieval/tasks/${taskId}/results?view=${selected.value}&limit=20`+(cursor?'&cursor='+encodeURIComponent(cursor):''));if(!current()||request!==readToken)return;if(page.result_set_digest!==results.result_set_digest)throw new Error('结果版本已更新，请重新打开');if(!append)body.replaceChildren();body.querySelector('button')?.remove();for(const item of page.items){const row=document.createElement('div');row.className='cleaning-hit';const label=document.createElement('p');label.textContent=(selected.value==='accepted'?'入选':'候补')+' · '+item.candidate_id;row.append(label);if(data.candidate_preview_available){const img=document.createElement('img');img.alt=label.textContent;row.append(img);observeCleaningImage(img,taskId,item.candidate_id)}body.append(row)}if(!page.items.length&&!append)body.textContent='暂无素材';if(page.next_cursor){const more=document.createElement('button');more.textContent='加载更多';more.onclick=()=>read(page.next_cursor,true);body.append(more)}}catch(error){if(current()&&request===readToken)body.textContent='读取失败：'+error.message}};selected.onchange=()=>read();members.append(heading,selected,body);container.append(members);read()}
    if(!data.available){container.textContent='尚未产生可查看的清洗步骤结果。';return}
    if(data.execution){const execution=document.createElement('div');execution.className='cleaning-execution';const model=document.createElement('p');model.textContent=`清洗模型：${data.execution.model.provider} / ${data.execution.model.name}（${data.execution.model.id}）`;const instruction=document.createElement('p');instruction.textContent='实际清洗指令：'+data.execution.instruction;const version=document.createElement('p');version.textContent=`清洗算子：${data.execution.backend_ref} · 提示版本：${data.execution.prompt_version}`;execution.append(model,instruction,version);for(const node of data.execution.nodes||[]){const row=document.createElement('p');row.textContent=`${node.node_id} · ${node.source} · 指令：${node.instruction}`;execution.append(row)}container.append(execution)}
    if(!data.execution){const warning=document.createElement('p');warning.textContent='此记录未保存 VLM 执行快照，不使用当前配置补写历史模型或指令。';container.append(warning)}
    for(const run of data.runs){const note=document.createElement('p');note.textContent=run.note;container.append(note);
      for(const step of run.steps){const detail=document.createElement('details'),summary=document.createElement('summary');summary.textContent=`${step.step_id} · ${{completed:'已完成',running:'运行中',pending:'待执行'}[step.status]||step.status} · 输入 ${step.counts.input} / 保留 ${step.counts.retained} / 淘汰 ${step.counts.rejected} / 待复核 ${step.counts.review} / 处理中 ${step.counts.pending??0} / 阻断 ${step.counts.blocked??0}`;detail.append(summary);
        const info=document.createElement('p');info.textContent=`输入版本 ${step.input_version} · 输出版本 ${run.output_version} · 算子 ${step.backend_ref} · ${run.execution_mode==='shared_input_detection'?'并行共享输入，不按步骤递减':'按冻结依赖执行'}`;detail.append(info);
        for(const rule of step.rules){const text=document.createElement('p');text.textContent=`${rule.predicate} ${rule.operator} ${JSON.stringify(rule.value)}`;detail.append(text)}
        const select=document.createElement('select');select.setAttribute('aria-label','筛选步骤结果');for(const [value,label] of [['input','全部输入'],['retained','本步保留'],['rejected','本步淘汰'],['review','待复核'],['pending','处理中'],['blocked','阻断']]){const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option)}
        const items=document.createElement('div');items.className='cleaning-items';let request=0,loaded=false;const read=async(cursor=null,append=false)=>{const currentRequest=++request;try{const params=new URLSearchParams({operation_id:run.operation_id,step_execution_id:step.step_execution_id,output_version:run.output_version,view_type:select.value,limit:'20'});if(cursor)params.set('cursor',cursor);const page=await api(`/v1/retrieval/tasks/${taskId}/nodes/cleaning/outputs?${params}`);if(!current()||currentRequest!==request)return;if(page.output_version!==run.output_version||page.step_execution_id!==step.step_execution_id)throw new Error('步骤输出版本已变化，请重新打开');if(!append)items.replaceChildren();items.querySelector('button')?.remove();const names={retained:'保留',rejected:'淘汰',review:'待复核',pending:'处理中',blocked:'阻断'};for(const item of page.items){const row=document.createElement('div');row.className='search-hit cleaning-hit cleaning-status-'+item.status;const title=document.createElement('strong');title.textContent=item.candidate_id;const badge=document.createElement('span');badge.className='cleaning-badge cleaning-badge-'+item.status;badge.textContent=names[item.status]||item.status;const head=document.createElement('div');head.className='cleaning-head';head.append(badge,title);row.append(head);if(item.preview_status==='available'){const img=document.createElement('img');img.alt=`清洗${names[item.status]||item.status}：${item.candidate_id}`;img.loading='lazy';row.append(img);observeCleaningImage(img,taskId,item.candidate_id)}const list=document.createElement('ul');list.className='cleaning-reasons';for(const reason of item.reasons){const li=document.createElement('li');const rb=document.createElement('span');rb.className='cleaning-badge cleaning-badge-'+reason.verdict;rb.textContent=reason.verdict==='pass'?'通过':reason.verdict==='fail'?'淘汰':'待定';const rid=document.createElement('span');rid.className='cleaning-rule-id';rid.textContent=reason.rule_id;const rcode=document.createElement('span');rcode.className='cleaning-reason-code';rcode.textContent=cleaningReasonLabel(reason.reason_code);li.append(rb,rid,rcode);const s=document.createElement('span');s.className='cleaning-reason-summary';s.textContent=cleaningReasonSummary(reason);li.append(s);list.append(li)}row.append(list);items.append(row)}if(!page.items.length&&!append)items.textContent='该分类暂无素材。';if(page.next_cursor){const more=document.createElement('button');more.textContent='加载更多步骤结果';more.onclick=()=>read(page.next_cursor,true);items.append(more)}loaded=true}catch(error){if(current()&&currentRequest===request)items.textContent='步骤分页读取失败：'+error.message}};select.addEventListener('change',()=>read());detail.addEventListener('toggle',()=>{if(detail.open&&!loaded)read()});detail.append(select,items);container.append(detail)}
    }
  }catch(error){if(current())container.textContent='步骤结果暂不可读：'+error.message}finally{button.disabled=false}
}
function observeCleaningImage(img,taskId,candidateId){const load=async()=>{if(!img.isConnected)return;try{const response=await fetch(`/v1/retrieval/tasks/${encodeURIComponent(taskId)}/cleaning/candidates/${encodeURIComponent(candidateId)}/preview`,{headers});if(!response.ok)throw new Error('图片不可读取');const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('返回内容不是图片');if(!img.isConnected)return;const url=URL.createObjectURL(blob);img.addEventListener('load',()=>URL.revokeObjectURL(url),{once:true});img.addEventListener('error',()=>URL.revokeObjectURL(url),{once:true});img.src=url}catch(error){if(img.isConnected){const note=document.createElement('small');note.className='preview-note';note.textContent='图片预览失败：'+error.message;img.replaceWith(note)}}};if(typeof IntersectionObserver==='undefined'){load();return}const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)){observer.disconnect();load()}},{rootMargin:'250px'});observer.observe(img)}
function showProgress(...args){return measureChatRender('progress_render',()=>showProgressImpl(...args),args[0])}
function showProgressImpl(progress,results,searchProgress=null,candidates=null,manifest=null){state.currentProgress=progress;if(results)state.currentResults=results;$('task-sidebar').classList.remove('hidden');$('task-sidebar-identity').classList.remove('hidden');$('task-sidebar-id').textContent=state.task?.task_id||progress.task_id||'';$('task-sidebar-summary').textContent=`版本 ${progress.revision} · 目标 ${progress.counts.target} · 有效 ${progress.counts.accepted===null?'未知':progress.counts.accepted}`;const nodes=$('task-sidebar-nodes');if(state.sidebarTaskId===progress.task_id)for(const card of nodes.querySelectorAll('details.node-card[data-node]'))(state.nodeOpen??={})[`${progress.task_id}:${card.dataset.node}`]=card.open;state.sidebarTaskId=progress.task_id;const signature=JSON.stringify([progress,results,state.confirmedRevision,manifest]);if(state.sidebarSignature===signature&&nodes.children.length===progress.nodes.length){const retrieval=progress.nodes.findIndex(node=>node.node==='retrieval');if(retrieval>=0)nodes.children[retrieval].replaceWith(nodeCard(progress.nodes[retrieval],retrieval,progress,results,searchProgress,candidates,manifest))}else{nodes.replaceChildren();progress.nodes.forEach((node,index)=>nodes.append(nodeCard(node,index,progress,results,searchProgress,candidates,manifest)));state.sidebarSignature=signature}}
function searchBranchCounts(progress){
 if(progress?.branch_counts)return progress.branch_counts;
 const groups=new Map();for(const m of progress?.members||[]){const key=m.branch_id||m.plan_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(m)}
 let success=0,failed=0,running=0,waiting=0;
 for(const members of groups.values()){const done=m=>Boolean(m.done||m.status==='stopped'||(m.stop_reason&&!['more_available','retry_pending'].includes(m.stop_reason)));const error=m=>Boolean(m.last_error||['failed','error'].includes(m.status)||['failed','error'].includes(m.stop_reason));if(members.every(done)){if(members.some(error))failed++;else success++}else if(progress.job_status==='failed'){failed++}else if(members.some(m=>m.pages||m.committed_pages||m.status==='pending'||m.status==='more_available'))running++;else waiting++}
 return {success,failed,running,waiting,total:groups.size};
}
function searchStatusLabel(progress){if(progress.pause_reason)return ({CLEANING_IN_PROGRESS:'等待清洗完成',CANDIDATE_WINDOW_READY:'候选窗口已就绪，等待清洗',TARGET_MET:'有效数量已达标',AWAIT_REFILL:'后续检索交由补数'})[progress.pause_reason]||'检索已暂停';const counts=searchBranchCounts(progress);if(progress.execution_budget?.stop_reason)return '预算已用尽';if(progress.execution_queue?.state==='failed')return '检索已停止';if(progress.execution_queue?.state==='queued'&&!progress.pagination_stopped&&!progress.initial_window_complete)return '排队中';if(progress.job_status==='failed')return '检索失败';if(progress.job_status==='superseded')return '已替代';if(progress.pagination_stopped)return counts.failed?'部分分支失败':'检索已结束';if(progress.initial_window_complete)return '首轮候选已就绪';return counts.running||counts.success||progress.job_status==='running'?'检索中':'等待检索'}
function searchSummary(progress,candidates){const c=searchBranchCounts(progress);return `${c.success+c.failed}/${c.total} 分支已结束 · 成功 ${c.success} · 失败 ${c.failed} · 进行中 ${c.running} · 待启动 ${c.waiting}\n累计原始命中 ${candidates?.raw_hit_count??'未知'} 条 · 未清洗`}
async function refreshTaskSidebar(workspaceSeed=null){
 if(!state.task)return;const taskId=state.task.task_id;
 const progressLink=$('search-progress-link');if(progressLink)progressLink.href='/production?task='+encodeURIComponent(taskId)+'&node=retrieval';
 const readToken=state.sidebarReadToken=(state.sidebarReadToken||0)+1;
 const same=()=>state.sidebarReadToken===readToken&&state.task?.task_id===taskId;
 try{
  const started=performance.now();
  const seed=workspaceSeed?.task_id===taskId&&workspaceSeed.task_version===state.task.task_version&&workspaceSeed.confirmed_revision===state.task.confirmed_revision&&workspaceSeed.run_generation===state.task.run_generation&&!(state.task.mutation_sequence!=null&&(workspaceSeed.mutation_sequence==null||workspaceSeed.mutation_sequence<state.task.mutation_sequence))?workspaceSeed:null;
  const [workspace,results]=await Promise.allSettled([seed?Promise.resolve(seed):api(`/v1/retrieval/tasks/${taskId}/workspace`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=1`)]);
  if(!same())return;
  if(workspace.status!=='fulfilled')throw workspace.reason;
  const data=workspace.value;
  if(data.agent_retrieval){renderAgentWindowWorkspace(data);return data.workflow}
  if(data.task_id!==taskId||!data.workflow||data.workflow.binding.task_version!==data.task_version)throw new Error('任务状态绑定已变化，请重读');
  const previous=state.searchSnapshot;
  const snapshot={taskId,revision:data.confirmed_revision,generation:data.run_generation,
    progress:data.progress,workflow:data.workflow,search:data.search,manifest:data.manifest,
    candidates:{raw_hit_count:data.progress.counts.returned,verified_count:data.manifest?.candidate_count},updated:new Date().toLocaleTimeString()};
  const bound=page=>page?.task_id===taskId&&page.revision===data.confirmed_revision&&page.run_generation===data.run_generation&&page.task_version===data.task_version;
  if(results.status==='fulfilled'&&bound(results.value))snapshot.results=results.value;
  else if(bound(previous?.results))snapshot.results=previous.results;
  state.task={...state.task,...data};state.searchSnapshot=snapshot;
  state.candidateManifest=data.manifest?.confirmed&&data.manifest.expected_task_version===data.task_version?data.manifest:null;
  showProgress(snapshot.progress,snapshot.results,snapshot.search,snapshot.candidates,snapshot.manifest);
  $('retrieval-live-status').textContent=data.retrieval_agent_job?('检索 Agent · '+({unknown:'执行结果待核对，不会自动重试',failed:'检索未完成，请查看说明',queued:'已接收词表，等待查找'}[data.retrieval_agent_job.status]||data.workflow.summary)):data.workflow.summary;
  if(data.retrieval_agent_job&&typeof showConfirmedTermsAgentStatus==='function')showConfirmedTermsAgentStatus(data.retrieval_agent_job);
  if(typeof recordChatTiming==='function')recordChatTiming('sidebar_update',performance.now()-started);
  if(data.workflow.available_actions.some(action=>action.id==='confirm_candidates')){
    const identity=[taskId,data.confirmed_revision,data.run_generation,data.task_version].join(':');state.manifestPrompted=state.manifestPrompted||new Set();
    if(!state.manifestPrompted.has(identity)){state.manifestPrompted.add(identity);guidedAction(message('assistant','检索结果已返回',data.workflow.nodes.find(node=>node.node==='retrieval').detail),'确认候选清单',showCandidateManifestReview)}
  }
  return data.workflow;
 }catch(error){if(same()){$('retrieval-live-status').textContent='状态读取失败：'+error.message+'；上次状态可能滞后。';$('task-sidebar-summary').textContent='状态未刷新，以下为上次读取结果';if(state.searchSnapshot)state.searchSnapshot.error=error.message}return null}
}

const resultViewLabels={accepted:'可用',review:'需要判断',failed:'规则淘汰',duplicate:'重复合并',historical:'历史排重',user_excluded:'已排除'};
function resetResultWorkspace(){$('result-details').open=false;state.resultTaskId=null;state.currentResults=null;state.resultView='accepted';$('result-sidebar').classList.add('hidden');renderResultCounts(null);syncResultTabs('accepted');for(const button of document.querySelectorAll('[data-result-view]'))button.disabled=true;$('result-list').replaceChildren()}
function renderResultCounts(results){$('result-compact-counts').textContent=results?.available?`可用 ${results.counts.accepted??'—'} · 待判断 ${results.counts.pending_review??'—'} · 缺口 ${results.counts.gap??'—'}`:'查看素材与复核';const counts=$('result-counts');counts.replaceChildren();if(!results?.available)return;for(const [label,value] of [['可用',results.counts.accepted],['候补',results.counts.reserve],['需判断',results.counts.pending_review],['规则淘汰',results.counts.failed],['已排除',results.counts.user_excluded??0],['缺口',results.counts.gap]]){const item=document.createElement('span');item.className='result-count';const strong=document.createElement('strong');strong.textContent=value??'—';item.append(strong,label);counts.append(item)}}
function syncResultTabs(view){for(const button of document.querySelectorAll('[data-result-view]'))button.setAttribute('aria-selected',String(button.dataset.resultView===view))}
async function loadResultView(view,cursor=null,append=false){const taskId=state.task?.task_id;if(!taskId)return;try{const page=await api(`/v1/retrieval/tasks/${taskId}/results?view=${view}&limit=20`+(cursor?'&cursor='+encodeURIComponent(cursor):''));state.resultView=view;showResults(page,taskId,append)}catch(error){$('status').textContent='结果读取失败：'+error.message}}
function openResultFeedback(mode,taskId,results,item,scope='candidate'){state.resultFeedback={mode,taskId,results,item,scope};const labels={exclude:'排除素材',rule:'升级为全任务规则',manual_accept:'人工通过',manual_reject:'人工淘汰'};$('result-feedback-title').textContent=labels[mode];const affected=scope==='near_duplicate_cluster'?(item.near_duplicate_matches?.length||0)+1:1;$('result-feedback-impact').textContent=mode==='manual_accept'?`人工通过后：待复核 -1，有效数最多 +1。\n旧结果版本保持只读。`:mode==='manual_reject'?`人工淘汰后：待复核 -1，淘汰 +1。\n旧结果版本保持只读。`:mode==='rule'?`这会先生成规则草稿；确认草稿后才创建新版本并重验受影响素材。`:`预计排除 ${affected} 张；系统会立即重算候补和缺口。`;$('result-feedback-reason-choice').value=mode==='manual_accept'?'人工确认符合需求':'';$('result-feedback-reason').value='';$('submit-result-feedback').textContent=mode==='rule'?'生成规则草稿':'确认并生成新结果版本';$('result-feedback-dialog').showModal()}
async function excludeResultItem(taskId,results,item,scope,reason){const label=scope==='candidate'?'仅排除此图':'排除相似簇';const body={expected_task_version:results.task_version,result_set_digest:results.result_set_digest,candidate_id:item.candidate_id,scope,reason};const changed=await api(`/v1/retrieval/tasks/${taskId}/result-exclusions`,{method:'POST',keyValue:key('result-exclusion'),body});message('assistant',label,`已排除 ${changed.applied_exclusion.candidate_ids.length} 张；有效数 ${changed.accepted_count}，缺口 ${changed.gap}。旧结果版本保持历史只读。`);await openTask(taskId)}
async function reviewResultItem(taskId,results,item,action,reason){const changed=await api(`/v1/retrieval/tasks/${taskId}/manual-reviews`,{method:'POST',keyValue:key('manual-review'),body:{expected_task_version:results.task_version,result_set_digest:results.result_set_digest,candidate_id:item.candidate_id,asset_version:item.asset_version,action,reason}});message('assistant',action==='accept'?'人工通过':'人工淘汰',`裁决已写入新结果版本；有效 ${changed.accepted_count}，待复核 ${changed.pending_review_count}，缺口 ${changed.gap}。`);await openTask(taskId)}
async function promoteResultRule(taskId,item,reason){if(state.feedback){throw new Error('请先处理上一条未确认反馈')}const text=`以后所有候选都要遵守：${reason}（由结果素材 ${item.candidate_id} 的反馈升级）`;state.feedback=await api(`/v1/retrieval/tasks/${taskId}/feedback`,{method:'POST',keyValue:key('promote-result-rule'),body:{text,scope:'all',expected_revision:state.task.confirmed_revision,expected_task_version:state.task.task_version}});const bubble=message('assistant','升级为规则 · 等待确认',`规则草稿：“${reason}”。确认后才会创建新版本，并从历史原始候选池增量重算；当前结果不变。`);guidedAction(bubble,'确认创建新版本',()=>decideFeedback('confirm'));guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);state.feedbackReviewBubble=bubble;$('feedback-box').classList.add('hidden')}
async function submitResultFeedback(event){event.preventDefault();const pending=state.resultFeedback;if(!pending)return;const choice=$('result-feedback-reason-choice').value,note=$('result-feedback-reason').value.trim(),reason=[choice&&choice!=='custom'?choice:'',note].filter(Boolean).join('：');if(!reason){$('result-feedback-impact').textContent='请选择原因或填写判断依据。';return}$('result-feedback-dialog').close();if(typeof candidateDialog!=='undefined'&&candidateDialog.open)closeCandidateBrowser();setBusy(true);try{if(pending.mode==='exclude')await excludeResultItem(pending.taskId,pending.results,pending.item,pending.scope,reason);else if(pending.mode==='rule')await promoteResultRule(pending.taskId,pending.item,reason);else await reviewResultItem(pending.taskId,pending.results,pending.item,pending.mode==='manual_accept'?'accept':'reject',reason)}catch(error){$('status').textContent='操作未生效：'+error.message}finally{state.resultFeedback=null;setBusy(false)}}
function showResults(...args){return measureChatRender('results_render',()=>showResultsImpl(...args),args[0])}
function showResultsImpl(results,taskId,append=false){
  const list=$('result-list');
  if(!append)list.replaceChildren();else $('result-more')?.remove();
  if(!results.available){resetResultWorkspace();return}
  if(state.resultTaskId!==taskId){$('result-details').open=false;state.resultTaskId=taskId}
  $('result-sidebar').classList.remove('hidden');
  for(const button of document.querySelectorAll('[data-result-view]'))button.disabled=false;
  state.currentResults=results;state.resultView=results.view||state.resultView||'accepted';if(typeof syncResultTabs==='function')syncResultTabs(state.resultView);if(typeof renderResultCounts==='function')renderResultCounts(results);
  if(!append&&(results.group_progress||[]).length)card('分组进度',(results.group_progress||[]).map(row=>[row.label,`合格 ${row.accepted_count}/${row.target_count} · 缺口 ${row.gap}`]),[],list);
  for(const item of results.items){const libraries=[...new Set((item.sources||[]).map(source=>source.library_id).filter(Boolean))],actions=[];if(results.view==='accepted'){actions.push(['仅排除此图',()=>openResultFeedback('exclude',taskId,results,item,'candidate'),true],['升级为规则',()=>openResultFeedback('rule',taskId,results,item),true]);if(item.near_duplicate_matches?.length)actions.splice(1,0,['排除相似簇',()=>openResultFeedback('exclude',taskId,results,item,'near_duplicate_cluster'),true])}else if(results.view==='review'){actions.push(['人工通过',()=>openResultFeedback('manual_accept',taskId,results,item),false],['人工淘汰',()=>openResultFeedback('manual_reject',taskId,results,item),true])}const verdict=item.manual_review?`人工${item.manual_review.action==='accept'?'通过':'淘汰'}：${item.manual_review.reason}`:item.cleaning_status==='pass'?'全部硬规则通过':item.cleaning_status==='fail'?'未通过规则':'等待人工复核';const resultCard=card(`${resultViewLabels[results.view]||'结果'}素材`,[['素材标识',item.candidate_id],['素材版本',item.asset_version],['当前结论',verdict],...(item.elimination_stage?[['排除环节',({cleaning:'清洗校验',deduplication:'内容去重',historical_deduplication:'历史批次去重',selection:'数量选样',owner_review:'人工排除'})[item.elimination_stage]||item.elimination_stage]]:[]),...(item.representative_candidate_id?[['合并至',item.representative_candidate_id]]:[]),['规则证据',(item.reasons||[]).map(reason=>`${reason.rule_id||reason.reason_code}：${reason.verdict==='pass'?'通过':reason.verdict==='fail'?'未通过':'待复核'}${reason.reason_summary?'（'+reason.reason_summary+'）':''}`).join('；')||'未保存逐规则原因'],...(item.user_exclusion?[['用户排除原因',item.user_exclusion.reason]]:[]),['来源',item.source_count?`${item.source_count} 条命中 · ${libraries.join('、')}`:'未记录来源'],['预览',item.preview_status==='available'?'每次读取重新校验授权':'当前不可读取']],actions,list);if(item.preview_status==='available'){const img=document.createElement('img');img.alt=`${resultViewLabels[results.view]||'结果'}素材 · ${item.candidate_id}`;img.loading='lazy';resultCard.insertBefore(img,resultCard.querySelector('.actions'));observeCleaningImage(img,taskId,item.candidate_id)}}
  if(!results.items.length&&!append)card(`暂无${resultViewLabels[results.view]||'当前分类'}素材`,[],[],list);
  if(results.next_cursor){card('更多结果',[],[['加载更多',()=>loadResultView(results.view,results.next_cursor,true),false]],list).id='result-more'}
  if(!append&&results.view==='accepted'&&results.counts.gap>0&&state.task?.task_id===taskId&&!state.task.pending_draft&&state.task.confirmed_revision===results.revision){
    if(state.task.run_status==='PARTIAL')offerGapExpansion(results,list);
    else if(['RUNNING','AWAITING_INPUT'].includes(state.task.run_status))offerRefillAction(results,taskId,list)
  }
}
function offerGapExpansion(results,list){const gap=results.counts.gap;card('有效数量不足',[['缺口',gap],['下一步','当前批准位置不可继续；可生成补充检索词候选，新版词表仍须由你确认。']],[['根据缺口补充检索词',()=>showPostTaskExpansion(`当前权威结果缺口 ${gap} 张，请保持原目标数量和硬约束，补充不同可见场景的检索词。`,{kind:'expansion',target_count:null,requested_terms_per_group:null,count_edits:[],expansion_instruction:`当前权威结果缺口 ${gap} 张；保留原主体、目标数量和硬约束，只补充有助于覆盖缺口的具体画面检索词。`},'system'),false]],list)}
async function offerRefillAction(results,taskId,list){const token=(state.refillOfferToken||0)+1;state.refillOfferToken=token;try{const plan=await api(`/v1/retrieval/tasks/${taskId}/refill`);if(state.refillOfferToken!==token||state.task?.task_id!==taskId||state.task.confirmed_revision!==results.revision)return;if(plan.action==='await_owner'&&plan.next_position){const position=plan.next_position;card('继续补数',[['缺口',plan.gap],...(plan.group_gaps||[]).filter(row=>row.gap).map(row=>[row.label+' 缺口',row.gap]),...(position.group_labels?.length?[['补充分组',position.group_labels.join('、')]]:[]),['检索词',position.query],['素材库',position.library_id],...(position.selection?[['选择依据',({continue_productive_branch:'继续有有效新增的分支',probe_approved_alternative:'尝试其他已批准分支',retry_low_yield_branch:'其他分支不足，继续低产出分支'})[position.selection.reason]||position.selection.reason]]:[]),['位置',position.position.cursor||`深度 ${position.position.depth}`],['说明','沿用当前已确认检索词和范围，只确认启动这一轮。']],[['确认启动本轮补数',()=>confirmRefillIteration(taskId,plan),false]],list);return}if(plan.action==='wait'){card('补数进行中',[['轮次',plan.iteration],['缺口',plan.gap],['说明','已预留当前批准位置，正在等待检索回传。'],...refillRoundRows(plan.execution)],[],list);return}if(plan.action==='partial_delivery'){const execution=plan.execution||{},actions=[];if(results.counts.accepted>0)actions.push(['按当前有效素材部分交付',()=>openDelivery(taskId),false]);card('自动补数已停止',[['缺口',plan.gap],['停止原因',refillStopLabel(plan.stop_reason)],['已发轮次',execution.issued_rounds??'未知'],['已完成轮次',execution.completed_rounds??'未知'],['连续无进展',execution.stagnant_rounds??'未知'],['已预留 VLM 调用',execution.vlm_calls??'未知'],...refillRoundRows(execution),['下一步',results.counts.accepted>0?'可部分交付，或修改方案重新检索':'当前无有效素材，请修改方案重新检索']],actions,list);offerGapExpansion(results,list)}}catch(error){if(state.refillOfferToken!==token)return;if(error.code==='REFILL_NOT_CONFIGURED'){offerGapExpansion(results,list);return}card('补数状态读取失败',[['说明',error.message]],[],list)}}
async function confirmRefillIteration(taskId,plan){setBusy(true);try{const result=await api(`/v1/retrieval/tasks/${taskId}/refill`,{method:'POST',keyValue:`refill:${taskId}:${plan.next_position.position_fingerprint}`,body:{expected_revision:plan.revision,expected_generation:plan.generation,expected_task_version:plan.task_version,position_fingerprint:plan.next_position.position_fingerprint,action:'confirm'}});message('assistant',result.replayed?'补数轮次已存在':'补数轮次已启动',`第 ${result.iteration} 轮已按当前确认检索词提交。`);const [task,progress,results]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}`),api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`)]);state.task=task;showProgress(progress,results);showResults(results,taskId);refreshTaskSidebar()}catch(error){$('status').textContent=error.message}finally{setBusy(false)}}
async function loadMoreResults(taskId,cursor){setBusy(true);try{const results=await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20&cursor=${encodeURIComponent(cursor)}`);showResults(results,taskId,true)}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function startIncrementalRecompute(taskId){setBusy(true);try{const proposal=await api(`/v1/retrieval/tasks/${taskId}/cleaning/prepare`,{method:'POST',body:{incremental_recompute:true}});const operation=await api(`/v1/retrieval/tasks/${taskId}/cleaning/confirm`,{method:'POST',keyValue:key('incremental-recompute-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});message('assistant','增量重算已启动',`历史原始候选 ${proposal.candidate_count} 张已绑定当前 revision；后台状态：${operation.status}。`);state.cleaningOperation=operation;pollVlmCleaning(taskId,operation.operation_id)}catch(error){$('status').textContent='增量重算未启动：'+(error.detail||error.message)}finally{setBusy(false)}}
async function openTask(taskId){sessionStorage.removeItem('dsh-fresh-chat');const token=state.historyOpenToken=(state.historyOpenToken||0)+1,started=performance.now();setBusy(true);showHistoryLoading('正在打开生产线路…');$('status').textContent='正在打开生产线路…';try{const binding=await api(`/v1/retrieval/tasks/${taskId}/read-binding`);if(token!==state.historyOpenToken)return false;if(binding.proposal.query_spec.media_type==='video'){await openVideoWorkspace(taskId);return true}const task=await api(`/v1/retrieval/tasks/${taskId}/workspace`);if(token!==state.historyOpenToken)return false;if(task.agent_retrieval){resetTimeline('历史需求',task.raw_request);return await openAgentWindowWorkspace(task)}resetTimeline('历史需求',task.raw_request||`任务 ${taskId}`);timeline.dataset.firstTextMs=String(performance.now()-started);const [revisions,progress,results,history]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}/revisions`),task.progress||api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`),task.conversation_id?api(`/v1/retrieval/conversations/${task.conversation_id}`).catch(()=>null):null]);if(token!==state.historyOpenToken)return false;state.task=task;restoreFollowupContext(task,history?.task_messages);state.conversationId=task.conversation_id||null;localStorage.setItem('dsh-task-id',taskId);showProgress(progress,results);showResults(results,taskId);startTaskEvents(taskId,progress.as_of_sequence);for(const revision of revisions.items){const changes=(revision.changes||[]).map(item=>item.label+(item.field==='target_count'?`：${item.before} → ${item.after}`:'')).join('；')||'初始确认';card(`正式版本 ${revision.revision}`,[['需求',revision.proposal.query_spec.raw_request],['规则变化',changes],['版本隔离',revision.revision_note||'只读取本版本结果'],['方案摘要',revision.proposal_digest]]);showSavedGroups(revision.proposal.query_spec.grouped_expansion)}if(task.confirmed_revision>1)try{const recompute=await api(`/v1/retrieval/tasks/${taskId}/cleaning/recompute`);if(token!==state.historyOpenToken)return false;if(recompute.available){const report=recompute.report,counts=report?.counts;card('反馈后的增量重算',[['候选来源','历史原始输入清单（包含此前淘汰项）'],['规则变化',(recompute.rule_changes||[]).map(item=>`${item.rule_id}：${item.change}`).join('；')||'规则完全兼容'],['复用已有事实',recompute.reused_candidate_count],['需要重验',recompute.revalidated_candidate_count],['可恢复的旧淘汰项',recompute.restorable_rejected_count],['重验素材',(report?.revalidated_candidate_ids||recompute.candidates.filter(item=>item.action==='revalidate').map(item=>item.candidate_id)).join('、')||'无'],['计数变化',counts?`有效 ${counts.before.accepted??'待发布'} → ${counts.after.accepted??'待发布'}；通过 ${counts.before.pass} → ${counts.after.pass}；淘汰 ${counts.before.fail} → ${counts.after.fail}；待复核 ${counts.before.unknown} → ${counts.after.unknown}`:'新版本尚未发布，不沿用旧数量'],['版本栅栏',`revision ${recompute.publication_fence.revision} · generation ${recompute.publication_fence.generation} · version ${recompute.publication_fence.task_version}`],['说明',recompute.note]],report?[]:[['确认增量重算',()=>startIncrementalRecompute(taskId),false]])}}catch(error){if(token!==state.historyOpenToken)return false;if(!['CLEANING_NOT_CONFIGURED','DETECTOR_NOT_RELEASED'].includes(error.code))card('增量重算状态',[['说明',error.message]])}renderSavedTaskMessages([...(history?.clarification_messages||[]),...(history?.task_messages||[])]);if(task.pending_draft){state.feedback=task.pending_draft;const bubble=message('assistant','有一条未确认的反馈','“'+state.feedback.feedback_text+'”仍是草稿，尚未改变当前需求。');guidedAction(bubble,'重新理解并处理',()=>routeTaskFollowup(state.feedback.feedback_text));guidedAction(bubble,'重新描述',editFeedback,true);guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);state.feedbackReviewBubble=bubble}$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');$('status').textContent='';refreshTaskSidebar();offerExistingSearch(taskId);refreshHistory();void recoverTaskTurn(taskId);return true}catch(e){if(token===state.historyOpenToken)$('status').textContent=e.message;return false}finally{if(token===state.historyOpenToken)setBusy(false)}}
function searchUnavailableReason(preview){
  const routes=preview?.routes||[];
  const missingIndex=routes.filter(route=>!route.options?.length&&route.availability==='no_available_index').map(route=>route.query_text);
  const missingEncoding=routes.filter(route=>!route.options?.length&&route.availability==='query_encoding_unavailable').map(route=>route.query_text);
  const other=routes.filter(route=>!route.options?.length&&!['no_available_index','query_encoding_unavailable'].includes(route.availability)).map(route=>route.query_text);
  const parts=[];
  if(missingIndex.length)parts.push('无匹配的已授权索引：'+missingIndex.join('、'));
  if(missingEncoding.length)parts.push('当前文本编码器无法处理：'+missingEncoding.join('、'));
  if(other.length)parts.push('尚无可执行路由：'+other.join('、'));
  if(!routes.length)parts.push('没有可执行的检索词');
  return parts.join('；')+'。词表已确认，但当前检索环境未能执行这些查询；这不代表图片不存在，也不是零命中。本地测试环境只支持登记的词规则；接入可处理任意描述的真实文本检索服务和授权索引后才能检索。检索命中仅是候选，仍需逐图复判，不能直接计为合格素材。';
}
async function offerExistingSearch(taskId){
  restoreTaskExpansionJob(taskId);
  try{
    const progress=await api(`/v1/retrieval/tasks/${taskId}/first-search/progress`);
    if(state.task?.task_id!==taskId)return;
    if(progress.status==='unapproved'){
      const preview=await api(`/v1/retrieval/tasks/${taskId}/first-search`).catch(()=>null);
      if(state.task?.task_id!==taskId)return;
      if(preview){const unavailable=preview.routes.filter(route=>!route.options.length);if(unavailable.length){const reason=searchUnavailableReason(preview);state.searchAttempt={taskId,revision:state.task.confirmed_revision,status:'unavailable',reason};message('assistant','检索未启动',reason);await refreshTaskSidebar();return}}
      const bubble=message('assistant','','尚未开始检索。');
      guidedAction(bubble,'检查检索计划',guidedStartSearch);
      return;
    }
    await guidedTick();
  }catch(error){
    if(state.task?.task_id!==taskId)return;
    if(error.code==='INVALID_STATE_TRANSITION'&&['AWAITING_INPUT','PARTIAL','COMPLETED','FAILED'].includes(state.task.run_status)){
      $('status').textContent='';
      return
    }
    if(error.code==='REVISION_CONFLICT'){
      const cleaning=await api(`/v1/retrieval/tasks/${taskId}/cleaning/steps`).catch(()=>null);
      if(state.task?.task_id!==taskId)return;
      if(cleaning?.available){showCleaningHandoff(cleaning);return}
      if(state.task.active_cleaning_operation_id){message('assistant','VLM 清洗进行中','检索候选已固定，后台正在执行已确认的清洗方案。');return}
    }
    $('status').textContent='检索进度暂不可读：'+error.message
  }
}
function guidedAction(bubble,label,handler,secondary=false){const box=bubble.querySelector('.actions')||document.createElement('div');box.className='actions';const button=document.createElement('button');button.textContent=label;if(secondary)button.className='secondary';button.addEventListener('click',handler);box.append(button);bubble.append(box);return button}
function reviewDetails(bubble,label,text){const details=document.createElement('details'),heading=document.createElement('summary'),body=document.createElement('p');details.className='analysis-details';heading.textContent=label;body.textContent=text;details.append(heading,body);bubble.append(details)}
function showTermReview(proposal,intro='我建议用这些检索词：'){
  if(state.agentWindowLoading){
    const waiting=message('assistant','','正在核对检索执行范围…');
    void loadAgentWindowProfile().then(()=>{waiting?.remove?.();if(state.proposedExpansion===proposal&&!state.task)showTermReview(proposal,intro)});
    return;
  }
  const execution=state.agentWindowProfile?.confirmed_terms_execution;
  const reviewExecution=state.agentWindowProfile?.candidate_review_execution;
  let reviewCandidates=false;
  const version=proposal.version||1;
  const historySelection=JSON.stringify(state.historyPolicy||null);
  const historyNote=state.historyPolicy?.scope==='selected_batches'?`历史批次去重：明确选择 ${state.historyPolicy.references.length} 个已交付快照，按 SHA-256 相同内容排除。`:'历史批次去重：仅本批内部去重，未选择历史基准。';
  const referenceDigest=state.queryImage?.digest||null,excludeExact=$('query-image-exclude')?.checked===true;
  let refill;try{refill=refillFormValue()}catch(error){$('status').textContent=error.message;return}
  const groups=proposal.plan?.groups||[];const quotaNote=groups.length>1&&groups.some(group=>group.delivery_target!=null)?'\n\n各组目标（每组分别计数，任一组不足都不算完成）：'+groups.map(group=>group.primary_label+' '+group.delivery_target+' 张').join('、'):groups.length>1?'\n\n未单独分配各组数量，将只按总数计数；需要分别计数请写明，例如“狗 20 张、猫 30 张”。':'';
  const referenceNote=state.queryImage?'\n\n参考图：已绑定不可变摘要 '+state.queryImage.digest.slice(0,19)+'…；图片向量用于召回，以上文字用于选库和清洗约束；'+(excludeExact?'会排除与参考图 SHA-256 完全相同的内容。':'不会自动排除参考图相同内容。'):'';
  const compact=proposal.changeSummary;
  const bubble=message('assistant','',(compact?`总目标 ${compact.beforeCount} → ${proposal.count} 张，检索词沿用。`:`合计需要 ${proposal.count} 张\n`+intro+'\n'+proposal.lines)+
    (groups.length>1&&groups.some(group=>group.delivery_target!=null)?'\n各组目标：'+groups.map(group=>group.primary_label+' '+group.delivery_target+' 张').join('、'):'')+
    '\n补数：'+(refill.enabled?'自动补足':'不自动补数')+'\n'+historyNote+
    (execution?'\n确认后，由检索 Agent 按这份完整词表查找图片。':'')+
    (execution?.candidate_intake?'\n候选自动进入现有清洗流程，按已确认条件检测；图片会发送给本次选定的清洗模型，失败或未知不算通过，结果就绪后再确认交付。':''));
  reviewDetails(bubble,compact?'查看完整词表与执行设置':'查看执行设置',
    (compact?proposal.lines+'\n':'')+quotaNote+referenceNote+'\n'+refillSummary(refill)+
    (execution?`\n执行：检索 Agent；来源：${state.agentWindowProfile.library_ids.join('、')}；最多 ${execution.max_calls} 次检索、${execution.max_hit_slots} 条原始命中。找到的候选仍需清洗。`:''));
  if(execution?.candidate_intake&&reviewExecution){
    const label=document.createElement('label'),checkbox=document.createElement('input');
    checkbox.type='checkbox';checkbox.checked=false;
    checkbox.addEventListener('change',()=>{reviewCandidates=checkbox.checked});
    label.append(checkbox,document.createTextNode('先审候选，再自动清洗（可选）'));bubble.append(label);
  }
  guidedAction(bubble,state.queryImage?'确认参考图方案并开始检索':'确认检索词并开始检索',async()=>{
    if(state.agentWindowError){$('status').textContent='检索执行范围暂时无法核对，请刷新后再确认；词表已保留。';return}
    if(execution?.policy_digest!==state.agentWindowProfile?.confirmed_terms_execution?.policy_digest){bubble.querySelector('.actions')?.remove();showTermReview(proposal,'执行范围已变化，请查看最新词表与设置：');return}
    if(state.proposedExpansion!==proposal||state.task||version!==(proposal.version||1)){$('status').textContent='这组检索词已变化，请确认最新词表。';return}
    if(referenceDigest!==(state.queryImage?.digest||null)||excludeExact!==($('query-image-exclude')?.checked===true)){bubble.querySelector('.actions')?.remove();showTermReview(proposal,'参考图设置已变化，请确认最新完整方案：');return}
    if(historySelection!==JSON.stringify(state.historyPolicy||null)){bubble.querySelector('.actions')?.remove();showTermReview(proposal,'历史去重设置已变化，请确认最新完整方案：');return}
    if(reviewCandidates&&reviewExecution?.policy_digest!==state.agentWindowProfile?.candidate_review_execution?.policy_digest){$('status').textContent='候选审阅设置已变化，请重新查看方案。';return}
    await confirmGuidedExpansion(proposal,{value:proposal.lines,disabled:false},refill,reviewCandidates?reviewExecution:execution);
  });
  guidedAction(bubble,'重新生成检索词',async()=>{
    if(state.proposedExpansion!==proposal||state.task)return;
    setBusy(true);
    try{await prepareGuidedExpansion(proposal.raw,proposal.count,undefined,undefined,true);if(state.proposedExpansion!==proposal)bubble.querySelector('.actions')?.remove()}
    catch(error){$('status').textContent='重新生成检索词失败：'+(error.detail||error.message)}
    finally{setBusy(false)}
  },true);
}
async function prepareGuidedExpansion(raw,count,signal,selection,regenerate=false){
  await startExpansionJob({raw_request:raw,dimension_id:'default',target_count:count,model_selection:selection||{llm:$('llm-model').value,vlm:$('vlm-model').value},...(regenerate?{regeneration_key:key('regen')}:{})},signal);
}
function expansionJobPath(session=state.dialogueId){return '/v1/retrieval/projects/demo/dialogue-sessions/'+encodeURIComponent(session)+'/retrieval-agent/terms'}
function clearExpansionPolling(){if(state.expansionPollTimer)clearTimeout(state.expansionPollTimer);state.expansionPollTimer=null;state.expansionJob=null;state.expansionJobBubble=null}
async function startExpansionJob(body,signal){
  if(!state.dialogueId||state.dialogueStage!=='terms')throw new Error('请先完成需求理解，再生成检索词');
  const session=state.dialogueId,version=state.dialogueVersion;
  const job=await api(expansionJobPath(session),{method:'POST',signal,body:{dialogue_session_id:session,dialogue_version:version,...body}});
  if(signal?.aborted||state.dialogueId!==session||state.dialogueVersion!==version)return;
  watchExpansionJob(job);
}
function expansionProgress(job){
  const seconds=Math.max(0,Math.floor((job.elapsed_ms||0)/1000));
  const model=(typeof registeredModelProfiles==='undefined'?[]:registeredModelProfiles).find(item=>item.id===job.model_selection?.llm)?.model;
  const prefix=job.status==='queued'?'扩词排队中':job.completed_terms?'正在扩词':'正在生成首批检索词';
  const count=job.target_terms?`已生成 ${job.completed_terms}/${job.target_terms} 个词`:'';
  return [prefix,model,`已等待 ${seconds} 秒`,count,seconds>=15?'模型响应较慢，可切换模型后重新生成':null].filter(Boolean).join(' · ');
}
function expansionFailureMessage(job){
  const reasons={
    MODEL_RATE_LIMIT:'模型服务暂时繁忙，检索词还没生成完成。请稍后重试，或换个模型再试。',
    MODEL_QUOTA:'模型服务的可用额度不足，请换个模型，或联系管理员处理。',
    MODEL_AUTH_FAILED:'暂时无法连接模型服务，请换个模型，或联系管理员检查设置。',
    MODEL_UNAVAILABLE:'模型服务暂时不可用，请稍后重试。',
    DSH_TIMEOUT:'生成检索词等待超时，请稍后重试。',
    MODEL_REQUEST_TIMEOUT:'生成检索词等待超时，请稍后重试。',
    MODEL_OUTPUT_INVALID:'这次生成的检索词不完整，请重新生成。',
    MODEL_CONFIG_CHANGED:'模型设置已改变，请使用当前模型重新生成检索词。',
    DIALOGUE_CONFLICT:'你的需求已更新，请按最新需求重新生成检索词。',
    REVISION_CONFLICT:'你的需求已更新，请按最新需求重新生成检索词。',
    RECALL_EVIDENCE_CHANGED:'检索结果已更新，请重新查看结果后再调整检索词。',
    EXPANSION_UNCHANGED:'这次没有生成新的检索词，可以换个说法再试。'
  };
  return (reasons[job.error?.code]||'检索词暂时没能生成完成，请稍后重试。')+
    (job.task_id?' 原来的需求和检索词已保留。':' 你的需求已保存。');
}
async function regenerateExpansionJob(job){
    const session=job.dialogue_session_id,version=job.dialogue_version;
    if(state.dialogueId!==session||state.dialogueVersion!==version||state.expansionRegenerating)return;
    state.expansionRegenerating=true;
    try{
      const next=await api(expansionJobPath(session),{method:'POST',body:{dialogue_session_id:session,dialogue_version:version,
        raw_request:job.raw_request,target_count:job.target_count,dimension_id:'default',
        model_selection:{llm:$('llm-model').value,vlm:$('vlm-model').value},regeneration_key:key('regenerate'),
        ...(job.requested_terms_per_group?{requested_terms_per_group:job.requested_terms_per_group}:{})}});
      if(state.dialogueId===session&&state.dialogueVersion===version)watchExpansionJob(next);
    }catch(error){$('status').textContent='重新生成失败：'+(error.detail||error.message)}
    finally{state.expansionRegenerating=false;}
}
function watchExpansionJob(job){
  if(state.task||state.dialogueId!==job.dialogue_session_id||state.dialogueVersion!==job.dialogue_version)return;
  if(state.expansionPollTimer)clearTimeout(state.expansionPollTimer);
  if(state.expansionJob?.job_id!==job.job_id){state.expansionJobBubble?.remove();state.expansionJobBubble=null;}
  state.expansionJob=job;
  if(!state.expansionJobBubble)state.expansionJobBubble=message('assistant','生成检索词','');
  const bubble=state.expansionJobBubble,line=bubble.querySelector('p');
  const node=$('task-sidebar-nodes')?.querySelector('.node-card .node-status');
  if(node)node.textContent=job.status==='failed'?'暂未完成':job.status==='completed'?'待确认':job.target_terms?`已生成 ${job.completed_terms}/${job.target_terms}`:'正在生成检索词';
  if(job.status==='completed'&&job.result?.plan){
    bubble.remove();state.expansionJobBubble=null;
    if(!job.result.plan.groups.length||job.result.plan.groups.every(group=>['图片','图像','素材','数据'].includes(group.primary_label))){message('assistant','','要找什么主体或场景？例如“狗”或“客厅”。');return}
    if(state.proposedExpansion?.jobId!==job.job_id||(job.confirmation&&!state.proposedExpansion?.confirmation)){
      const previous=state.proposedExpansion;
      const lines=job.result.plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
      state.proposedExpansion={raw:job.raw_request,count:job.target_count,plan:job.result.plan,lines,version:(previous?.version||0)+1,jobId:job.job_id,agent:job.agent,confirmation:job.confirmation};
      state.guidedPhase='expansion';renderPlanningWorkflow('expansion');saveDialogueSession();
      showTermReview(state.proposedExpansion,job.feedback_text?'已调整检索词：':'检索词：');
    }
    return;
  }
  if(job.status==='failed'){
    line.textContent=expansionFailureMessage(job);
    bubble.querySelector('.actions')?.remove();
    guidedAction(bubble,'重试',async()=>{try{const next=await api(expansionJobPath(job.dialogue_session_id)+'/'+job.job_id+'/retry',{method:'POST',body:{}});watchExpansionJob(next)}catch(error){$('status').textContent=expansionFailureMessage({error})}});
    if(!job.feedback_text)guidedAction(bubble,'重新生成检索词',()=>regenerateExpansionJob(job));
    return;
  }
  const first=job.preview_groups?.slice(0,2).map(group=>`${group.primary_label}：${group.auxiliary_terms.slice(0,3).join('、')}`).filter(Boolean)||[];
  line.textContent=expansionProgress(job)+(first.length?'\n首批预览（生成中，暂不可确认）：'+first.join('；'):'');
  bubble.querySelector('.actions')?.remove();
  if((job.elapsed_ms||0)>=15000&&!job.feedback_text)guidedAction(bubble,'按当前模型重新生成',()=>regenerateExpansionJob(job));
  state.expansionPollTimer=setTimeout(async()=>{try{const latest=await api(expansionJobPath(job.dialogue_session_id)+'/'+job.job_id);if(state.expansionJob?.job_id===job.job_id)watchExpansionJob(latest)}catch(error){if(state.dialogueId===job.dialogue_session_id){line.textContent='扩词进度暂时读取失败：'+(error.detail||error.message);state.expansionPollTimer=setTimeout(()=>watchExpansionJob(job),4000)}}},2000);
}
async function restoreExpansionJob(){if(!state.dialogueId||state.dialogueStage!=='terms')return;try{const job=await api(expansionJobPath());if(state.dialogueId===job.dialogue_session_id)watchExpansionJob(job)}catch(error){if(error.code!=='EXPANSION_JOB_NOT_FOUND')$('status').textContent='扩词状态读取失败：'+(error.detail||error.message)}}
function saveDialogueSession(){if(state.dialogueId){sessionStorage.removeItem('dsh-fresh-chat');sessionStorage.setItem('dsh-dialogue',JSON.stringify({id:state.dialogueId,proposal:state.proposedExpansion}));saveCurrentModelChoice('dialogue:'+state.dialogueId)}else sessionStorage.removeItem('dsh-dialogue')}
function insertRequestExample(){
  const input=$('request-text');
  if(input.value.trim()){$('status').textContent='输入框已有内容，保留你的文字；清空后可填入参考说法。';input.focus();return}
  input.value='找【内容】图片，需要【数量】张；画幅或文件格式不限。';
  const start=input.value.indexOf('【内容】');input.focus();input.setSelectionRange(start,start+'【内容】'.length);
}
async function restoreDialogueSession(previewText=null){
  let saved;try{saved=JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')}catch{return}
  if(!saved?.id)return;
  const openToken=state.historyOpenToken=(state.historyOpenToken||0)+1;
  const textStarted=performance.now();
  resetTimeline(previewText?'历史需求摘要':'历史 Chat',previewText||'正在读取历史消息…');
  if(previewText){timeline.dataset.firstTextMs=String(performance.now()-textStarted);timeline.dataset.firstTextSource='history-summary'}
  try{
    const current=await api('/v1/retrieval/projects/demo/dialogue-sessions/'+encodeURIComponent(saved.id));
    if(openToken!==state.historyOpenToken)return;
    state.dialogueId=current.session_id;state.dialogueVersion=current.version;state.dialogueStage=current.stage;
    if(current.stage==='terms')renderPlanningWorkflow('expansion');
    const models=readStoredModelChoice('dialogue:'+saved.id)||state.serverModelDefaults;
    if(models)restoreModelChoice(models);
    timeline.replaceChildren();
    if(!current.messages.length){message('system','新建数据需求','告诉我想找什么；不清楚的地方我会先问，确认检索词后才开始。');await refreshHistory();$('request-text').focus();return}
    if(current.stage==='terms'&&saved.proposal){state.proposedExpansion=saved.proposal;state.guidedPhase='expansion'}
    for(const item of current.messages){if(item.role==='assistant')message('assistant','',item.text);else message('user','',item.text)}
    if(state.proposedExpansion)showTermReview(state.proposedExpansion);
    else if(current.stage==='terms'&&current.request.target_count){const bubble=message('assistant','','历史消息已恢复。需要检索词时，可重新生成待确认的词表。');guidedAction(bubble,'重新生成检索词',async()=>{setBusy(true);try{await prepareGuidedExpansion(current.request.effective_request||current.request.raw_request,current.request.target_count)}catch(error){$('status').textContent='生成检索词失败：'+(error.detail||error.message)}finally{setBusy(false)}})}
    else if(!current.messages.some(item=>item.role==='assistant'))message('assistant','',current.summary+'。'+(current.question==='confirm_summary'?'对吗？':'请继续补充。'));
    await restoreExpansionJob();
    await refreshHistory();
  }catch(error){if(openToken!==state.historyOpenToken)return;$('status').textContent='历史 Chat 加载失败：'+(error.detail||error.message);resetTimeline('历史 Chat','历史消息暂时读取失败，点击左侧 Chat 可重试。')}
}
async function prepareTask(){
  const input=$('request-text'),text=input.value.trim();if(!text||state.requestController)return;
  if(state.task){$('status').textContent='当前任务已确认；请在下方继续对话或新建 Chat。';return}
  if(state.agentWindowProfile?.enabled&&state.agentWindowProfile.chat_entry==='candidate'&&!state.dialogueId&&!state.proposedExpansion&&!state.resumeDraft){
    if(state.agentWindowError){$('status').textContent=state.agentWindowError;return}
    return prepareAgentCandidateRequest(text);
  }
  const unresolved=pendingDialogueCommand();if(unresolved){
    const selectedDialogue=$('dialogue-llm-model')?.value||$('llm-model')?.value;
    const modelChanged=Boolean(selectedDialogue)&&selectedDialogue!==unresolved.body.llm_id;
    if(unresolved.body.message===text&&!modelChanged){await recoverDialogueCommand(true);return}
    if(await recoverDialogueCommand(false,true))return prepareTask();
    return;
  }
  if(state.dialogueModelBinding){$('status').textContent='正在保存对话模型，请稍候。';return}
  if(state.proposedExpansion?.plan?.groups?.some(group=>group.delivery_target!=null)
     &&/^(?:(?:把|将)?(?:图片|照片|目标|数量|张数)?\s*)?(?:改成|调整为|设为|换成)\s*[1-9]\d{0,4}\s*张(?:图片|照片)?(?:[，,、；;\s]*其他不变)?[。.]?$/.test(text)){
    $('status').textContent='当前需求已分配各组数量，请写明每组的新目标。';return;
  }
  const llm=$('llm-model').value;if(!llm){message('assistant','','请先在左下角“模型设置”选择 LLM，我才能理解你的需求。');return}
  const dialogueLlm=$('dialogue-llm-model')?.value||llm;
  const selection={llm,vlm:$('vlm-model').value};
  input.value='';message('user','',text);setDialogueBusy(true);
  const controller=new AbortController();state.requestController=controller;$('stop-request')?.classList?.remove('hidden');
  const active=()=>state.requestController===controller&&!controller.signal.aborted;
  const adjustingTerms=state.dialogueStage==='terms'&&Boolean(state.proposedExpansion);
  const pending=message('assistant','', adjustingTerms?'正在理解检索词调整要求……':'正在理解需求……');pending?.setAttribute?.('role','status');
  let waitStage=adjustingTerms?'正在理解检索词调整要求':'正在理解需求',waitSeconds=0;
  let interpretStarted=null;
  const renderWait=()=>{const line=pending?.querySelector?.('p');if(line)line.textContent=`${waitStage}……已等待 ${waitSeconds} 秒`};
  const waitTimer=setInterval(()=>{waitSeconds+=1;renderWait()},1000);
  try{
    const proposal=state.proposedExpansion;
    if(proposal&&state.targetCountTouched)throw new Error('词表已生成；如需改变图片数量，请直接在对话中说明');
    const hint=state.targetCountTouched&&$('target-count').value?Number($('target-count').value):null;
    if(hint!==null&&(!Number.isInteger(hint)||hint<1||hint>10000))throw new Error('目标数量须为 1 到 10000 张图片');
    const currentGroups=proposal?groupInputBody(proposal.raw,proposal.lines).groups:null;
    let newRequestContext=null;try{newRequestContext=JSON.parse(typeof sessionStorage==='undefined'?'null':sessionStorage.getItem?.('dsh-new-request-context')||'null')}catch{}
    const referenceRequest=!state.dialogueVersion&&/一样|相同|沿用|同样|按(?:之前|刚才|上一)|其他不变|条件不变/.test(text)&&newRequestContext?.reference_request;
    interpretStarted=performance.now();
    const turn=await sendDialogueCommand({session_id:state.dialogueId,expected_version:state.dialogueVersion,message:text,llm_id:dialogueLlm,phase:state.dialogueStage,count_hint:hint,generate_terms_when_complete:true,...(referenceRequest?{reference_request:referenceRequest}:{}),...(currentGroups?{current_groups:currentGroups,current_target_count:proposal.count}:{})},selection,controller.signal);
    if(typeof recordChatTiming==='function')recordChatTiming('understanding',performance.now()-interpretStarted);interpretStarted=null;
    if(!active())return;
    await applyDialogueTurn(turn,proposal,text,selection,controller.signal,active,stage=>{waitStage=stage;waitSeconds=0;renderWait()},turn.recovered===true);
  }catch(e){
    if(interpretStarted!==null&&typeof recordChatTiming==='function')recordChatTiming('understanding_failed',performance.now()-interpretStarted);
    if(state.requestController!==controller)return;
    if(e.name==='AbortError'){const unresolved=Boolean(pendingDialogueCommand());$('status').textContent=unresolved?'已停止等待，后台结果仍需核对。':'已暂停等待，可以修改后重新发送。';const bubble=message('assistant','',unresolved?'已停止等待，可以核对原消息的处理结果。':'已暂停等待。原消息已放回输入框，可以修改后重新发送。');if(unresolved)guidedAction(bubble,'核对处理结果',()=>recoverDialogueCommand());if(!input.value.trim())input.value=text;return}
    const explanation=(e.code==='DSH_TIMEOUT'?(waitStage.includes('扩词')||waitStage.includes('生成检索词')?'扩词 Agent 等待超时，尚未生成可用的新词表；原词表已保留，可重试。':'需求理解模型响应超时，这条消息已保留，未创建任务。'):
      e.code==='NETWORK_UNAVAILABLE'?'与服务的连接中断，原消息已放回输入框。服务恢复后可重试。':
      e.code==='MODEL_RATE_LIMIT'?'模型服务暂时繁忙，原需求已保留，请稍后重试。':
      e.code==='MODEL_OUTPUT_INVALID'?(e.detail||'这次生成的内容未通过校验，这条消息已保留。'):
      e.code==='MODEL_REQUEST_FAILED'?'模型服务暂时没有返回可用结果，这条消息已保留。':
      '这次消息没有处理成功：'+(e.detail||e.message));
    $('status').textContent=explanation;
    showDialogueFailure(e,explanation);
    if(!input.value.trim())input.value=text;
  }
  finally{clearInterval(waitTimer);pending?.remove?.();if(state.requestController===controller){state.requestController=null;$('stop-request')?.classList?.add('hidden');setDialogueBusy(false)}}
}
function dialogueCommandScope(sessionId=state.dialogueId){
  if(sessionId)return 'dialogue:'+sessionId;
  if(!state.newChatToken){try{state.newChatToken=sessionStorage.getItem('dsh-new-chat-token')}catch{}
    if(!state.newChatToken){state.newChatToken=key('chat');try{sessionStorage.setItem('dsh-new-chat-token',state.newChatToken)}catch{}}}
  return 'dialogue:new:'+state.newChatToken;
}
function pendingDialogueCommand(){
  for(const scope of new Set([dialogueCommandScope(),dialogueCommandScope(null)])){
    const pending=pendingTaskTurn(scope);
    if(pending&&(!state.dialogueId||pending.body.session_id===state.dialogueId||pending.result?.session_id===state.dialogueId))return pending;
  }
  return null;
}
function dialogueCommandPath(commandKey){return '/v1/retrieval/projects/demo/dialogue-commands/'+encodeURIComponent(commandKey)}
function showDialogueFailure(error,explanation){
  const pending=Boolean(pendingDialogueCommand());
  const terminal=['failed','interrupted'].includes(error.commandStatus);
  const uncertain=pending&&!terminal;
  const bubble=message('assistant',uncertain?'消息结果待核对':'消息暂未完成',uncertain?'暂时无法核对处理结果，原消息已保留。':explanation);
  if(pending){state.dialogueRecoveryBubble?.remove?.();state.dialogueRecoveryBubble=bubble}
  guidedAction(bubble,uncertain?'核对处理结果':'重试这条消息',()=>pending?recoverDialogueCommand(terminal):prepareTask());
}
async function awaitDialogueCommand(value,commandKey,signal){
  if(value.schema_version!=='dialogue-command@1')return value;
  while(value.status!=='completed'){
    if(['failed','interrupted'].includes(value.status)){
      const error=new Error(value.error?.message||'处理因服务重启中断，可以明确重试。');
      error.code=value.error?.code||'COMMAND_INTERRUPTED';error.detail=error.message;error.commandStatus=value.status;throw error;
    }
    await new Promise((resolve,reject)=>{
      const cleanup=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort)};
      const abort=()=>{cleanup();const error=new Error('Stopped waiting');error.name='AbortError';reject(error)};
      const timer=setTimeout(()=>{cleanup();resolve()},700);
      if(signal?.aborted)abort();else signal?.addEventListener('abort',abort,{once:true});
    });
    value=await api(dialogueCommandPath(commandKey),{signal});
  }
  return value.result;
}
async function sendDialogueCommand(body,selection,signal,retryFailed=false){
  const scope=dialogueCommandScope(body.session_id);
  let pending=pendingTaskTurn(scope);
  if(pending&&pending.body.message!==body.message){const error=new Error('请先核对上一条消息的结果；新输入已保留。');error.code='COMMAND_UNRESOLVED';throw error}
  if(!pending){pending={taskId:scope,key:key('dialogue-command'),body,selection,proposal:state.proposedExpansion};saveTaskTurn(pending)}
  sessionStorage.removeItem('dsh-fresh-chat');
  const received=result=>{saveTaskTurn({...pending,result});return {...result,command_key:pending.key,command_scope:scope}};
  try{const accepted=await api('/v1/retrieval/projects/demo/dialogue-command',{method:'POST',keyValue:pending.key,body:pending.body,signal,retryFailed,asyncCommand:true});
    return received(await awaitDialogueCommand(accepted,pending.key,signal))}
  catch(error){
    if(['NETWORK_UNAVAILABLE','COMMAND_RUNNING'].includes(error.code)){
      try{const receipt=await api(dialogueCommandPath(pending.key),{signal});if(receipt.status==='completed')return received({...receipt.result,recovered:true})}catch{}
    }
    if(['INVALID_SPEC','DIALOGUE_CONFLICT','DIALOGUE_NOT_FOUND','NOT_FOUND','ACCESS_DENIED','MODEL_NOT_CONFIGURED','IDEMPOTENCY_KEY_REQUIRED'].includes(error.code))clearTaskTurn(scope,pending.key);
    throw error;
  }
}
async function recoverDialogueCommand(retryFailed=false,releaseFailed=false){
  if(state.task||state.requestController)return;
  const pending=pendingDialogueCommand();if(!pending)return;
  const controller=new AbortController();state.requestController=controller;setDialogueBusy(true);$('stop-request')?.classList?.remove('hidden');
  const active=()=>state.requestController===controller&&!controller.signal.aborted&&!state.task;
  try{
    let receipt;
    try{receipt=await api(dialogueCommandPath(pending.key),{signal:controller.signal})}
    catch(error){if(error.code==='NOT_FOUND'&&retryFailed)receipt={status:'not_received'};else throw error}
    if(!active())return;
    // A failed dialogue command has no committed turn: completion and session writes are atomic.
    // Only an authoritative failed receipt permits replacing it; uncertain/running work stays protected.
    if(releaseFailed&&receipt.status==='failed'){
      clearTaskTurn(pending.taskId,pending.key);
      state.dialogueRecoveryBubble?.remove?.();state.dialogueRecoveryBubble=null;
      return true;
    }
    let turn;
    if(receipt.status==='completed')turn={...receipt.result,command_key:pending.key,command_scope:pending.taskId};
    else if(retryFailed||receipt.status==='prepared')turn=await sendDialogueCommand(pending.body,pending.selection,controller.signal,true);
    else{
      const running=receipt.status==='understanding';state.dialogueRecoveryBubble?.remove?.();
      const bubble=message('assistant','',running?'上一条消息仍在后台处理，可以查询结果。':receipt.status==='interrupted'?'上一条消息因服务重启中断，可以重试。':receipt.error?.message||'上一条消息未处理完成，可以重试。');
      guidedAction(bubble,running?'查询处理结果':'重试这条消息',()=>recoverDialogueCommand(!running));state.dialogueRecoveryBubble=bubble;return;
    }
    if(!active())return;
    state.dialogueId=turn.session_id;saveDialogueSession();
    await restoreDialogueSession();if(!active()||state.dialogueId!==turn.session_id)return;
    state.dialogueRecoveryBubble?.remove?.();state.dialogueRecoveryBubble=null;
    await applyDialogueTurn(turn,pending.proposal,pending.body.message,pending.selection,controller.signal,active,()=>{},true);
  }catch(error){if(active()){
    if(error.code==='NOT_FOUND'){
      const bubble=message('assistant','','服务尚未收到上一条消息，可以重新发送。');
      guidedAction(bubble,'重新发送',()=>recoverDialogueCommand(true));
    }else if(error.name!=='AbortError'){
      const explanation=error.code==='MODEL_RATE_LIMIT'?'模型服务暂时繁忙，原需求已保留，请稍后重试。':error.detail||error.message;
      $('status').textContent=explanation;showDialogueFailure(error,explanation);
    }
  }}finally{if(state.requestController===controller){state.requestController=null;$('stop-request')?.classList?.add('hidden');setDialogueBusy(false)}}
}
async function applyDialogueTurn(turn,proposal,text,selection,signal,active=()=>true,onStage=()=>{},recovered=false){
    if(!active()||state.task)return;
    if(turn.is_current===false){
      if(turn.command_scope)clearTaskTurn(turn.command_scope,turn.command_key);
      state.dialogueId=turn.session_id;saveDialogueSession();await restoreDialogueSession();return;
    }
    if(typeof sessionStorage!=='undefined')sessionStorage.removeItem?.('dsh-new-request-context');
    const alreadyDisplayed=recovered&&state.dialogueId===turn.session_id&&state.dialogueVersion===turn.version;
    state.targetCountTouched=false;
    state.dialogueId=turn.session_id;state.dialogueVersion=turn.version;state.dialogueStage=turn.stage;saveDialogueSession();
    if(turn.command_scope)clearTaskTurn(turn.command_scope,turn.command_key);
    if(typeof refreshHistory==='function')refreshHistory();
    if(!active())return;
    if(turn.action==='answer'||turn.action==='clarify'){if(!alreadyDisplayed)message('assistant','',turn.reply);return}
    if(turn.reply_source==='model'&&['update','replace','revise_terms'].includes(turn.action))if(!alreadyDisplayed)message('assistant','',turn.reply);
    if(turn.action==='confirm'&&turn.request.media_type==='image'&&turn.request.target_count){
      onStage('正在生成检索词');
      await prepareGuidedExpansion(turn.request.effective_request||turn.request.raw_request,turn.request.target_count,signal,selection);return;
    }
    if(turn.stage==='terms'&&!proposal&&turn.request.target_count){
      onStage('正在生成检索词');
      await prepareGuidedExpansion(turn.request.effective_request||turn.request.raw_request,turn.request.target_count,signal,selection);return;
    }
    if((turn.requirement_edits||[]).length&&turn.stage==='terms'&&proposal){
      onStage('正在生成检索词');
      await prepareGuidedExpansion(turn.request.effective_request,turn.request.target_count,signal,selection);return;
    }
    if(turn.action==='confirm_terms'&&proposal){showTermReview(proposal,'检索词还需要你亲自确认：');return}
    if(turn.action==='revise_terms'&&turn.stage==='terms'&&proposal&&turn.request.target_count!==proposal.count
       &&!turn.expansion_instruction&&!(turn.count_edits||[]).some(edit=>edit.target==='terms')
       &&proposal.plan.groups.every(group=>group.delivery_target==null)){
      const updated={...proposal,count:turn.request.target_count,raw:turn.request.effective_request||proposal.raw,version:(proposal.version||1)+1,changeSummary:{beforeCount:proposal.count}};
      state.proposedExpansion=updated;saveDialogueSession();showTermReview(updated,`目标改为 ${updated.count} 张，检索词保持不变：`);return;
    }
    if(turn.action==='revise_terms'&&turn.stage==='terms'&&proposal){onStage('正在生成检索词');await reviseGuidedExpansion(proposal,{value:proposal.lines},{value:text},turn,selection,signal);return}
    if(turn.action==='replace'){state.proposedExpansion=null;state.guidedPhase='input';state.resumeDraft=null;state.draft=null;saveDialogueSession()}
    if(turn.stage==='request'&&proposal){state.proposedExpansion=null;state.guidedPhase='input';saveDialogueSession()}
    if(!alreadyDisplayed)message('assistant','',turn.reply);
}
async function reviseGuidedExpansion(proposal,editor,feedbackEditor,turn,selection,signal){
  if(state.proposedExpansion!==proposal||state.task)return;
  const feedback=feedbackEditor.value.trim();if(!feedback){$('status').textContent='请先写下希望如何调整检索词';return}
  const llm=selection?.llm??$('llm-model').value,vlm=selection?.vlm??$('vlm-model').value;if(!llm||!vlm){$('status').textContent='按反馈重新扩词需要已配置的 LLM 和 VLM 模型选择';return}
  if(!signal)setBusy(true);
  try{
    const current=groupInputBody(proposal.raw,editor.value.trim()).groups;
    if(!current?.length)throw new Error('请先保留至少一组可调整的检索词');
    const count=turn?.request.target_count||proposal.count;
    const instruction=turn?.expansion_instruction||feedback;
    const edits=(turn?.count_edits||[]).filter(edit=>edit.target==='terms');
    await startExpansionJob({raw_request:turn?.request.effective_request||proposal.raw,dimension_id:'default',target_count:count,model_selection:{llm,vlm},feedback_text:instruction,current_groups:current,count_edits:edits,current_target_count:proposal.count},signal);
    if(signal?.aborted)return;
    feedbackEditor.value='';
  }catch(error){
    if(signal?.aborted)return;
    const explanation=error.code==='EXPANSION_UNCHANGED'?'这次模型没有按你的建议改出新词，原词表已保留。可以重试这条建议。':error.code==='DSH_TIMEOUT'?'扩词 Agent 等待超时，尚未生成可用的新词表；原词表已保留，可重试。':'重新扩词未完成：'+(error.detail||error.message);
    $('status').textContent=explanation;
    const bubble=message('assistant','',explanation);
    if(['EXPANSION_UNCHANGED','TERM_COUNT_MISMATCH','MODEL_OUTPUT_INVALID','MODEL_REQUEST_FAILED'].includes(error.code))guidedAction(bubble,'重试这条建议',()=>reviseGuidedExpansion(proposal,editor,{value:feedback},turn));
  }
  finally{if(!signal)setBusy(false)}
}
async function confirmGuidedExpansion(proposal,editor,confirmedRefill,confirmedExecution){
  if(state.proposedExpansion!==proposal||state.task)return;
  setBusy(true);
  try{
    let plan=proposal.plan;
    if(editor.value.trim()!==proposal.lines.trim()){
      $('group-lines').value=editor.value.trim();
      const edited=groupPreviewBody(proposal.raw,proposal.count);
      // Editing terms must not silently drop confirmed image targets. A renamed
      // group has no target, which the server rejects rather than guessing one.
      if(plan.groups.some(group=>group.delivery_target!=null)){const targets=new Map(plan.groups.map(group=>[group.primary_label,group.delivery_target]));edited.groups=edited.groups.map(group=>({...group,delivery_target:targets.get(group.primary_label)??null}))}
      const changed=await api('/v1/retrieval/projects/demo/expansions',{method:'POST',body:edited});
      if(!changed.recognized||!changed.plan)throw new Error('修改后的扩词未通过校验');
      plan=changed.plan;
    }
    const refill=confirmedRefill||refillFormValue();
    const body=draftBody(proposal.raw,proposal.count,plan,refill,state.queryImage,$('query-image-exclude')?.checked===true);
    if(confirmedExecution){body.query_spec.retrieval_execution=confirmedExecution;
      if(confirmedExecution.candidate_intake){body.node_modes.retrieval='auto';body.node_modes.cleaning='auto'}
    }
    const resumeAgent=Boolean(proposal.confirmation&&proposal.jobId);
    if(resumeAgent&&(!confirmedExecution||plan.plan_digest!==proposal.confirmation.plan_digest))throw new Error('检索方案或执行设置已变化，请重新生成并确认最新完整方案。');
    if(resumeAgent){
      state.task=await api(expansionJobPath(state.dialogueId)+'/'+proposal.jobId+'/confirm',{method:'POST',keyValue:key('plan-confirm'),body:{...proposal.confirmation,proposal:body}});
      state.conversationId=state.task.conversation_id||null;
    }else{
      const path=state.resumeDraft?'/v1/retrieval/projects/demo/drafts/'+state.resumeDraft.draft_id:'/v1/retrieval/projects/demo/drafts';
      if(state.resumeDraft)body.expected_draft_version=state.resumeDraft.draft_version;
      state.draft=await api(path,{method:'POST',keyValue:key('draft'),body});
      state.conversationId=state.draft.conversation_id;
      state.resumeDraft={draft_id:state.draft.draft_id,draft_version:state.draft.draft_version};
      state.task=await api('/v1/retrieval/tasks',{method:'POST',keyValue:key('task'),body:{draft_id:state.draft.draft_id,expected_draft_version:state.draft.draft_version,proposal_digest:state.draft.proposal_digest,action:'confirm'}});
    }
    if(state.dialogueId){if(!resumeAgent)await api(`/v1/retrieval/projects/demo/dialogue-sessions/${state.dialogueId}/archive`,{method:'POST',body:{task_id:state.task.task_id}}).catch(()=>{});state.dialogueId=null}
    sessionStorage.removeItem('dsh-dialogue');
    localStorage.setItem('dsh-task-id',state.task.task_id);
    await refreshTaskSidebar();
    state.guidedPhase='search';state.proposedExpansion=null;editor.disabled=true;retireDraftActions();
    $('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');
    $('status').textContent='需求已确认，处理进度见右侧。';
    await refreshHistory();
    setBusy(false);
    if(confirmedExecution||state.task.search_enqueue?.backend==='confirmed-terms-agent'||state.task.search_enqueue?.status==='queued')await guidedTick();else await guidedStartSearch();
  }catch(e){
    const explanation=e.code==='FEATURE_NOT_SUPPORTED'?'页面还是旧版本，请刷新后再次确认检索词。词表仍在，检索没有启动。':state.task?'需求已保存；后续状态读取失败，请刷新状态确认检索进度。':'暂时没能开始检索。请刷新页面后再确认一次检索词。';
    $('status').textContent=explanation;message('assistant',state.task?'需求已保存':'需求尚未保存',explanation);
  }
  finally{setBusy(false)}
}
function showCleaningHandoff(cleaning){
  const completed=Boolean(cleaning.runs?.length)&&cleaning.runs.every(run=>run.status==='completed');
  message('assistant',completed?'清洗检测已结束':'候选已进入清洗',completed?'检测回执已生成，可查看各步骤结果；有效数量以正式结果为准。':'可在右侧“清洗”节点查看实际执行状态和各步骤明细。');
}
async function guidedStartSearch(){
  if(!state.task)return;
  if(state.task.retrieval_agent_job||state.task.search_enqueue?.backend==='confirmed-terms-agent'){await guidedTick();return}
  const taskId=state.task.task_id,revision=state.task.confirmed_revision;
  state.searchAttempt={taskId,revision,status:'checking',reason:''};await refreshTaskSidebar();
  try{
    const existing=await api('/v1/retrieval/tasks/'+taskId+'/first-search/progress');
    if(existing.status==='approved'&&existing.revision===revision){await guidedTick();return}
    const data=await api('/v1/retrieval/tasks/'+taskId+'/first-search');
    if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;
    state.firstSearchPreview=data;state.firstSearchChoices=[];
    if(!data.routes.length||data.routes.some(route=>!route.options.length)){
      const reason=searchUnavailableReason(data);state.searchAttempt={taskId,revision,status:'unavailable',reason};await refreshTaskSidebar();message('assistant','检索未启动',reason);$('feedback-box').classList.remove('hidden');return}
    state.firstSearchChoices=data.routes.flatMap(route=>route.options.map(option=>({branch_id:route.branch_id,binding_id:option.binding_id,batch_size:Math.min(2,option.max_batch_size)})));
    if(state.firstSearchChoices.length>64){const reason='可用数据源范围过大，需要缩小到 64 个词/数据源组合后再检索。';state.searchAttempt={taskId,revision,status:'unavailable',reason};await refreshTaskSidebar();message('assistant','需要缩小数据来源范围',reason);$('feedback-box').classList.remove('hidden');return}
    await guidedConfirmAndSearch();
  }catch(e){if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;const reason='当前测试索引不能逐词完成编码或路由：'+e.message+'。已确认的需求仍保留，但没有执行检索。';state.searchAttempt={taskId,revision,status:'unavailable',reason};await refreshTaskSidebar();message('assistant','检索暂不可用',reason);$('feedback-box').classList.remove('hidden')}
}
async function guidedConfirmAndSearch(){
  const preview=state.firstSearchPreview;
  if(!state.task||!preview||state.searchSetupBusy)return;
  if(preview.routes.some(route=>!state.firstSearchChoices.some(choice=>choice.branch_id===route.branch_id))){$('status').textContent='每个检索词至少选择一个索引';return}
  const taskId=state.task.task_id,revision=state.task.confirmed_revision;
  state.searchSetupBusy=true;
  try{
    const proposal=await api('/v1/retrieval/tasks/'+taskId+'/first-search/prepare',{method:'POST',body:{routing_digest:preview.routing_digest,choices:state.firstSearchChoices}});
    if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;
    state.firstSearchProposal=proposal;
    await api('/v1/retrieval/tasks/'+taskId+'/first-search/confirm',{method:'POST',keyValue:key('guided-search-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});
    if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;
    state.task=await api('/v1/retrieval/tasks/'+taskId);
    await refreshTaskSidebar();
    $('status').textContent='检索计划已确认 · 等待执行状态';
    await guidedTick();
  }catch(e){if(state.task?.task_id!==taskId||state.task.confirmed_revision!==revision)return;$('status').textContent=e.message;state.searchAttempt={taskId,revision,status:'unavailable',reason:e.message+'。未返回的词不显示为已有结果。'};await refreshTaskSidebar();message('assistant','检索未完成',e.message+'。未返回的词不显示为已有结果。');$('feedback-box').classList.remove('hidden')}
  finally{state.searchSetupBusy=false}
}
async function guidedTick(){
 clearTimeout(searchPollTimer);const taskId=state.task?.task_id;if(!taskId)return;await refreshTaskSidebar();
 const poll=async()=>{if(state.task?.task_id!==taskId)return;if(!searchPollBusy&&!document.hidden){searchPollBusy=true;try{await refreshTaskSidebar()}finally{searchPollBusy=false}}if(state.task?.task_id===taskId)searchPollTimer=setTimeout(poll,2500)};
 searchPollTimer=setTimeout(poll,2500);
}
async function showCandidateManifestReview(){
  if(!state.task||state.manifestConfirming)return;
  const taskId=state.task.task_id,revision=state.task.confirmed_revision,version=state.task.task_version,generation=state.task.run_generation;
  const incremental=Boolean(state.searchSnapshot?.results?.available);
  const current=()=>state.task?.task_id===taskId&&state.task.confirmed_revision===revision&&state.task.task_version===version&&state.task.run_generation===generation;
  state.manifestConfirming=true;setBusy(true);
  const bubble=message('assistant','','正在确认候选清单…'),line=bubble.querySelector('p');
  const path=`/v1/retrieval/tasks/${taskId}/first-search/manifest`;
  try{
    let manifest=await api(path);if(!current())return;
    if(!manifest.candidate_count){line.textContent='暂无可确认的候选图片。';return}
    if(manifest.confirmation_supported===false){line.textContent=manifest.note||'候选输入仅供审阅，尚不能确认清洗。';return}
    if(!manifest.confirmed&&manifest.kind!=='candidate_input_manifest'){
      line.textContent=`正在保存 ${manifest.candidate_count} 张候选图片…`;
      try{manifest=await api(path,{method:'POST',keyValue:`candidate-manifest:${manifest.proposal_digest}`,body:{proposal_digest:manifest.proposal_digest,expected_task_version:manifest.expected_task_version,action:'confirm'}})}
      catch(error){
        if(error.code!=='NETWORK_UNAVAILABLE')throw error;
        // The POST may have committed before the connection dropped. Read back
        // first; never silently replay a confirmation with an unknown outcome.
        const saved=await api(path).catch(()=>null);
        if(!saved?.confirmed&&saved?.kind!=='candidate_input_manifest')throw error;
        manifest=saved;
      }
    }
    if(!current())return;
    state.candidateManifest=manifest;
    line.textContent=`已确认${incremental?'本轮新增':''} ${manifest.candidate_count} 张候选图片，可以继续准备清洗。`;
    if(state.task?.retrieval_agent_job?.execution_scope?.candidate_intake){
      line.textContent=`已固定 ${manifest.candidate_count} 张候选，后台将按已确认条件自动清洗。`;
    }else offerVlmCleaning(manifest);
    $('status').textContent='';
    void refreshTaskSidebar();
  }catch(error){
    if(!current())return;
    line.textContent=error.code==='NETWORK_UNAVAILABLE'?'连接中断，暂时无法确认是否已保存。重试时会先核对保存状态。':'候选清单未确认：'+(error.detail||error.message);
    guidedAction(bubble,'重新确认',showCandidateManifestReview);
    $('status').textContent='';
  }finally{state.manifestConfirming=false;setBusy(false)}
}
function offerVlmCleaning(manifest){
  card('真实 VLM 清洗',[['输入',`${manifest.candidate_count} 条已固定候选`],['规则','按已确认需求逐图语义判定'],['状态','尚未准备、尚未调用模型']],[['生成清洗确认卡',prepareVlmCleaning,false]])
}
async function prepareVlmCleaning(){
  if(!state.task||!state.candidateManifest)return;const taskId=state.task.task_id;setBusy(true);
  try{const proposal=await api(`/v1/retrieval/tasks/${taskId}/cleaning/prepare`,{method:'POST',body:{manifest_ref:state.candidateManifest.manifest_ref}});if(state.task?.task_id!==taskId)return;state.cleaningProposal=proposal;
    showCleaningApproval(taskId,proposal)
  }catch(error){$('status').textContent=error.code==='CLEANING_NOT_CONFIGURED'?'服务端尚未装配真实 VLM 清洗 worker。':error.message}finally{setBusy(false)}
}
function showCleaningApproval(taskId,proposal){
  const execution=proposal.execution,model=execution?.model,confirmationKey=key('vlm-cleaning-confirm');
  const fields=[['候选数量',proposal.candidate_count],['预计 VLM 检查次数',proposal.estimated_vlm_calls??'未知'],['正式版本',`${proposal.revision} / generation ${proposal.generation}`],['候选清单',proposal.manifest_ref],['清洗模型',model?`${model.provider} / ${model.name}（${model.id}）`:'未提供模型快照，不能在此确认 VLM 执行'],['完整清洗指令',execution?.instruction||'未提供'],['已确认规则',proposal.rules.map(rule=>`${rule.rule_id}：${rule.predicate_ref} ${rule.operator} ${JSON.stringify(rule.value)}`).join('\n')],['步骤算子',(proposal.steps||[]).map(step=>`${step.step_id}：${step.source||'已确认条件'}\n指令：${step.instruction||'未提供'}`).join('\n')],['提示版本',execution?.prompt_version||'未提供'],['模型配置摘要',execution?.config_digest||'未提供'],['配方摘要',proposal.recipe_digest],['发布摘要',proposal.detector_release_digest||'未提供'],['未知与失败','待复核或失败，不计入有效素材'],['说明',proposal.confirmation_note||'确认仅批准此版本和候选清单。'],['图片发送','确认后后台会把授权图片发送给上述冻结的 VLM。']];
  let dismissed=false;const cancel=()=>{dismissed=true;if(state.cleaningProposal===proposal)state.cleaningProposal=null;message('assistant','本张清洗卡已关闭','此卡不再接受确认；如果此前已提交执行，关闭卡片不会撤销后台作业。可重新生成确认卡。')};
  card('VLM 清洗确认卡 · 尚未执行',fields,[['确认并开始 VLM 清洗',()=>{if(dismissed){$('status').textContent='此卡已取消，请重新生成确认卡。';return}return confirmVlmCleaning(proposal,taskId,confirmationKey)},!model||!execution?.instruction],['暂不执行',cancel,false],['修改清洗条件',()=>{cancel();$('feedback-box').classList.remove('hidden');$('feedback-text').value='增加清洗检测：';$('feedback-text').focus();message('assistant','请补充需要修改的条件','修改条件需生成并确认新需求版本；当前卡不会执行。')},false]])
}
async function confirmVlmCleaning(proposal,taskId,confirmationKey){
  if(!state.task||!proposal||state.task.task_id!==taskId||proposal.task_id!==taskId||proposal.expected_task_version!==state.task.task_version||proposal.revision!==state.task.confirmed_revision){$('status').textContent='清洗卡不属于当前任务，请重新打开对应任务。';return}setBusy(true);
  try{const operation=await api(`/v1/retrieval/tasks/${taskId}/cleaning/confirm`,{method:'POST',keyValue:confirmationKey,body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});if(state.task?.task_id!==taskId)return;state.cleaningOperation=operation;message('assistant','VLM 清洗已批准',`后台状态：${operation.status}；结果只接受当前任务版本与候选清单。`);pollVlmCleaning(taskId,operation.operation_id)}catch(error){$('status').textContent=error.message}finally{setBusy(false)}
}
async function pollVlmCleaning(taskId,operationId,attempt=0){
  if(state.task?.task_id!==taskId||state.cleaningOperation?.operation_id!==operationId)return;
  try{const status=await api(`/v1/retrieval/tasks/${taskId}/cleaning/status?operation_id=${encodeURIComponent(operationId)}`);if(state.task?.task_id!==taskId||state.cleaningOperation?.operation_id!==operationId)return;state.cleaningOperation=status;
    if(status.status==='completed'){message('assistant','VLM 清洗完成','三态结果已写入当前任务；可在右侧“清洗”节点查看每一步的保留、淘汰和待复核。');await refreshTaskSidebar();return}
    if(status.status==='failed'){message('assistant','VLM 清洗未完成',`${status.code||'CLEANING_FAILED'}：${status.message||'请检查模型配置后重新确认。'}`);return}
    if(status.status==='retry_wait')$('status').textContent=`清洗等待重试：已尝试 ${status.attempts}/${status.max_attempts} 次；下次不早于 ${new Date(status.next_attempt_at*1000).toLocaleTimeString()}`;
    if(status.progress&&status.status!=='retry_wait')$('status').textContent=`清洗已处理 ${status.progress.completed}/${status.progress.total} 张 · 通过 ${status.progress.passed} · 淘汰 ${status.progress.rejected} · 待复核 ${status.progress.review} · 未完成 ${status.progress.pending}（通过不等于最终入选）`;
    if(status.progress&&status.status==='retry_wait')$('status').textContent+=`；已保存 ${status.progress.completed}/${status.progress.total} 张，恢复时复用已完成判定`;
    if(status.status==='superseded'){$('status').textContent='清洗所属版本已失效，不再重试。';return}
    setTimeout(()=>pollVlmCleaning(taskId,operationId,0),1500)
  }catch(error){if(attempt<120)setTimeout(()=>pollVlmCleaning(taskId,operationId,attempt+1),1500);else $('status').textContent=error.message}
}
async function confirmTask(){if(!state.draft||state.task)return;setBusy(true);try{state.task=await api('/v1/retrieval/tasks',{method:'POST',keyValue:key('task'),body:{draft_id:state.draft.draft_id,expected_draft_version:state.draft.draft_version,proposal_digest:state.draft.proposal_digest,action:'confirm'}});retireDraftActions();localStorage.setItem('dsh-task-id',state.task.task_id);await refreshTaskSidebar();message('system','提单成功',`任务 ${state.task.task_id}\n正式版本 ${state.task.confirmed_revision} · 状态 ${state.task.run_status}`);$('feedback-box').classList.remove('hidden');await refreshHistory()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function prepareFeedback(){
  const text=$('feedback-text').value.trim();if(!text){$('status').textContent='请输入消息';return}
  await routeTaskFollowup(text);
}
function taskTurnStorageKey(taskId){return `dsh-task-command:${headers['X-Tenant-Id']}:${headers['X-User-Id']}:${taskId}`}
function pendingTaskTurn(taskId){
  if(state.pendingTaskTurn?.taskId===taskId)return state.pendingTaskTurn;
  try{const saved=JSON.parse(sessionStorage.getItem(taskTurnStorageKey(taskId))||'null');if(saved?.taskId===taskId&&typeof saved.key==='string'&&(typeof saved.body?.text==='string'||typeof saved.body?.message==='string')){state.pendingTaskTurn=saved;return saved}}catch{}
  return null;
}
function saveTaskTurn(pending){state.pendingTaskTurn=pending;try{sessionStorage.setItem(taskTurnStorageKey(pending.taskId),JSON.stringify(pending))}catch{}}
function clearTaskTurn(taskId,commandKey){
  if(state.pendingTaskTurn?.taskId===taskId&&state.pendingTaskTurn.key===commandKey)state.pendingTaskTurn=null;
  try{const saved=JSON.parse(sessionStorage.getItem(taskTurnStorageKey(taskId))||'null');if(saved?.key===commandKey)sessionStorage.removeItem(taskTurnStorageKey(taskId))}catch{}
}
function taskTurnPath(taskId,commandKey){return `/v1/retrieval/tasks/${encodeURIComponent(taskId)}/conversation-turns/${encodeURIComponent(commandKey)}`}
async function awaitTaskCommand(value,taskId,commandKey){
  if(value.schema_version!=='task-conversation-command@1')return value;
  const replayed=value.command_replayed===true;
  const deadline=Date.now()+60000;
  while(value.status!=='completed'){
    if(['failed','interrupted'].includes(value.status)){
      const error=new Error(value.error?.message||'后台处理因服务重启中断，可以重试。');
      error.code=value.error?.code||'COMMAND_INTERRUPTED';error.detail=error.message;throw error;
    }
    if(Date.now()>=deadline){const error=new Error('后台仍在处理，稍后可查询结果。');error.code='COMMAND_RUNNING';error.detail=error.message;throw error}
    await new Promise(resolve=>setTimeout(resolve,700));
    value=await api(taskTurnPath(taskId,commandKey));
  }
  return replayed?{...value.result,command_replayed:true}:value.result;
}
async function sendTaskTurn(taskId,body,selection,retryFailed=false,onAccepted=null){
  let pending=pendingTaskTurn(taskId);
  if(pending&&pending.body.text!==body.text){const error=new Error('上一条消息的处理结果尚未核对，请先恢复该消息。');error.code='COMMAND_UNRESOLVED';throw error}
  if(!pending){pending={taskId,key:key('task-conversation'),body,selection};saveTaskTurn(pending)}
  try{
    const started=performance.now();
    const accepted=await api(`/v1/retrieval/tasks/${taskId}/conversation-turn`,{method:'POST',keyValue:pending.key,body:pending.body,retryFailed,asyncCommand:true});
    if(typeof recordChatTiming==='function')recordChatTiming('task_acceptance',performance.now()-started);
    if(accepted.schema_version==='task-conversation-command@1')onAccepted?.();
    const result=await awaitTaskCommand(accepted,taskId,pending.key);
    clearTaskTurn(taskId,pending.key);return result;
  }catch(error){
    if(['NETWORK_UNAVAILABLE','COMMAND_RUNNING'].includes(error.code)){
      try{const receipt=await api(taskTurnPath(taskId,pending.key));if(receipt.status==='completed'){clearTaskTurn(taskId,pending.key);return {...receipt.result,recovered:true}}}catch{}
    }
    if(['INVALID_SPEC','REVISION_CONFLICT','DRAFT_VERSION_CONFLICT','NOT_FOUND','ACCESS_DENIED','IDEMPOTENCY_KEY_REQUIRED','MODEL_NOT_CONFIGURED','PENDING_DRAFT_EXISTS'].includes(error.code))clearTaskTurn(taskId,pending.key);
    throw error;
  }
}
async function recoverTaskTurn(taskId=state.task?.task_id,retryFailed=false){
  const pending=pendingTaskTurn(taskId);if(!pending||state.task?.task_id!==taskId||state.followupBusy)return;
  const token=state.historyOpenToken;setFollowupBusy(true);
  const current=()=>state.task?.task_id===taskId&&state.historyOpenToken===token;
  try{
    const receipt=await api(taskTurnPath(taskId,pending.key));if(!current())return;
    if(receipt.status==='completed'){
      clearTaskTurn(taskId,pending.key);state.commandRecoveryBubble?.remove?.();state.commandRecoveryBubble=null;
      await applyTaskTurnResult(receipt.result,taskId,pending.body.expected_task_version,pending.body.text,pending.selection,true);return;
    }
    if(retryFailed||receipt.status==='prepared'){
      const result=await sendTaskTurn(taskId,pending.body,pending.selection,true);if(!current())return;
      state.commandRecoveryBubble?.remove?.();state.commandRecoveryBubble=null;
      await applyTaskTurnResult(result,taskId,pending.body.expected_task_version,pending.body.text,pending.selection,true);return;
    }
    state.commandRecoveryBubble?.remove?.();
    const running=receipt.status==='understanding';
    const bubble=message('assistant','',running?'上一条消息仍在后台处理，可以查询结果。':receipt.status==='interrupted'?'上一条消息因服务重启中断，可以重试。':(receipt.error?.message||'上一条消息未处理完成，可以重试。'));
    guidedAction(bubble,running?'查询处理结果':'重试这条消息',()=>recoverTaskTurn(taskId,!running));state.commandRecoveryBubble=bubble;
  }catch(error){if(current()){
    if(error.code==='NOT_FOUND'){
      const bubble=message('assistant','','服务尚未收到上一条消息，可以重新发送。');guidedAction(bubble,'重新发送',async()=>{try{const result=await sendTaskTurn(taskId,pending.body,pending.selection);if(current())await applyTaskTurnResult(result,taskId,pending.body.expected_task_version,pending.body.text,pending.selection,true)}catch(e){$('status').textContent=e.detail||e.message}});
    }else $('status').textContent='暂时无法核对消息结果：'+(error.detail||error.message);
  }}finally{if(current())setFollowupBusy(false)}
}
async function applyTaskTurnResult(intent,taskId,taskVersion,text,selection,recovered=false){
    recovered=recovered||intent.command_replayed===true;
    if(state.task?.task_id!==taskId)return;
    if(state.task.task_version!==taskVersion||intent.is_current===false){message('assistant','','任务或草稿已有更新，这条消息的旧结果已保留，请查看当前方案。');return}
    if(!recovered&&intent.timings?.scope==='original_processing'&&typeof recordChatTiming==='function'){
      recordChatTiming('server_processing',intent.timings.total_ms);
      if(!intent.command_replayed)recordPipelineTiming({stage:'conversation_processing_original',trace_id:intent.timings.trace_id,ms:intent.timings.total_ms});
      if(Number.isFinite(intent.timings.queue_wait_ms))recordChatTiming('server_queue_wait',intent.timings.queue_wait_ms);
      for(const [stage,value] of Object.entries(intent.timings.stages||{}))recordChatTiming('server_'+stage,value.ms);
    }
    if(intent.status_requested){
      let workflow=intent.workflow;
      if(!recovered){const refreshed=await refreshTaskSidebar(intent.workspace);if(state.task?.task_id!==taskId)return;workflow=refreshed||workflow}
      const reply=(intent.status_topic==='slow'?workflow?.slow_reply:workflow?.status_reply)||intent.reply;
      const bubble=message('assistant',recovered?'已恢复的回复（当时状态）':'',reply);
      if(!recovered)for(const action of workflow?.available_actions||[])addWorkflowAction(bubble,action,taskId,workflow.binding);
      return;
    }
    if(state.followupContext?.taskId!==taskId||state.followupContext?.version!==taskVersion)state.followupContext={taskId,version:taskVersion,messages:[]};
    state.followupContext.messages.push({role:'user',text},...(intent.reply?[{role:'assistant',text:intent.reply}]:[]));
    state.followupContext.messages=state.followupContext.messages.slice(-20);
    if(intent.feedback_draft&&intent.cleaning_review){
      state.expansionPreviewPlan=null;state.expansionPreviewToken=null;
      state.feedback=intent.feedback_draft;state.cleaningFeedbackDraftId=state.feedback.draft_id;
      showFeedbackReview(intent.cleaning_review);return;
    }
    if(intent.kind==='new_request'){
      const bubble=message('assistant','这是新的检索需求','需要新建需求单；当前需求会继续在后台检索，不会被这条消息改动。');
      guidedAction(bubble,'新建 Chat 处理',async()=>{
        const refersBack=/一样|相同|沿用|同样|按(?:之前|刚才|上一)|其他不变|条件不变/.test(text);
        let reference_request=null;
        if(refersBack){try{const revisions=await api(`/v1/retrieval/tasks/${taskId}/revisions`);reference_request=revisions.items.find(item=>item.revision===state.task.confirmed_revision)?.proposal?.query_spec?.raw_request||null}catch(error){$('status').textContent='读取上一需求失败：'+(error.detail||error.message);return}}
        sessionStorage.setItem('dsh-new-request-prefill',text);
        sessionStorage.setItem('dsh-new-request-context',JSON.stringify({text,reference_request}));startNewChat();
      });
      return;
    }
    if(intent.kind==='expansion'){await showPostTaskExpansion(text,intent,null,selection);return}
    if(intent.kind==='answer'||intent.kind==='clarify'){message('assistant',recovered?'已恢复的回复（当时状态）':'',intent.reply||'你希望调整哪些内容？');return}
    throw new Error('服务端未返回可审阅的修改，请重新描述。');
}
function addWorkflowAction(bubble,action,taskId,binding){
  const openNode=node=>{const card=document.querySelector(`[data-node="${node}"]`);if(!card)return;if(card.tagName==='DETAILS')card.open=true;card.querySelector('button')?.click();card.scrollIntoView?.({block:'nearest'})};
  const handlers={
    confirm_candidates:showCandidateManifestReview,prepare_cleaning:prepareVlmCleaning,
    start_retrieval:guidedStartSearch,view_retrieval:openCandidateBrowser,
    view_cleaning:()=>openNode('cleaning'),view_refill:()=>openNode('refill'),
    view_delivery:()=>openDelivery(taskId),refresh:refreshTaskSidebar,
    review_change:async()=>{const current=await api(`/v1/retrieval/tasks/${taskId}`);if(state.task?.task_id!==taskId||!current.pending_draft)return;state.feedback=current.pending_draft;await showCleaningSuggestion()}
  };
  if(!Object.hasOwn(handlers,action.id))return;
  guidedAction(bubble,action.label,()=>{if(state.task?.task_id!==taskId)return;if(binding&&(state.task.task_version!==binding.task_version||state.task.confirmed_revision!==binding.revision||state.task.run_generation!==binding.generation)){$('status').textContent='状态已更新，请查询当前下一步。';return}return handlers[action.id]()});
}
async function routeTaskFollowup(text){
  if(!state.task||state.followupBusy)return;
  if(state.task.agent_retrieval){
    state.agentFollowupBubble?.remove?.();
    const bubble=message('assistant','','这段对话对应之前的图片请求。要按这条消息另找一批图片吗？');
    guidedAction(bubble,'另开对话查找',()=>{sessionStorage.removeItem('dsh-new-request-context');sessionStorage.setItem('dsh-new-request-prefill',text);startNewChat()});
    state.agentFollowupBubble=bubble;return;
  }
  const pending=pendingTaskTurn(state.task.task_id);
  if(pending&&pending.body.text!==text){$('status').textContent='先核对上一条消息的结果；新输入已保留。';await recoverTaskTurn(state.task.task_id);return}
  if(state.dialogueModelBinding){$('status').textContent='正在保存对话模型，请稍候。';return}
  const llm=$('llm-model').value;
  const dialogueLlm=$('dialogue-llm-model')?.value||llm;
  const taskId=state.task.task_id,taskVersion=state.task.task_version;
  const requestToken={};state.followupRequestToken=requestToken;const openToken=state.historyOpenToken;
  const sameScreen=()=>state.followupRequestToken===requestToken&&state.historyOpenToken===openToken&&state.task?.task_id===taskId;
  const current=()=>sameScreen()&&state.task.task_version===taskVersion;
  const selection={llm,vlm:$('vlm-model').value};setFollowupBusy(true);
  message('user','',text);
  if($('feedback-text').value.trim()===text)$('feedback-text').value='';
  const waiting=message('assistant','','正在理解你的消息…');
  waiting?.setAttribute?.('role','status');
  const started=performance.now();let understood=false;
  try{
    const preview=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===taskVersion?state.expansionPreviewPlan:null;
    if(state.followupContext?.taskId!==taskId||state.followupContext?.version!==taskVersion)state.followupContext={taskId,version:taskVersion,messages:[]};
    const feedback=state.feedback;
    const intent=await sendTaskTurn(taskId,{text,llm_id:dialogueLlm,expected_revision:state.task.confirmed_revision,expected_task_version:taskVersion,recent_messages:state.followupContext.messages.slice(-20),...(feedback?{feedback_ref:{draft_id:feedback.draft_id,draft_version:feedback.draft_version,proposal_digest:feedback.proposal_digest}}:{}),...(preview?{draft_preview:{groups:preview.plan.groups.map(group=>({primary_label:group.primary_label,auxiliary_terms:group.auxiliary_terms})),target_count:preview.count}}:{})},selection,false,()=>{const line=waiting?.querySelector?.('p');if(line)line.textContent='消息已接收，正在后台处理…'});
    understood=true;if(typeof recordChatTiming==='function')recordChatTiming('followup_understanding',performance.now()-started);
    const waitLine=waiting?.querySelector?.('p');if(waitLine)waitLine.textContent=intent.kind==='expansion'?'正在提交扩词…':'正在整理清洗条件…';
    if(!current()){if(sameScreen()){$('status').textContent='任务版本已更新，请重新发送这条消息。';if(!$('feedback-text').value.trim())$('feedback-text').value=text}return}
    await applyTaskTurnResult(intent,taskId,taskVersion,text,selection,intent.recovered===true);
  }catch(e){if(!understood&&typeof recordChatTiming==='function')recordChatTiming('followup_understanding_failed',performance.now()-started);if(sameScreen()){const detail=e.detail||e.message,pending=Boolean(pendingTaskTurn(taskId));$('status').textContent=(pending?'消息结果待核对：':'这条消息未处理：')+detail;const bubble=message('assistant',pending?'消息结果待核对':'这条消息未处理',detail);if(pending)guidedAction(bubble,'核对处理结果',()=>recoverTaskTurn(taskId));if(!$('feedback-text').value.trim())$('feedback-text').value=text}}
  finally{waiting?.remove?.();if(state.followupRequestToken===requestToken){state.followupRequestToken=null;setFollowupBusy(false)}}
}
function taskExpansionPath(taskId){return '/v1/retrieval/tasks/'+encodeURIComponent(taskId)+'/retrieval-agent/terms'}
function clearTaskExpansionPolling(){if(state.taskExpansionPollTimer)clearTimeout(state.taskExpansionPollTimer);state.taskExpansionPollTimer=null;state.taskExpansionJob=null;state.taskExpansionBubble=null;clearCoordinatorPolling()}
function clearCoordinatorPolling(){if(state.coordinatorPollTimer)clearTimeout(state.coordinatorPollTimer);state.coordinatorPollTimer=null;state.coordinatorBusy=false;state.coordinatorJob=null;state.coordinatorBubble=null}
function watchTaskExpansionJob(job){
  if(state.task?.task_id!==job.task_id||state.task.task_version!==job.task_version||state.task.confirmed_revision!==job.revision)return;
  if(state.taskExpansionPollTimer)clearTimeout(state.taskExpansionPollTimer);
  if(state.taskExpansionJob?.job_id!==job.job_id){state.taskExpansionBubble?.remove?.();state.taskExpansionBubble=null}
  state.taskExpansionJob=job;
  if(job.status==='completed'&&state.expansionPreviewPlan?.jobId===job.job_id)return;
  if(!state.taskExpansionBubble)state.taskExpansionBubble=message('assistant','重新扩词','');
  const status=state.taskExpansionBubble.querySelector('p');
  if(job.status==='completed'&&job.result?.plan){
    if(state.expansionPreviewPlan?.jobId===job.job_id)return;
    if(!job.result.plan.groups.length){status.textContent='扩词结果为空，请重试。';return}
    const taskId=job.task_id,baseVersion=job.task_version,count=job.target_count,plan=job.result.plan;
    const feedbackText=job.confirmation_feedback_text||job.feedback_text;
    const lines=plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
    if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();
    const token=key('expansion-preview');state.expansionPreviewToken=token;
    state.expansionPreviewPlan={taskId,baseVersion,plan,count,requestedTerms:job.requested_terms_per_group,feedbackText,jobId:job.job_id,sourceGroups:job.source_groups};
    const changes=job.changes;
    const summary=changes?`目标 ${changes.target_before===count?count:changes.target_before+' → '+count} 张 · 新增 ${changes.added_terms} 个词 · 移除 ${changes.removed_terms} 个词`+
      ((changes.added_groups||changes.removed_groups)?`\n分组：新增 ${changes.added_groups||0} · 移除 ${changes.removed_groups||0}`:''):'目标 '+count+' 张图';
    const bubble=message('assistant',job.result.note?.includes('按用户逐字指定')?'指定词表待确认':'待确认修改',summary);state.expansionPreviewBubble=bubble;
    const quotas=plan.groups.filter(group=>group.delivery_target!=null).map(group=>group.primary_label+' '+group.delivery_target+' 张').join('、');
    reviewDetails(bubble,'查看完整词表',lines+(quotas?'\n各组目标：'+quotas:''));
    state.taskExpansionBubble?.remove?.();state.taskExpansionBubble=null;
    guidedAction(bubble,'确认新版词表',async()=>{
      if(state.expansionPreviewToken!==token||state.task?.task_id!==taskId||state.task.task_version!==baseVersion)return;
      setBusy(true);
      try{
        const pending=state.feedback;
        state.feedback=await api(`/v1/retrieval/tasks/${taskId}/feedback${pending?'/'+pending.draft_id:''}`,{method:'POST',keyValue:key('expansion-feedback'),body:{text:feedbackText,scope:'all',expected_revision:job.revision,expected_task_version:baseVersion,...(pending?{expected_draft_version:pending.draft_version}:{}),target_count:count,grouped_expansion:plan}});
        await decideFeedback('confirm',true);
      }catch(error){$('status').textContent='新版词表尚未确认：'+error.message}
      finally{setBusy(false)}
    });
    guidedAction(bubble,'继续调整',()=>{$('feedback-text').focus()},true);
    return;
  }
  if(job.status==='failed'){
    status.textContent=expansionFailureMessage(job);
    state.taskExpansionBubble.querySelector('.actions')?.remove();
    if(job.error?.code==='EXPANSION_UNCHANGED'){
      guidedAction(state.taskExpansionBubble,'重新提交这条修改',()=>{$('feedback-text').value=(job.confirmation_feedback_text||job.feedback_text||'').split('后续调整：').at(-1).trim();$('feedback-text').focus()});
    }else{
      guidedAction(state.taskExpansionBubble,'重试',async()=>{try{watchTaskExpansionJob(await api(taskExpansionPath(job.task_id)+'/'+job.job_id+'/retry',{method:'POST',body:{}}))}catch(error){$('status').textContent=expansionFailureMessage({task_id:job.task_id,error})}});
    }
    return;
  }
  const first=job.preview_groups?.slice(0,2).map(group=>`${group.primary_label}：${group.auxiliary_terms.slice(0,3).join('、')}`)||[];
  status.textContent=expansionProgress(job)+(first.length?'\n首批预览（生成中，暂不可确认）：'+first.join('；'):'');
  state.taskExpansionBubble.querySelector('.actions')?.remove();
  state.taskExpansionPollTimer=setTimeout(async()=>{try{watchTaskExpansionJob(await api(taskExpansionPath(job.task_id)+'/'+job.job_id))}catch(error){if(state.task?.task_id===job.task_id){status.textContent='扩词进度暂时读取失败：'+(error.detail||error.message);state.taskExpansionPollTimer=setTimeout(()=>watchTaskExpansionJob(job),4000)}}},2000);
}
async function restoreTaskExpansionJob(taskId){try{const job=await api(taskExpansionPath(taskId));if(state.task?.task_id===taskId)watchTaskExpansionJob(job)}catch(error){if(error.code!=='EXPANSION_JOB_NOT_FOUND'&&state.task?.task_id===taskId)$('status').textContent='重新扩词状态读取失败：'+(error.detail||error.message)}}
async function showPostTaskExpansion(text,intent,displayRole='user',selection){
  if(!state.task)return;
  const taskId=state.task.task_id,baseVersion=state.task.task_version,llm=selection?.llm??$('llm-model').value,vlm=selection?.vlm??$('vlm-model').value;
  if(!llm||!vlm){$('status').textContent='重新扩词需要已配置的 LLM 和 VLM 模型选择';return}
  const ownsBusy=!state.followupBusy;if(ownsBusy)setFollowupBusy(true);
  try{
    if(displayRole)message(displayRole,'',text);
    const revisions=await api(`/v1/retrieval/tasks/${taskId}/revisions`);
    const revision=revisions.items.find(item=>item.revision===state.task.confirmed_revision),spec=revision?.proposal?.query_spec;
    if(!spec)throw new Error('当前需求版本不可读');
    let previous=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===baseVersion?state.expansionPreviewPlan:null;
    if(!previous&&state.feedback?.feedback_text&&state.feedback.feedback_text!==text){
      const earlier=await api(`/v1/retrieval/tasks/${taskId}/followup-intent`,{method:'POST',body:{text:state.feedback.feedback_text,llm_id:$('dialogue-llm-model')?.value||llm,expected_revision:state.task.confirmed_revision,expected_task_version:baseVersion}});
      if(earlier.kind==='expansion')previous={count:earlier.target_count,requestedTerms:earlier.requested_terms_per_group,feedbackText:state.feedback.feedback_text};
    }
    const count=intent.target_count||previous?.count||spec.target_count;
    let source=previous?.plan?.groups||spec.grouped_expansion?.groups||[];
    const countEdits=(intent.count_edits||[]).filter(edit=>edit.target==='terms').flatMap(edit=>edit.labels?.length?[edit]:source.length?source.map(group=>({...edit,labels:[group.primary_label]})):[edit]);
    const requestedTerms=countEdits.length?null:intent.requested_terms_per_group||previous?.requestedTerms||null;
    if(intent.term_count_only===true&&requestedTerms){
      const candidates=[previous?.sourceGroups,spec.grouped_expansion?.groups];
      const usable=groups=>Array.isArray(groups)&&groups.length===source.length&&groups.every((group,index)=>group.primary_label===source[index].primary_label&&group.auxiliary_terms.length>requestedTerms);
      if(!usable(source)){const fallback=candidates.find(usable);if(fallback)source=fallback;else intent={...intent,term_count_only:false}}
    }
    const feedbackText=text;
    const current=source.length?source.map(group=>({primary_label:group.primary_label,auxiliary_terms:group.auxiliary_terms})):null;
    const job=await api(taskExpansionPath(taskId),{method:'POST',body:{expected_task_version:baseVersion,expected_revision:state.task.confirmed_revision,raw_request:spec.raw_request,dimension_id:revision.proposal.dimension_id,target_count:count,current_target_count:previous?.count||spec.target_count,model_selection:{llm,vlm},feedback_text:intent.expansion_instruction||feedbackText,confirmation_feedback_text:feedbackText,...(current?{current_groups:current}:{}),...(requestedTerms?{requested_terms_per_group:requestedTerms}:{count_edits:countEdits}),...(intent.term_count_only===true?{term_count_only:true}:{})}});
    if(state.task?.task_id!==taskId||state.task.task_version!==baseVersion)return;
    state.expansionPreviewToken=null;
    state.expansionPreviewBubble?.querySelector('.actions')?.remove();
    watchTaskExpansionJob(job);
    if($('feedback-text').value.trim()===text)$('feedback-text').value='';
  }catch(error){if(state.task?.task_id===taskId&&state.task.task_version===baseVersion){const detail=error.detail||error.message;$('status').textContent='重新扩词未完成：'+detail;message('assistant','重新扩词未完成',detail+'。原正式版本未改变。');if(!$('feedback-text').value.trim())$('feedback-text').value=text}}
  finally{if(ownsBusy)setFollowupBusy(false)}
}
async function startPlatformCoordination(){
 if(!state.task||state.coordinatorBusy)return;
 const taskId=state.task.task_id,version=state.task.task_version,epoch=state.historyOpenToken;
 const current=()=>state.task?.task_id===taskId&&state.historyOpenToken===epoch;
 const llm=$('llm-model').value;if(!llm){$('status').textContent='请先选择已配置的模型';return}
 state.coordinatorBusy=true;
 if(state.coordinatorBubble?.isConnected)state.coordinatorBubble.remove();
 const bubble=message('assistant','安排下一步','正在读取当前任务并安排下一步…');state.coordinatorBubble=bubble;
 const requestKey=key('coordinate');
 const body={capability:'coordination',workflow_ref:'platform-coordinator@1',planning:{message:'请根据当前真实进度安排下一步；需要人工确认时留在原确认入口，不重复启动正在运行或未知的作业。',model_profile_ref:llm,expected_revision:state.task.confirmed_revision,expected_generation:state.task.run_generation,expected_task_version:version}};
 const root=`/v1/retrieval/tasks/${taskId}/agent-jobs`;
 let prepared=null;
 const watch=async job=>{
  if(!current())return;
  state.coordinatorJob=job;
  const line=bubble.querySelector('p');
  if(job.status==='completed'){
   const steps=job.coordination?.steps||[];
   line.textContent=steps.some(step=>step.result?.status==='waiting_confirmation')?'已核对进度，下一步需要你在原节点确认。':
    steps.some(step=>['queued','running'].includes(step.result?.status))?'已安排检索作业，可在原检索节点查看进度。':'已核对当前任务；没有重复启动作业。';
   if(job.coordination?.suggestion)reviewDetails(bubble,'查看安排说明',job.coordination.suggestion);
   state.coordinatorBusy=false;await refreshTaskSidebar();return;
  }
  if(['unknown','failed','cancelled'].includes(job.status)){
   line.textContent=job.status==='cancelled'?'任务版本已变化，请按当前版本重新安排。':'本次安排需要核对，未自动重复派发。';
   state.coordinatorBusy=false;return;
  }
  line.textContent='正在安排下一步，原检索和清洗确认方式不变。';
  state.coordinatorPollTimer=setTimeout(async()=>{
   try{await watch(await api(root+'/'+job.job_ref))}catch(error){if(current()){line.textContent='安排状态暂时读取失败：'+(error.detail||error.message);state.coordinatorBusy=false}}
  },2000);
 };
 const submit=async()=>{
  prepared=await api(root+'/prepare',{method:'POST',keyValue:requestKey,body});
  if(!current())return;
  if(prepared.status==='ready')prepared=await api(root+'/'+prepared.job_ref+'/start',{method:'POST',keyValue:requestKey+'-start',body:{proposal_digest:prepared.proposal_digest,expected_task_version:version}});
  if(current())await watch(prepared);
 };
 try{
  const previous=await api(root);if(!current())return;
  const active=(previous.items||[]).find(job=>job.capability==='coordination'&&job.revision===body.planning.expected_revision&&job.generation===body.planning.expected_generation&&(job.source_task_version??job.task_version)===version&&['queued','running','dispatching','unknown'].includes(job.status));
  if(active)await watch(active);else await submit();
 }catch(error){if(current()){
  bubble.querySelector('p').textContent='本次安排尚未核对完成：'+(error.detail||error.message);
  guidedAction(bubble,'核对本次安排',async()=>{try{await submit()}catch(next){if(current())$('status').textContent='安排仍待核对：'+(next.detail||next.message)}});
 }if(current())state.coordinatorBusy=false}
}
async function showRecallAnalysis(){
 if(!state.task)return;
 const taskId=state.task.task_id,version=state.task.task_version;
 const current=()=>state.task?.task_id===taskId&&state.task.task_version===version;
 if(state.recallAnalysisBubble?.isConnected)state.recallAnalysisBubble.remove();
 const bubble=message('assistant','本次召回','正在读取实际检索记录…');state.recallAnalysisBubble=bubble;
 try{
  const report=await api(`/v1/retrieval/tasks/${taskId}/retrieval-agent/recall`);
  if(!current()||report.scope.task_version!==version){bubble.remove();return}
  const totals=report.totals;
  bubble.querySelector('p').textContent=`${totals.raw_hits} 条命中，${totals.unique_candidates} 个唯一候选，${totals.repeated_hits} 次重复覆盖；其中 ${totals.assignable_candidates} 个符合本次候选清单的输入范围。`+
    (report.complete?'':'当前记录尚未完整，不能据此判断零收益。')+'\n'+report.suggestions.map(row=>row.reason).join('\n')+'\n这些候选尚未按本次统计判定为清洗通过。';
  reviewDetails(bubble,'查看各词召回',report.branches.map(row=>row.query_text+'：'+
    (row.sources.every(source=>source.committed_pages===0)?'尚无已提交页面':`${row.raw_hits} 条命中 · 新增 ${row.new_unique_candidates} 个候选 · 重复 ${row.repeated_hits} 次 · 未映射 ${row.unresolved_hits} 条`)).join('\n'));
  if(report.can_refine)guidedAction(bubble,'根据召回调整词表',async()=>{
   if(!current())return;
   const llm=$('llm-model').value,vlm=$('vlm-model').value;
   if(!llm||!vlm){$('status').textContent='请先选择已配置的模型';return}
   if(bubble.dataset.refining==='true')return;bubble.dataset.refining='true';
   setFollowupBusy(true);
   try{
    const binding=await api(`/v1/retrieval/tasks/${taskId}/read-binding`);
    if(!current())return;
    const job=await api(taskExpansionPath(taskId),{method:'POST',body:{
      expected_task_version:version,expected_revision:report.scope.revision,
      raw_request:binding.proposal.query_spec.raw_request,target_count:report.target_count,dimension_id:binding.proposal.dimension_id,
      model_selection:{llm,vlm},recall_evidence_digest:report.evidence_digest}});
    if(current())watchTaskExpansionJob(job);
   }catch(error){if(current())$('status').textContent='调整词表未启动：'+(error.detail||error.message)}
   finally{bubble.dataset.refining='false';setFollowupBusy(false)}
  });
 }catch(error){if(current())bubble.querySelector('p').textContent='召回分析暂不可用：'+(error.detail||error.message);else bubble.remove()}
}
async function decideFeedback(action,expansion=false){if(!state.task||!state.feedback)return;const taskId=state.task.task_id,feedbackId=state.feedback.draft_id,feedbackVersion=state.feedback.draft_version;setBusy(true);try{const result=await api(`/v1/retrieval/tasks/${state.task.task_id}/confirmations`,{method:'POST',keyValue:key(action),body:{draft_id:state.feedback.draft_id,expected_draft_version:state.feedback.draft_version,proposal_digest:state.feedback.proposal_digest,expected_task_version:state.feedback.base_task_version,action}});if(state.task?.task_id!==taskId||state.feedback?.draft_id!==feedbackId||state.feedback?.draft_version!==feedbackVersion)return;state.task=result.task;state.feedback=null;if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.querySelector('.actions')?.remove();if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.querySelector('.actions')?.remove();if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();state.expansionPreviewToken=null;state.expansionPreviewPlan=null;await refreshTaskSidebar();message('assistant',action==='confirm'?(expansion?'新版词表已确认':'反馈已确认'):'已忽略这条反馈',action==='confirm'?(expansion?'已保存为同一需求 ID 的新版本；正在核对全部检索词的索引和编码能力，通过后才会启动检索。':'需求已更新为新版本；当前尚未创建或执行清洗管线。'):'原需求保持不变。');$('feedback-text').value='';$('feedback-box').classList.remove('hidden');await refreshHistory();setBusy(false);if(action==='confirm'&&(expansion||result.search_enqueue)){if(result.search_enqueue?.status==='queued')await guidedTick();else await guidedStartSearch()}else if(action==='confirm'){state.candidateManifest=null;state.cleaningProposal=null;await prepareUpdatedCleaning()}}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function prepareUpdatedCleaning(){
  const taskId=state.task?.task_id;if(!taskId)return;
  try{const proposal=await api(`/v1/retrieval/tasks/${taskId}/cleaning/prepare`,{method:'POST',body:{incremental_recompute:true}});
    if(state.task?.task_id!==taskId||proposal.expected_task_version!==state.task.task_version)return;
    state.cleaningProposal=proposal;showCleaningApproval(taskId,proposal);
  }catch(error){message('assistant','清洗条件已保存',error.code==='RECOMPUTE_SOURCE_UNAVAILABLE'?'需要先完成检索并确认候选清单，再准备清洗方案。':'新清洗方案暂未生成：'+(error.detail||error.message));}
}
async function editFeedback(){
  const text=state.feedback?.feedback_text||'';
  await decideFeedback('reject');
  if(state.feedback)return;
  $('feedback-text').value=text;$('feedback-text').focus();
}
function showFeedbackReview(suggestion){
  if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.remove();
  if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();
  $('feedback-box').classList.remove('hidden');
  const sentence=text=>{const copy=text.trim().replace(/^请用户/, '请你').replace(/^建议用户/, '建议你');return /[。！？!?]$/.test(copy)?copy:copy+'。'};
  let bubble;
  if(!suggestion){
    bubble=message('assistant','请审阅这条反馈','模型暂时无法判断这条反馈是否形成清洗条件。你可以重新描述，或手动核对完整需求后确认；目前没有自动执行。');
    guidedAction(bubble,'查看完整需求',()=>{if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();state.feedbackManualCard=card('完整反馈草稿 · 手动确认',[['原反馈',state.feedback.feedback_text],['完整需求',state.feedback.proposal.query_spec.raw_request],['执行说明',state.feedback.execution_note]],[['仅保存反馈文本',()=>decideFeedback('confirm'),false],['不采用',()=>decideFeedback('reject'),true]])});
  }else if(suggestion.feedback_status==='ready'){
    const plan=suggestion.cleaning_plan_preview;
    const diff=(plan?.changes||[]).map(change=>change.op==='add'?`新增：${change.after}`:change.op==='remove'?`删除：${change.before}`:`替换：${change.before} → ${change.after}`).join('\n');
    const quantity=plan?.target&&plan.target.before!==plan.target.after?`总目标：${plan.target.before} → ${plan.target.after} 张\n`:'';
    const retrieval=plan?.retrieval?.changed?'检索主体：'+(plan.retrieval.before||[]).map(group=>group.primary_label).join('、')+' → '+plan.retrieval.after.map(group=>group.primary_label).join('、')+'\n词表已更新\n':'';
    const requirementChanged=Boolean(quantity||retrieval);
    bubble=message('assistant',requirementChanged?'需求修改待确认':'清洗方案待确认',plan?quantity+retrieval+(diff?diff+'\n\n':'')+`检测节点 ${plan.before_count} → ${plan.after_count}：\n`+plan.nodes.map((node,index)=>`${index+1}. ${node.source}`).join('\n'):'新增条件：'+(state.feedback?.feedback_text||suggestion.requirement));
    if(retrieval)reviewDetails(bubble,'查看完整检索词',plan.retrieval.after.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('、')).join('\n'));
    guidedAction(bubble,requirementChanged?'确认更新需求':'确认更新清洗条件',()=>decideFeedback('confirm'));
  }else{
    const unrelated=suggestion.feedback_status==='unrelated';
    bubble=message('assistant',unrelated?'这条反馈还不能用于清洗':'请再补充一点',sentence(suggestion.rationale)+'当前需求不会改变。');
  }
  guidedAction(bubble,'重新描述',editFeedback,true);
  guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);
  state.feedbackReviewBubble=bubble;
}
async function showCleaningSuggestion(selection){
  const taskId=state.task?.task_id,draft=state.feedback;if(!taskId||!draft)return;
  const current=()=>state.task?.task_id===taskId&&state.feedback?.draft_id===draft.draft_id&&state.feedback?.draft_version===draft.draft_version;
  const llm=$('dialogue-llm-model')?.value||selection?.llm||$('llm-model').value,vlm=selection?.vlm??$('vlm-model').value;
  if(!draft.proposal?.query_spec?.cleaning_conditions&&(!llm||!vlm)){showFeedbackReview(null);return}
  try{
    const suggestion=await api(`/v1/retrieval/tasks/${taskId}/feedback-suggestions`,{method:'POST',body:{draft_id:draft.draft_id,expected_draft_version:draft.draft_version,proposal_digest:draft.proposal_digest,model_selection:{llm,vlm}}});
    if(!current())return;
    showFeedbackReview(suggestion);
  }catch(e){if(!current())return;showFeedbackReview(null);$('status').textContent='模型暂时无法判断：'+e.message}
}
function chatNavigationIntent(){
 const linked=new URLSearchParams(location.search).get('task');
 if(/^rt_[a-f0-9]{32}$/.test(linked||''))return {fresh:false,taskId:linked};
 let fresh=false;try{fresh=Boolean(sessionStorage.getItem('dsh-fresh-chat'))}catch{}
 if(fresh){localStorage.removeItem('dsh-task-id');sessionStorage.removeItem('dsh-dialogue');return {fresh:true,taskId:null}}
 return {fresh:false,taskId:localStorage.getItem('dsh-task-id')};
}
function startNewChat(){
  const hasContent=Boolean(state.task||state.dialogueId||state.conversationId||state.draft||state.agentDraftRecord);
  state.historyOpenToken=(state.historyOpenToken||0)+1;state.sidebarReadToken=(state.sidebarReadToken||0)+1;
  if(typeof stopAgentWindowPolling==='function')stopAgentWindowPolling();state.agentCardIdentity=null;state.agentJobCard=null;state.agentDraftRecord=null;state.agentDraftCard=null;
  state.historyPolicy=null;for(const option of ($('history-references')?.options||[]))option.selected=false;showHistoryPolicy();
  cancelDialogueRequest();
  state.newChatToken=key('chat');try{sessionStorage.setItem('dsh-new-chat-token',state.newChatToken);sessionStorage.setItem('dsh-fresh-chat',state.newChatToken)}catch{}
  // An unsent composer is not a historical conversation. Repeated clicks
  // simply return focus to the same empty workspace.
  if(!hasContent){let context=null;try{context=JSON.parse(sessionStorage.getItem?.('dsh-new-request-context')||'null')}catch{}if(context?.text)$('request-text').value=context.text;$('request-text').focus();return}
  if(typeof stopTaskEvents==='function')stopTaskEvents();
  state.task=null;state.dialogueId=null;state.conversationId=null;state.resumeDraft=null;state.draft=null;
  if(typeof history!=='undefined')history.replaceState(null,'','/chat');
  localStorage.removeItem('dsh-task-id');
  localStorage.removeItem('dsh-model-choice:new');
  sessionStorage.removeItem('dsh-dialogue');
  location.reload();
}
function syncRefillFields(){$('auto-refill-fields').classList.toggle('hidden',!$('auto-refill').checked)}
async function loadRefillLibraries(){
  const select=$('refill-library-ids'),selected=new Set(Array.from(select.selectedOptions,option=>option.value));
  try{
    const data=await api('/v1/retrieval/projects/demo/catalog-config'),entries=data.config?.entries||[];
    select.replaceChildren();
    for(const entry of entries){const option=document.createElement('option');option.value=entry.library_id;option.textContent=entry.library_id+' · '+entry.description;option.selected=selected.has(entry.library_id);select.append(option)}
    const available=entries.length>0;state.refillLibrariesAvailable=available;$('auto-refill').disabled=!available&&!$('auto-refill').checked;
    $('refill-library-status').textContent=available?'只列出“索引来源设置”中当前可用的素材库；可多选。':'尚未配置可用于补数的素材库，请先到左下角“索引来源设置”保存范围。';
  }catch(error){state.refillLibrariesAvailable=false;$('auto-refill').disabled=true;$('refill-library-status').textContent='素材库范围暂不可读：'+(error.detail||error.message)}
}
const settingsPath='/v1/retrieval/projects/demo/model-settings';
let modelConnections=[];
let approvedModelUrls=[];
let registeredModelProfiles=[];
function settingsStatus(text,error=false){const node=$('api-config-status');node.textContent=text;node.dataset.error=String(error)}
$('open-model-settings').addEventListener('click',()=>{$('model-settings-dialog').showModal();loadModelSettings(undefined,true).catch(error=>settingsStatus(error.message,true))});
$('close-model-settings').addEventListener('click',()=>{$('model-settings-dialog').close()});
for(const kind of ['llm','vlm'])$('test-'+kind+'-model').addEventListener('click',async()=>{
  const button=$('test-'+kind+'-model'),profile=$((kind)+'-model').value;
  if(!profile){$('model-test-status').textContent='请先选择模型';return}
  button.disabled=true;$('model-test-status').textContent='正在测试 '+kind.toUpperCase()+'…';
  try{await api(settingsPath+'/probe',{method:'POST',body:{profile_id:profile,kind}});$('model-test-status').textContent=kind.toUpperCase()+' 最小调用通过；不代表所有业务效果已验证。'}
  catch(error){$('model-test-status').textContent=error.message}
  finally{button.disabled=false}
});
async function settingsAction(action){
  const buttons=['save-api-config','refresh-api-models','rotate-api-key','bind-api-models','save-default-models'];
  for(const id of buttons)$(id).disabled=true;
  settingsStatus('正在处理，请稍候…');
  try{await action()}catch(error){settingsStatus(error.detail||error.message,true)}
  finally{for(const id of buttons)$(id).disabled=false;updateCatalogAvailability()}
}
function connectionModelChoices(connection,profiles,kind,serverMode=false){
  if(serverMode)return profiles.filter(p=>p.kind===kind).map(p=>({value:p.id,label:p.provider+' / '+p.model}));
  if(!connection)return [];
  const bound=connection.profiles.filter(p=>p.kind===kind);
  return [...new Set([...connection.models,...bound.map(p=>p.model)])].map(model=>({
    value:bound.find(p=>p.model===model)?.id||'candidate:'+encodeURIComponent(connection.id)+':'+encodeURIComponent(model),label:model}));
}
function allModelChoices(kind){
  const boundIds=new Set(modelConnections.flatMap(c=>c.profiles.map(p=>p.id)));
  const original=registeredModelProfiles.filter(p=>p.kind===kind&&!boundIds.has(p.id)).map(p=>({value:p.id,label:`已有配置 / ${p.model}`}));
  return original.concat(modelConnections.flatMap(connection=>connectionModelChoices(connection,registeredModelProfiles,kind).map(choice=>({value:choice.value,label:`${connection.name} / ${choice.label}`}))));
}
function updateCatalogAvailability(){
  const enabled=allModelChoices('llm').length>0||allModelChoices('vlm').length>0;
  for(const id of ['default-llm-model-input','default-vlm-model-input','save-default-models'])$(id).disabled=!enabled;
}
function renderDialogueModelChoices(){
  const picker=$('dialogue-llm-model'),input=$('dialogue-llm-model-input'),list=$('dialogue-llm-model-list');if(!picker||!input||!list)return;
  const choices=allModelChoices('llm'),selected=state.dialogueModelId??state.serverModelDefaults?.dialogue_llm??'';
  picker.replaceChildren();list.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='沿用扩词模型';picker.append(empty);
  for(const choice of choices){const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;picker.append(option);const suggestion=document.createElement('option');suggestion.value=choice.label;list.append(suggestion)}
  picker.value=choices.some(choice=>choice.value===selected)?selected:'';input.value=choices.find(choice=>choice.value===picker.value)?.label||'';
}
async function chooseDialogueModel(){
  const picker=$('dialogue-llm-model'),input=$('dialogue-llm-model-input'),before=state.dialogueModelId??state.serverModelDefaults?.dialogue_llm??'';
  state.dialogueModelBinding=true;input.disabled=true;
  try{
    let selected=picker.value;
    if(selected.startsWith('candidate:')){const parts=selected.split(':');const bound=await api(`${settingsPath}/connections/${decodeURIComponent(parts[1])}/bind`,{method:'POST',body:{kind:'llm',model:decodeURIComponent(parts[2])}});selected=bound.profile_id}
    const defaults=state.serverModelDefaults||{};
    const saved=await api(settingsPath+'/defaults',{method:'POST',body:{llm:defaults.llm||'',vlm:defaults.vlm||'',dialogue_llm:selected}});
    state.serverModelDefaults=saved.defaults;state.dialogueModelId=selected;
    await loadModels(true);await loadModelSettings();
    $('model-status').textContent='对话模型已保存，下条消息生效。';
  }catch(error){state.dialogueModelId=before;$('model-status').textContent='对话模型保存失败：'+(error.detail||error.message)}
  finally{state.dialogueModelBinding=false;input.disabled=false;renderDialogueModelChoices()}
}
$('dialogue-llm-model-input').addEventListener('change',()=>{const input=$('dialogue-llm-model-input'),choice=allModelChoices('llm').find(item=>item.label===input.value);if(choice||!input.value.trim()){$('dialogue-llm-model').value=choice?.value||'';chooseDialogueModel()}else renderDialogueModelChoices()});
function renderModelChoices(){
  for(const prefix of ['','default-']){
    for(const kind of ['llm','vlm']){
      const id=prefix+kind+'-model',picker=$(id),previous=picker.value,choices=allModelChoices(kind),list=$(id+'-list');
      picker.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent='未选择';picker.append(empty);
      list.replaceChildren();
      for(const choice of choices){const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;picker.append(option);const suggestion=document.createElement('option');suggestion.value=choice.label;list.append(suggestion)}
      picker.value=choices.some(choice=>choice.value===previous)?previous:'';
      $(id+'-input').value=choices.find(choice=>choice.value===picker.value)?.label||'';
    }
  }
  updateCatalogAvailability();
  renderDialogueModelChoices();
}
for(const prefix of ['','default-'])for(const kind of ['llm','vlm']){
  const id=prefix+kind+'-model',input=$(id+'-input');
  input.addEventListener('focus',()=>input.select());
  input.addEventListener('input',()=>{
    const choice=allModelChoices(kind).find(item=>item.label===input.value);
    if(!choice||choice.value===$(id).value)return;
    $(id).value=choice.value;
    if(!prefix)chooseCurrentModel(kind);
  });
  input.addEventListener('change',()=>{
    const choice=allModelChoices(kind).find(item=>item.label===input.value);
    if(choice){if(choice.value!==$(id).value){$(id).value=choice.value;if(!prefix)chooseCurrentModel(kind)}}
    else {const selected=allModelChoices(kind).find(item=>item.value===$(id).value);input.value=selected?.label||'';if(input.value)$('model-status').textContent='请选择搜索结果中的模型';}
  });
}
for(const id of ['api-base-url','api-key'])$(id).addEventListener('input',()=>{
  if($('saved-api-connection').value){$('saved-api-connection').value='';renderConnectionModels()}
  settingsStatus('连接信息尚未提交。请点击“连接并获取模型”，成功后再选择模型。');
});
function renderConnectionModels(){
  const connection=modelConnections.find(item=>item.id===$('saved-api-connection').value);
  $('discovered-models').replaceChildren();
  for(const id of connection?.models||[]){const option=document.createElement('option');option.value=id;$('discovered-models').append(option)}
  $('connection-model-status').textContent=connection?`已保存连接，密钥由服务端加密保管 · ${connection.models.length} 个目录模型 · ${connection.discovery==='listed'?'目录已获取，可直接切换模型':'可刷新模型列表，无需重填密钥'}`:'选择已保存连接即可切换模型，无需重新输入密钥。';
  $('custom-llm-model').value='';$('custom-vlm-model').value='';
  $('new-api-connection').open=!connection&&$('saved-api-connection').value!=='__server__';
  $('saved-connection-actions').classList.toggle('hidden',!connection);
  renderModelChoices();
}
async function loadModelSettings(selected,preferSaved=false){
  const data=await api(settingsPath);modelConnections=data.connections;approvedModelUrls=data.allowed_urls;
  const picker=$('saved-api-connection'),previous=selected||picker.value;picker.replaceChildren();
  const empty=document.createElement('option');empty.value='';empty.textContent='新建连接';picker.append(empty);
  const server=document.createElement('option');server.value='__server__';server.textContent='使用已有服务端配置';picker.append(server);
  for(const connection of modelConnections){const option=document.createElement('option');option.value=connection.id;option.textContent=connection.name;picker.append(option)}
  picker.value=selected||previous||(preferSaved?modelConnections[0]?.id:'')||'';$('approved-model-urls').replaceChildren();
  for(const url of data.allowed_urls){const option=document.createElement('option');option.value=url;$('approved-model-urls').append(option)}
  renderConnectionModels();
}
async function discoverConnection(id){
  if(!id)throw new Error('请先选择已注册连接');
  settingsStatus('连接已保存，正在获取模型列表…');
  try{const result=await api(`${settingsPath}/connections/${id}/discover`,{method:'POST',body:{}});settingsStatus(`连接已保存，已获取 ${result.connection.models.length} 个模型。关闭弹窗后可在左下角“模型设置”切换。`)}
  catch(error){throw new Error('连接已保存，但模型列表获取失败：'+(error.detail||error.message)+'。可点击“刷新模型列表”重试，无需重新注册。')}
  finally{await loadModelSettings(id)}
}
async function saveApiConfig(){await settingsAction(async()=>{
  const body={name:$('api-name').value.trim(),provider:$('api-provider').value,base_url:$('api-base-url').value.trim(),api_key:$('api-key').value.trim()};
  if(!body.base_url||!body.api_key)throw new Error('请填写 API 地址和密钥');
  let parsed;try{parsed=new URL(body.base_url)}catch{throw new Error('API 地址格式不正确，请填写含 HTTPS 协议的完整 Base URL')}
  if(parsed.protocol!=='https:')throw new Error('API 地址必须使用 HTTPS');
  body.base_url=body.base_url.replace(/\/+$/,'');
  if(!approvedModelUrls.includes(body.base_url)&&parsed.pathname==='/'){
    const matches=approvedModelUrls.filter(url=>{try{const allowed=new URL(url);return allowed.origin===parsed.origin}catch{return false}});
    if(matches.length===1)body.base_url=matches[0];
  }
  if(!approvedModelUrls.includes(body.base_url))throw new Error('该 API 地址尚未获准使用。请从地址建议中选择已批准服务，或请管理员添加此地址；密钥尚未提交。');
  body.name=body.name||parsed.hostname;
  settingsStatus('正在保存连接…');
  const data=await api(settingsPath+'/connections',{method:'POST',body});
  $('api-key').value='';
  await loadModelSettings(data.connection.id);
  await discoverConnection(data.connection.id);
})}
$('saved-api-connection').addEventListener('change',renderConnectionModels);
$('refresh-api-models').addEventListener('click',()=>settingsAction(()=>discoverConnection($('saved-api-connection').value)));
$('rotate-api-key').addEventListener('click',()=>settingsAction(async()=>{
  const id=$('saved-api-connection').value,key=$('replacement-api-key').value.trim();if(!id||!key)throw new Error('选择连接并输入新密钥');
  await api(`${settingsPath}/connections/${id}/rotate`,{method:'POST',body:{api_key:key}});$('replacement-api-key').value='';await discoverConnection(id);
}));
$('bind-api-models').addEventListener('click',()=>settingsAction(async()=>{
  const id=$('saved-api-connection').value;if(!id)throw new Error('请先选择连接');
  const selections={llm:$('custom-llm-model').value.trim(),vlm:$('custom-vlm-model').value.trim()};
  if(!selections.llm&&!selections.vlm)throw new Error('请至少填写一个模型 ID');
  for(const kind of ['llm','vlm']){if(selections[kind])await api(`${settingsPath}/connections/${id}/bind`,{method:'POST',body:{kind,model:selections[kind]}})}
  await loadModels(true);await loadModelSettings(id);
  settingsStatus('模型 ID 已保存，可在左下角“模型设置”选择；原连接密钥继续使用。');
}));
async function saveModelDefaults(){
  const defaults={};
  for(const kind of ['llm','vlm']){let value=$('default-'+kind+'-model').value;if(value.startsWith('candidate:')){const parts=value.split(':');const result=await api(`${settingsPath}/connections/${decodeURIComponent(parts[1])}/bind`,{method:'POST',body:{kind,model:decodeURIComponent(parts[2])}});value=result.profile_id}defaults[kind]=value}
  await api(settingsPath+'/defaults',{method:'POST',body:defaults});
  await loadModels(true);await loadModelSettings();
  $('default-model-status').textContent='默认模型已保存，新 Chat 会使用这一套配置。';
}
$('save-default-models').addEventListener('click',async()=>{
  const button=$('save-default-models');button.disabled=true;$('default-model-status').textContent='正在保存默认模型…';
  try{await saveModelDefaults()}catch(error){$('default-model-status').textContent=error.detail||error.message}
  finally{button.disabled=false}
});
function restoreModelChoice(binding){
  state.modelChoiceTouched=true;
  for(const kind of ['llm','vlm']){const picker=$(kind+'-model'),value=binding[kind]||'';
    if(!value||Array.from(picker.options).some(option=>option.value===value))picker.value=value;
  }if(typeof renderModelChoices==='function')renderModelChoices();updateModelMode();
}
function modelChoiceScope(){const taskId=state.task?.task_id||localStorage.getItem('dsh-task-id');if(taskId)return 'task:'+taskId;let dialogueId=state.dialogueId;try{dialogueId=dialogueId||JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')?.id}catch{}return dialogueId?'dialogue:'+dialogueId:'new'}
function modelDefaultsKey(){const defaults=state.serverModelDefaults;return defaults?JSON.stringify([defaults.llm||'',defaults.vlm||'']):null}
function readStoredModelChoice(scope=modelChoiceScope()){
  try{
    const value=JSON.parse(localStorage.getItem('dsh-model-choice:'+scope)||'null');
    if(!value||typeof value.llm!=='string'||typeof value.vlm!=='string')return null;
    const defaultsKey=modelDefaultsKey();
    if(!defaultsKey||value.defaultsKey!==defaultsKey)return null;
    return {llm:value.llm,vlm:value.vlm};
  }catch{return null}
}
function saveCurrentModelChoice(scope=modelChoiceScope()){localStorage.setItem('dsh-model-choice:'+scope,JSON.stringify({llm:$('llm-model').value,vlm:$('vlm-model').value,defaultsKey:modelDefaultsKey()}))}
function showHistoryPolicy(){const p=state.historyPolicy;const box=$('history-dedup-status');if(box)box.textContent=p?.scope==='selected_batches'?`已固定 ${p.references.length} 份历史交付快照；确认需求后按相同内容排除，候补也不会重复使用。`:'仅本批内部去重；未选择历史基准。'}
function bindHistoryControls(){
$('history-load').addEventListener('click',async()=>{try{const data=await api('/v1/retrieval/projects/demo/historical-batches?dimension_id=default');const box=$('history-references');box.replaceChildren();for(const item of data.options){if(item.reference.task_id===state.task?.task_id)continue;const option=document.createElement('option');option.value=JSON.stringify(item.reference);option.textContent=`${item.label} · ${item.count} 条 · ${item.reference.batch_id} · ${item.reference.delivery_id}`;box.append(option)}$('history-dedup-status').textContent=box.options.length?'请选择交付快照并点击“应用历史选择”；尚未改变当前方案。':'当前没有本人在本项目、本子维度可选的已交付图片批次。'}catch(error){$('history-dedup-status').textContent=error.message}});
$('history-apply').addEventListener('click',async()=>{try{const references=Array.from($('history-references').selectedOptions,option=>JSON.parse(option.value));const result=await api('/v1/retrieval/projects/demo/historical-dedup',{method:'POST',body:{dimension_id:'default',references}});state.historyPolicy=result.policy;showHistoryPolicy()}catch(error){$('history-dedup-status').textContent=error.message}});
$('history-clear').addEventListener('click',()=>{state.historyPolicy=null;for(const option of $('history-references').options)option.selected=false;showHistoryPolicy()});
}
$('prepare-task').addEventListener('click',prepareTask);$('request-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareTask()}});$('prepare-feedback').addEventListener('click',prepareFeedback);$('feedback-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareFeedback()}});$('new-task').addEventListener('click',startNewChat);$('refresh-history').addEventListener('click',refreshHistory);$('save-api-config').addEventListener('click',saveApiConfig);document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();startNewChat()}});
bindHistoryControls();
$('auto-refill').addEventListener('change',syncRefillFields);
$('stop-request').addEventListener('click',()=>state.requestController?.abort());
$('history-list').addEventListener('click',event=>{if(event.target.closest('.history-item'))cancelDialogueRequest()},{capture:true});
$('insert-request-example').addEventListener('click',insertRequestExample);
for(const button of document.querySelectorAll('[data-result-view]'))button.addEventListener('click',()=>loadResultView(button.dataset.resultView));
$('result-feedback-form').addEventListener('submit',submitResultFeedback);
$('close-result-feedback').addEventListener('click',()=>{$('result-feedback-dialog').close();state.resultFeedback=null});
$('cancel-result-feedback').addEventListener('click',()=>{$('result-feedback-dialog').close();state.resultFeedback=null});
$('result-feedback-dialog').addEventListener('cancel',()=>{state.resultFeedback=null});
function updateModelMode(){
  const llm=$('llm-model'),enabled=!!llm.value;
  const modelName=llm.selectedOptions[0]?.textContent||llm.value;
  $('chat-model-badge').textContent=enabled?'LLM 已选择':'模板模式';
  $('chat-model-badge').classList.toggle('neutral',!enabled);
  $('composer-model-note').textContent=enabled?'':'请先在左下角“模型设置”选择 LLM';
  $('feedback-model-note').textContent=enabled?'':'请先选择对话模型。';
}
async function chooseCurrentModel(kind){
  const picker=$(kind+'-model');state.modelChoiceTouched=true;
  if(picker.value.startsWith('candidate:')){
    const parts=picker.value.split(':'),input=$(kind+'-model-input');input.disabled=true;
    try{
      const result=await api(`${settingsPath}/connections/${decodeURIComponent(parts[1])}/bind`,{method:'POST',body:{kind,model:decodeURIComponent(parts[2])}});
      await loadModels(true);picker.value=result.profile_id;await loadModelSettings();
      $('model-status').textContent='当前 Chat 已选择模型，继续使用已保存的连接密钥。';
    }catch(error){picker.value='';$('model-status').textContent='模型切换失败：'+(error.detail||error.message)}
    finally{input.disabled=false}
  }
  renderModelChoices();updateModelMode();saveCurrentModelChoice();if(state.dialogueId)saveDialogueSession();
  if(state.requestController)$('model-status').textContent='模型已切换；本轮仍使用发送时的模型，下条消息生效。';
}
$('llm-model').addEventListener('change',()=>chooseCurrentModel('llm'));
$('vlm-model').addEventListener('change',()=>chooseCurrentModel('vlm'));
$('target-count').addEventListener('input',()=>{state.targetCountTouched=true});
async function loadModels(preserve=false){
  try{
    const data=await api('/v1/retrieval/projects/demo/models');
    registeredModelProfiles=data.items;
    state.serverModelDefaults=data.defaults||{};
    const previous={llm:$('llm-model').value,vlm:$('vlm-model').value};
    for(const kind of ['llm','vlm']){for(const prefix of ['','default-']){const picker=$(prefix+kind+'-model');picker.replaceChildren();const option=document.createElement('option');option.value='';option.textContent='未选择';picker.append(option)}}
    for(const model of data.items){if(!['llm','vlm'].includes(model.kind))continue;const option=document.createElement('option');option.value=model.id;option.textContent=`${model.provider} / ${model.model} (${model.id})`;$(model.kind+'-model').append(option)}
    for(const model of data.items){if(!['llm','vlm'].includes(model.kind))continue;const option=document.createElement('option');option.value=model.id;option.textContent=`${model.provider} / ${model.model}`;$('default-'+model.kind+'-model').append(option)}
    const llms=data.items.filter(model=>model.kind==='llm'),vlms=data.items.filter(model=>model.kind==='vlm');
    if(!state.modelChoiceTouched&&llms.length===1)$('llm-model').value=llms[0].id;
    if(!state.modelChoiceTouched&&vlms.length===1)$('vlm-model').value=vlms[0].id;
    for(const kind of ['llm','vlm']){const value=data.defaults?.[kind]||'';$('default-'+kind+'-model').value=value;if(!preserve&&!state.modelChoiceTouched&&Object.hasOwn(data.defaults||{},kind))$(kind+'-model').value=value}
    if(preserve||state.modelChoiceTouched)restoreModelChoice(previous);
    $('model-status').textContent=llms.length?(data.runtime?.engine==='dsh'?'DSH · 当前 Chat 模型':'当前 Chat 模型'):'尚未配置服务端 LLM';
    updateModelMode();
    renderDialogueModelChoices();
  }catch(e){$('model-status').textContent='模型目录加载失败：'+e.message;updateModelMode()}
}
async function loadBuildVersion(){
  try{
    const build=await api('/v1/build');
    const commit=build.commit?build.commit.slice(0,10):'未知';
    const source=build.source_clean===true?'已提交':build.source_clean===false?'存在未提交修改':'源码状态未知';
    const planning=build.model_engine==='dsh'?'DSH':build.model_engine==='direct'?'直连模型':'未配置模型';
    const engine=build.conversation_engine==='direct'?`对话：直连模型 · 规划：${planning}`:planning;
    const retrieval=build.retrieval_mode==='LOCAL_CLIP'?'本地真实 CLIP 测试索引':
      (build.retrieval_mode==='local_fixture'||build.retrieval_mode==='LOCAL_EXACT')?
      '本地测试索引':'未配置检索';
    $('build-version').textContent=`版本 ${commit} · ${source} · ${engine} · ${retrieval} · 启动 ${build.started_at||'未知'}`;
  }catch(error){$('build-version').textContent='运行版本暂不可读';}
}
(async function initialize(){const navigation=chatNavigationIntent(),bootEpoch=state.historyOpenToken||0;void loadAgentWindowProfile();setHistoryOpen(false);renderPlanningWorkflow('request');resetResultWorkspace();syncRefillFields();void loadBuildVersion();void loadRefillLibraries();await loadModels();await loadModelSettings().catch(error=>{$('api-config-status').textContent=error.message});const savedModels=readStoredModelChoice();if(savedModels&&!state.modelChoiceTouched)restoreModelChoice(savedModels);await refreshHistory();if(bootEpoch!==(state.historyOpenToken||0))return;const taskId=navigation.taskId;if(new URLSearchParams(location.search).get('task'))history.replaceState(null,'','/chat');if(navigation.fresh){resetTimeline('新建对话','你想找什么图片？需要多少张？');$('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden');$('status').textContent=''}else if(taskId){if(!await openTask(taskId))localStorage.removeItem('dsh-task-id')}else {await restoreDialogueSession();await recoverDialogueCommand()}if(!state.task){let context=null;try{context=JSON.parse(sessionStorage.getItem('dsh-new-request-context')||'null')}catch{}const prefill=context?.text||sessionStorage.getItem('dsh-new-request-prefill');if(prefill){sessionStorage.removeItem('dsh-new-request-prefill');$('request-text').value=prefill;$('request-text').focus()}}})();
"""

from .candidate_ui import CANDIDATE_CSS, CANDIDATE_JS, CANDIDATE_HTML

CHAT_CSS += """
.query-image-input{display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:8px;border-top:1px solid #eceeeb}
.query-image-choose,.query-image-replace{display:inline-flex;align-items:center;width:max-content;border:1px solid #dbe1dc;border-radius:9px;padding:6px 9px;background:#edf1ed;color:#33453a;font-size:11px;font-weight:650;cursor:pointer}
.query-image-preview{display:grid;grid-template-columns:54px minmax(0,1fr) auto auto;align-items:center;gap:9px;width:100%;padding:7px;background:#f5f7f4;border-radius:10px}
.query-image-preview img{width:54px;height:54px;object-fit:contain;border-radius:7px;background:white;border:1px solid #dce2dd}
.query-image-preview strong{display:block;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.query-image-preview p{margin:3px 0 0}.query-image-preview label{margin:0}.query-image-exclude{font-size:11px!important;margin-top:5px!important}.query-image-exclude input{width:auto}
"""

QUERY_IMAGE_JS = r"""
function clearQueryImage(){if(state.queryImageObjectUrl)URL.revokeObjectURL(state.queryImageObjectUrl);state.queryImageObjectUrl=null;state.queryImage=null;const input=$('query-image-file');if(input)input.value='';if($('query-image-exclude'))$('query-image-exclude').checked=false;$('query-image-preview')?.classList.add('hidden');$('query-image-choose')?.classList.remove('hidden')}
async function selectQueryImage(file){if(!file)return;if(!['image/jpeg','image/png'].includes(file.type))throw new Error('参考图仅支持静态 JPEG 或 PNG');if(file.size>20*1024*1024)throw new Error('参考图不得超过 20 MB');clearQueryImage();$('query-image-choose').classList.add('hidden');$('query-image-preview').classList.remove('hidden');$('query-image-name').textContent=file.name||'参考图';$('query-image-status').textContent='正在校验并保存…';const response=await fetch('/v1/retrieval/projects/demo/query-images',{method:'POST',headers:{'Content-Type':file.type,'X-Tenant-Id':'tenant-a','X-User-Id':'user-a','Idempotency-Key':key('query-image-upload')},body:file});const data=await response.json();if(!response.ok){clearQueryImage();const error=new Error(data.message||'参考图上传失败');error.code=data.code;throw error}const preview=await fetch(data.preview_url,{headers:{'X-Tenant-Id':'tenant-a','X-User-Id':'user-a'}});if(!preview.ok){clearQueryImage();throw new Error('参考图已保存，但受控预览加载失败')}const blob=await preview.blob();state.queryImageObjectUrl=URL.createObjectURL(blob);state.queryImage=data;$('query-image-thumbnail').src=state.queryImageObjectUrl;$('query-image-status').textContent=`已安全保存 · ${data.width}×${data.height} · ${data.format}`}
"""
CHAT_JS = CHAT_JS.replace('eventBusy:false};',
    'eventBusy:false,queryImage:null,queryImageObjectUrl:null};', 1)
CHAT_JS = CHAT_JS.replace('function stopTaskEvents(){', QUERY_IMAGE_JS + '\nfunction stopTaskEvents(){', 1)
CHAT_JS = CHAT_JS.replace('clearTaskExpansionPolling();state.guidedResultBubble=null;',
    'clearTaskExpansionPolling();clearQueryImage();state.guidedResultBubble=null;', 1)
CHAT_JS = CHAT_JS.replace('(async function initialize(){',
    "$('query-image-file').addEventListener('change',async event=>{const file=event.target.files?.[0];try{await selectQueryImage(file);$('status').textContent='参考图已保存；当前尚未执行检索。'}catch(error){$('status').textContent=error.message}});"
    "$('query-image-remove').addEventListener('click',()=>{clearQueryImage();$('status').textContent='已从本次输入移除参考图；服务端不可变记录不会被改写。'});\n"
    '(async function initialize(){', 1)

CHAT_HTML = CHAT_HTML.replace('<script src="/chat.js">', CANDIDATE_HTML + '<script src="/chat.js">')
CHAT_CSS += CANDIDATE_CSS
CHAT_JS += CANDIDATE_JS

from .delivery_ui import DELIVERY_HTML, DELIVERY_CSS, DELIVERY_JS
CHAT_HTML = CHAT_HTML.replace('<script src="/chat.js">', DELIVERY_HTML + '<script src="/chat.js">')
CHAT_CSS += DELIVERY_CSS
CHAT_JS += DELIVERY_JS

from .ui_typography import TYPOGRAPHY_CSS, CHAT_TYPOGRAPHY_CSS

# The catalog also uses /chat.css, including its configuration dialogs.
CHAT_CSS += TYPOGRAPHY_CSS + CHAT_TYPOGRAPHY_CSS

from .video_ui import VIDEO_DRAFT_HTML, VIDEO_DRAFT_JS

CHAT_HTML = CHAT_HTML.replace('<a class="nav-link" href="/production">生产线路</a>',
    '<button id="open-video-draft" class="secondary" type="button">视频草稿</button><a class="nav-link" href="/production">生产线路</a>', 1)
CHAT_HTML = CHAT_HTML.replace('<script src="/chat.js">', VIDEO_DRAFT_HTML + '<script src="/chat.js">', 1)
CHAT_JS += VIDEO_DRAFT_JS

from .video_workspace import VIDEO_WORKSPACE_HTML, VIDEO_WORKSPACE_JS

CHAT_HTML = CHAT_HTML.replace('<p id="video-draft-summary" class="helper"></p>',
    '<p id="video-draft-summary" class="helper"></p>' + VIDEO_WORKSPACE_HTML, 1)
CHAT_JS += VIDEO_WORKSPACE_JS

from .agent_retrieval_chat import AGENT_RETRIEVAL_CHAT_JS
CHAT_JS += AGENT_RETRIEVAL_CHAT_JS
CHAT_CSS += "\n.agent-details{margin-top:14px;color:var(--muted);font-size:12px}.agent-details summary{cursor:pointer}.agent-details dd{overflow-wrap:anywhere}\n"
