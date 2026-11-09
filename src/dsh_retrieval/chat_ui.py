"""Dependency-free local Chat preview assets. Never embed credentials or production endpoints."""

CHAT_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DSH 检索需求体验</title><link rel="stylesheet" href="/chat.css"></head>
<body><a class="skip-link" href="#main-content">跳到主要内容</a><main id="main-content">
  <nav class="topbar" aria-label="主导航"><div class="topbar-start"><button id="toggle-history" class="secondary mobile-history-toggle" type="button" aria-controls="history-panel" aria-expanded="false">历史 Chat</button><a class="brand" href="/" aria-label="DSH 生产线路首页"><span class="brand-mark" aria-hidden="true">D</span><span>DSH Retrieval</span></a></div><div class="topbar-start"><a class="nav-link" href="/production">生产线路</a><a class="nav-link" href="/catalog">已有索引配置 <span aria-hidden="true">↗</span></a></div></nav>
  <div class="context-bar" aria-label="当前项目上下文"><span class="status-dot" aria-hidden="true"></span><span>项目 <strong>demo</strong></span><span class="project-divider" aria-hidden="true"></span><span>子维度 <strong>default</strong></span></div>
  <div class="app-shell"><aside class="history panel" id="history-panel" aria-labelledby="history-heading"><div class="history-title"><h2 id="history-heading">历史 Chat</h2><div class="history-actions"><button class="icon-button" id="refresh-history" aria-label="刷新历史 Chat" title="刷新历史 Chat">↻</button><button class="icon-button mobile-history-close" id="close-history" aria-label="关闭历史 Chat" type="button">×</button></div></div><div id="history-list" aria-live="polite"></div><nav id="history-pager" class="history-pager" aria-label="历史 Chat 分页" hidden><button id="history-prev" type="button" aria-label="上一页历史 Chat">‹</button><span id="history-page-label" aria-live="polite"></span><button id="history-next" type="button" aria-label="下一页历史 Chat">›</button></nav><div class="history-footer" aria-label="设置与环境"><button id="open-model-panel" type="button" aria-haspopup="dialog"><span aria-hidden="true">⚙</span>模型设置</button><button id="open-environment-info" type="button" aria-haspopup="dialog"><span aria-hidden="true">ⓘ</span>体验环境说明</button></div></aside>
  <div class="chat">
    <section class="conversation" aria-labelledby="conversation-heading"><div class="section-heading conversation-heading"><div><span class="section-kicker">CONVERSATION</span><h2 id="conversation-heading">对话</h2></div><span id="conversation-status" class="status-badge">先沟通，再检索</span></div><div id="timeline" class="timeline" role="log" aria-live="polite" aria-relevant="additions"><article class="message system"><h2>新建数据需求</h2><p>告诉我想找什么；不清楚的地方我会先问，确认检索词后才开始。</p></article></div></section>
    <section class="chat-composer panel" id="new-request" aria-labelledby="request-heading"><h2 class="sr-only" id="request-heading">与检索 Agent 对话</h2><label class="sr-only" for="request-text">输入消息</label><textarea id="request-text" rows="3" placeholder="直接说想找什么，例如：找 20 张狗图片，横图，JPG 格式" required></textarea><div class="chat-composer-footer"><button id="insert-request-example" class="secondary small" type="button">参考说法</button><details class="advanced-request"><summary>更多设置</summary><div class="advanced-fields"><div class="field-grid"><div class="field"><label for="group-parent">上级分类 <span class="optional">可选</span></label><input id="group-parent" placeholder="例如：动物"></div><div class="field"><label for="target-count">目标数量 <span class="optional">也可在对话中说明</span></label><input id="target-count" type="number" min="1" max="10000" placeholder="例如：100"></div></div><div class="field"><label for="group-lines">交付分组 <span class="optional">可选，每行一组</span></label><textarea id="group-lines" rows="3" placeholder="狗 | 金毛，柯基&#10;猫 | 橘猫，布偶猫"></textarea></div><div class="field refill-settings"><label class="choice-row" for="auto-refill"><input id="auto-refill" type="checkbox"><span>自动补足目标数量</span></label><p class="helper">只会在下方明确选择的已配置素材库内继续检索、生成增量候选并清洗。</p><p id="refill-library-status" class="helper">正在读取已配置素材库…</p><div id="auto-refill-fields" class="hidden"><label for="refill-library-ids">允许补数的素材库</label><select id="refill-library-ids" multiple size="3" aria-describedby="refill-library-status"></select><div class="field-grid"><div class="field"><label for="refill-max-rounds">最多补数轮次 <span class="optional">留空不限</span></label><input id="refill-max-rounds" type="number" min="1" step="1" placeholder="不限"></div><div class="field"><label for="refill-max-vlm-calls">最多 VLM 调用 <span class="optional">留空不限</span></label><input id="refill-max-vlm-calls" type="number" min="1" step="1" placeholder="不限"></div><div class="field"><label for="refill-no-progress-rounds">连续无进展停止</label><input id="refill-no-progress-rounds" type="number" min="1" step="1" value="2"></div></div></div></div></div></details><span id="composer-model-note" class="helper">正在检查模型…</span><button id="stop-request" class="secondary hidden" type="button">暂停</button><button id="prepare-task" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <section class="chat-composer panel hidden" id="feedback-box" aria-labelledby="feedback-heading"><h2 class="sr-only" id="feedback-heading">继续对话</h2><label class="sr-only" for="feedback-text">输入消息</label><textarea id="feedback-text" rows="3" placeholder="输入消息"></textarea><span id="feedback-model-note" class="hidden" aria-hidden="true"></span><div class="chat-composer-footer"><details class="advanced-request search-diagnostics"><summary>检索工具</summary><div class="preview-actions" aria-label="检索工具"><button class="secondary" id="preview-branches">查看查询分支（未执行）</button><button class="secondary" id="preview-routing">查看选库结果（未执行）</button><button class="secondary" id="first-search">本地首次检索（需确认）</button><button class="secondary" id="first-search-candidates">查看原始候选</button></div></details><button class="secondary small" id="new-task">新建 Chat</button><button id="prepare-feedback" aria-label="发送消息">发送 <span aria-hidden="true">↑</span></button></div></section>
    <p id="status" role="status" aria-live="polite"></p>
  </div>
  <aside class="right-rail" aria-label="Chat 操作与检索结果">
    <section class="rail-card" id="task-sidebar" aria-labelledby="task-sidebar-heading"><div class="rail-heading"><div><span class="section-kicker">WORKFLOW</span><h2 id="task-sidebar-heading">检索流程</h2></div></div><div class="task-identity hidden" id="task-sidebar-identity"><span>需求 ID</span><code id="task-sidebar-id"></code><button id="open-candidate-window" class="secondary" type="button">检索候选 / 正式结果</button></div><p id="task-sidebar-summary" class="helper"></p><div id="task-sidebar-nodes" class="progress-nodes" aria-label="五节点进度：扩检索词、检索、清洗、补数、打包"></div><p id="retrieval-live-status" class="helper" role="status"></p></section>
    <section class="rail-card hidden result-workspace" id="result-sidebar" aria-labelledby="result-sidebar-heading"><div class="rail-heading"><div><span class="section-kicker">RESULTS</span><h2 id="result-sidebar-heading">结果</h2></div></div><details id="result-details" class="result-details"><summary><span id="result-compact-counts">查看素材与复核</span><span class="result-details-hint">展开 / 收起</span></summary><div id="result-counts" class="result-counts" aria-label="结果计数"></div><div class="result-tabs" role="tablist" aria-label="结果分类"><button type="button" data-result-view="accepted" role="tab" aria-selected="true" disabled>可用素材</button><button type="button" data-result-view="review" role="tab" aria-selected="false" disabled>需要我判断</button><button type="button" data-result-view="failed" role="tab" aria-selected="false" disabled>规则淘汰</button><button type="button" data-result-view="user_excluded" role="tab" aria-selected="false" disabled>我已排除</button></div><div id="result-list" class="result-list"></div></details></section>
  </aside></div><button id="history-backdrop" class="history-backdrop" type="button" aria-label="关闭历史 Chat"></button>
  <dialog id="model-panel-dialog" class="sidebar-dialog" aria-labelledby="model-heading"><section class="rail-card model-config" id="model-config" aria-labelledby="model-heading"><div class="rail-heading"><div><span class="section-kicker">MODELS</span><h2 id="model-heading">模型设置</h2></div><button id="close-model-panel" class="secondary" type="button" aria-label="关闭模型设置">关闭</button></div><div id="model-controls" class="model-controls"><span id="chat-model-badge" class="status-badge neutral">正在连接</span>
      <div class="field compact-field"><label for="llm-model-input">当前 Chat · DSH 需求理解 / 扩词模型</label><input id="llm-model-input" list="llm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="llm-model-list"></datalist><select id="llm-model" hidden aria-hidden="true"><option value="">未选择</option></select></div>
      <div class="field compact-field"><label for="vlm-model-input">当前 Chat · 清洗语义判定模型</label><input id="vlm-model-input" list="vlm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="vlm-model-list"></datalist><select id="vlm-model" hidden aria-hidden="true"><option value="">未选择</option></select></div>
      <p id="model-status" class="helper">正在加载可用模型…</p>
      <details class="api-config"><summary>默认模型 · 新 Chat 使用</summary><div class="api-fields"><div class="field-grid"><div class="field"><label for="default-llm-model-input">默认 LLM</label><input id="default-llm-model-input" list="default-llm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="default-llm-model-list"></datalist><select id="default-llm-model" hidden aria-hidden="true"></select></div><div class="field"><label for="default-vlm-model-input">默认 VLM</label><input id="default-vlm-model-input" list="default-vlm-model-list" autocomplete="off" placeholder="输入名称搜索并选择"><datalist id="default-vlm-model-list"></datalist><select id="default-vlm-model" hidden aria-hidden="true"></select></div></div><button id="save-default-models" type="button">保存默认模型</button><p id="default-model-status" class="helper" role="status">一套默认模型，供所有新 Chat 使用。</p></div></details>
      <button id="open-model-settings" class="secondary" type="button">配置模型服务</button>
      <details class="api-config"><summary>测试当前模型</summary><div class="actions"><button id="test-llm-model" type="button" class="secondary">测试 LLM</button><button id="test-vlm-model" type="button" class="secondary">测试 VLM</button></div><p id="model-test-status" class="helper" role="status">测试会调用所选模型，可能产生费用。</p></details></div>
    </section>
  </dialog>
  <dialog id="environment-info-dialog" class="sidebar-dialog" aria-labelledby="environment-info-title"><div class="rail-heading"><h2 id="environment-info-title">体验环境说明</h2><button id="close-environment-info" class="secondary" type="button" aria-label="关闭体验环境说明">关闭</button></div><p id="build-version" class="helper">正在读取运行版本…</p><p class="helper">当前使用模拟身份和本地账本。测试索引检索不等于真实素材授权或清洗通过；模型建议在确认前不会执行。</p></dialog>
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
.composer{padding:18px;display:grid;gap:14px}.chat-composer{padding:12px;background:white}.chat-composer>textarea{border:0;box-shadow:none!important;min-height:74px;padding:8px}.chat-composer-footer{display:flex;align-items:end;justify-content:space-between;gap:10px;border-top:1px solid #eceeeb;padding-top:9px}.advanced-request{flex:1}.advanced-request summary{width:max-content;cursor:pointer;color:#5f6862;font-size:12px;font-weight:650;padding:6px 2px}.advanced-fields{display:grid;gap:10px;padding:12px 0 4px}.chat-composer-footer>button{border-radius:999px;padding:9px 14px}.field-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}.field{display:grid;gap:5px}.field label{font-size:12px;font-weight:700;color:#3b443e}.field label small{color:var(--muted);font-weight:600}.optional{color:#7a817c;font-weight:500;margin-left:4px}.form-footer,.preview-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}.preview-actions{justify-content:flex-start}.status-badge{display:inline-flex;align-items:center;border-radius:999px;padding:4px 8px;background:#e0f0e5;color:#245b3e;font-size:10px;font-weight:750;white-space:nowrap}.status-badge.neutral{background:#eef0ed;color:#69716c}
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
main:not(.catalog-page){height:100dvh;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding-bottom:16px}.topbar{flex:none}.topbar-start,.history-actions{display:flex;align-items:center;gap:8px}.context-bar{flex:none}.app-shell{flex:1;min-height:0;grid-template-rows:minmax(0,1fr)}.history,.right-rail{position:static;top:auto;max-height:none;height:100%;min-height:0;overflow:auto}.history{display:flex;flex-direction:column;overflow:hidden}.history-title{flex:none;background:white;padding:10px 0}#history-list{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain}.history-pager{flex:none;display:flex;align-items:center;justify-content:center;gap:12px;border-top:1px solid #ededea;margin-top:8px;padding:9px 0 2px;color:var(--muted);font-size:11px}.history-pager[hidden]{display:none}.history-pager button{display:grid;place-items:center;width:26px;height:26px;padding:0;border:1px solid transparent;border-radius:7px;background:transparent;color:#4c6052;font-size:20px;line-height:1;box-shadow:none}.history-pager button:hover:not(:disabled){background:#edf2ed;color:#214c32;transform:none}.history-pager button:disabled{opacity:.3;cursor:default}.right-rail{align-content:start;overscroll-behavior:contain}.chat{display:flex;flex-direction:column;height:100%;min-height:0}.conversation{display:flex;flex:1;flex-direction:column;min-height:0}.conversation-heading{flex:none;padding:4px 4px 10px;background:#f7f7f5}.conversation-heading>div{min-width:0}.conversation-heading h2{max-width:40vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.timeline{flex:1;min-height:0;overflow:auto;align-content:start;margin-top:0;overscroll-behavior:contain}.chat-composer{flex:none;box-shadow:0 8px 28px #1c2c2218}.model-controls,.result-list{display:grid;gap:12px;min-width:0}.result-list .message{max-width:100%;min-width:0;box-shadow:none}.mobile-history-toggle,.mobile-history-close,.history-backdrop{display:none}
@media(max-width:900px){dialog.sidebar-dialog{left:16px;bottom:16px}main:not(.catalog-page){height:auto;min-height:100dvh;overflow:visible;padding:10px 14px 48px}.topbar{position:sticky;top:0;z-index:12;background:#f7f7f5}.context-bar{position:sticky;top:44px;z-index:11;margin:4px auto 10px}.app-shell{display:block;min-height:0;flex:none}.chat{height:auto;min-height:calc(100dvh - 150px)}.conversation{min-height:0}.timeline{min-height:260px;max-height:calc(100dvh - 300px)}.chat-composer{position:sticky;bottom:8px;z-index:8}.right-rail{height:auto;max-height:none;overflow:visible;margin-top:16px}.history{position:fixed;top:0;left:0;z-index:31;width:min(86vw,320px);height:100dvh;max-height:none;border-radius:0 14px 14px 0;transform:translateX(-110%);transition:transform .2s;box-shadow:0 0 30px #0003}.history.is-open{transform:translateX(0)}.history-backdrop.is-open{display:block;position:fixed;inset:0;z-index:30;border:0;border-radius:0;background:#17221c80}.mobile-history-toggle,.mobile-history-close{display:inline-flex}.mobile-history-toggle{width:auto;padding:6px 9px;font-size:11px}.history-title{top:-9px}.brand span:last-child{display:none}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
.search-hit img{display:block;width:min(100%,220px);height:140px;object-fit:contain;background:#f1f4f1;border:1px solid #dce9df;border-radius:8px}.search-hit .preview-note{color:#795d30}
.history-footer{flex:none;display:grid;gap:2px;margin-top:8px;padding-top:8px;border-top:1px solid #ededea}.history-footer button{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;text-align:left;background:transparent;border:0;border-radius:8px;color:#47534b;font-size:12px;box-shadow:none}.history-footer button span{width:18px;text-align:center;font-size:16px;color:#68746b}.history-footer button:hover:not(:disabled){background:#f0f2ef;color:#263a2e;transform:none}
dialog.sidebar-dialog{position:fixed;top:auto;left:max(16px,calc((100vw - 1500px)/2 + 270px));bottom:16px;margin:0;width:min(420px,calc(100vw - 32px));max-height:min(80vh,680px);padding:16px;overflow:auto;border:1px solid #d6dbd6;border-radius:14px;background:white;color:var(--ink);box-shadow:0 18px 60px #17221c35}dialog.sidebar-dialog::backdrop{background:#17221c33}dialog.sidebar-dialog .model-config{border:0;box-shadow:none;padding:0;gap:12px}dialog.sidebar-dialog .model-controls{display:grid;gap:12px}dialog.sidebar-dialog .rail-heading h2{margin:0;font-size:16px}dialog.sidebar-dialog .helper{font-size:12px}
"""

CHAT_JS = r"""
const state={task:null,draft:null,feedback:null,feedbackReviewBubble:null,feedbackManualCard:null,expansionPreviewBubble:null,expansionPreviewToken:null,expansionPreviewPlan:null,confirmedRevision:null,conversationId:null,resumeDraft:null,connection:null,firstSearchPreview:null,firstSearchProposal:null,firstSearchChoices:[],candidateManifest:null,cleaningProposal:null,cleaningOperation:null,searchAttempt:null,targetCountTouched:false,modelChoiceTouched:false,proposedExpansion:null,confirmedCount:null,guidedResultBubble:null,guidedPhase:'input',dialogueId:null,dialogueVersion:0,dialogueStage:'request',requestController:null,expansionJob:null,expansionJobBubble:null,expansionPollTimer:null,refillLibrariesAvailable:false,eventTimer:null,eventController:null,eventTaskId:null,eventSequence:0,eventBusy:false};
const headers={'Content-Type':'application/json','X-Tenant-Id':'tenant-a','X-User-Id':'user-a'};
const $=id=>document.getElementById(id); const timeline=$('timeline');
const key=prefix=>prefix+'-'+crypto.randomUUID();
async function api(path,{method='GET',body,keyValue,signal}={}){const h={...headers};if(keyValue)h['Idempotency-Key']=keyValue;const r=await fetch(path,{method,headers:h,body:body?JSON.stringify(body):undefined,signal});const data=await r.json();if(!r.ok){const error=new Error(`${data.code||r.status}: ${data.message||'请求失败'}`);error.code=data.code||String(r.status);error.detail=data.message||'请求失败';throw error}return data}
function stopTaskEvents(){eventRefreshEpoch++;clearTimeout(eventRefreshTimer);eventRefreshTimer=null;eventRefreshDirty=false;eventRefreshResults=false;eventOutputVersions=null;if(state.eventTimer)clearTimeout(state.eventTimer);state.eventTimer=null;if(state.eventController)state.eventController.abort();state.eventController=null;state.eventTaskId=null;state.eventSequence=0;state.eventBusy=false}
let eventRefreshTimer=null,eventRefreshEpoch=0,eventRefreshDirty=false,eventRefreshResults=false,eventOutputVersions=null;
function scheduleTaskEventRefresh(taskId,event=null){
 if(state.eventTaskId!==taskId)return;eventRefreshDirty=true;
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
  const summary=await api(`/v1/retrieval/tasks/${taskId}/summary`);if(!current())return;
  const progress=summary.progress||await api(`/v1/retrieval/tasks/${taskId}/progress`);if(!current())return;
  const result=needResults?await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`):state.currentResults;if(!current())return;
  if(needResults){const latest=await api(`/v1/retrieval/tasks/${taskId}/summary`);if(!current())return;if(latest.task_version!==summary.task_version){eventRefreshDirty=true;eventRefreshResults=true;return}}
  state.task=summary;showProgress(progress,result);if(needResults)showResults(result,taskId);
  $('retrieval-live-status').textContent=`SSE 已恢复到事件序列 ${state.eventSequence} · ${new Date().toLocaleTimeString()}`;
 }catch(error){if(current()){if(['NOT_FOUND','PROJECT_ACCESS_DENIED','ACCESS_DENIED'].includes(error.code)){stopTaskEvents();return}eventRefreshDirty=true;eventRefreshResults=eventRefreshResults||needResults}throw error}
 finally{if(current()){state.eventBusy=false;if(eventRefreshDirty&&eventRefreshTimer===null)eventRefreshTimer=setTimeout(()=>{eventRefreshTimer=null;refreshTaskEventSnapshot(taskId).catch(()=>{})},500)}}
}
async function pollTaskEvents(){const taskId=state.eventTaskId;if(!taskId||state.eventBusy||document.hidden||state.task?.task_id!==taskId)return;try{const page=await api(`/v1/retrieval/tasks/${taskId}/events?after_sequence=${state.eventSequence}&limit=100&compact=true`);if(state.eventTaskId!==taskId||state.task?.task_id!==taskId)return;state.eventSequence=page.next_sequence;for(const event of page.events)scheduleTaskEventRefresh(taskId,event)}catch(error){if(error.code==='EVENT_HISTORY_EXPIRED'){state.eventSequence=0;$('retrieval-live-status').textContent='事件历史已过期，正在从权威快照恢复';return}if(['NOT_FOUND','REVISION_CONFLICT'].includes(error.code)){stopTaskEvents();return}$('retrieval-live-status').textContent='实时更新暂不可用；页面保留最后一次权威快照'}}
async function connectTaskEventStream(){const taskId=state.eventTaskId;if(!taskId||state.task?.task_id!==taskId)return;if(document.hidden){state.eventTimer=setTimeout(connectTaskEventStream,1000);return}const controller=new AbortController();state.eventController=controller;try{const response=await fetch(`/v1/retrieval/tasks/${taskId}/events/stream?after_sequence=${state.eventSequence}`,{headers:{...headers,Accept:'text/event-stream'},signal:controller.signal});if(!response.ok){const data=await response.json();const error=new Error(data.message||'SSE 连接失败');error.code=data.code||String(response.status);throw error}if(!response.body)throw new Error('浏览器不支持流式事件读取');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(state.eventTaskId===taskId){const chunk=await reader.read();if(chunk.done)break;buffer+=decoder.decode(chunk.value,{stream:true}).replace(/\r\n/g,'\n');let boundary,changed=false;while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);let kind='message',data='';for(const line of frame.split('\n')){if(line.startsWith('event:'))kind=line.slice(6).trim();else if(line.startsWith('data:'))data+=(data?'\n':'')+line.slice(5).trim()}if(kind==='stream-error'){const value=JSON.parse(data);const error=new Error(value.message||'SSE 流中断');error.code=value.code;throw error}if(kind==='task-event'&&data){const event=JSON.parse(data);if(Number.isInteger(event.sequence)&&event.sequence>state.eventSequence){state.eventSequence=event.sequence;changed=true;scheduleTaskEventRefresh(taskId,event)}}}}}catch(error){if(error.name==='AbortError'||state.eventTaskId!==taskId)return;if(error.code==='EVENT_HISTORY_EXPIRED'){state.eventSequence=0;$('retrieval-live-status').textContent='SSE 历史已过期，正在从权威快照恢复'}else if(['NOT_FOUND','REVISION_CONFLICT'].includes(error.code)){stopTaskEvents();return}else{$('retrieval-live-status').textContent='SSE 暂时断开，正在通过事件水位恢复'}await pollTaskEvents()}finally{if(state.eventController===controller)state.eventController=null;if(state.eventTaskId===taskId)state.eventTimer=setTimeout(connectTaskEventStream,1000)}}
function startTaskEvents(taskId,asOf=0){stopTaskEvents();state.eventTaskId=taskId;state.eventSequence=Number.isInteger(asOf)?asOf:0;connectTaskEventStream()}
function renderExecutionHistory(groups){for(const group of groups||[]){const runs=group.runs||[],latest=runs[0];card('执行历史 · '+group.task_id,[['已关联运行',runs.length],['最近状态',latest?.status||'尚无模型运行'],['最近版本',latest?.revision?`revision ${latest.revision} · generation ${latest.generation} · version ${latest.task_version}`:'无'],['最近模型',latest?.model_profile||'无'],['说明','仅展示脱敏运行标识与状态；业务结果仍以当前版本结果集为准']])}}
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
function draftBody(text,count,groupedExpansion,refill={enabled:false,libraries:[],limits:{max_rounds:null,max_vlm_calls:null,no_progress_rounds:2}},queryImage=null,excludeExactContent=false){const automatic=refill.enabled===true;const query={schema_version:'query-spec@2',raw_request:text,media_type:'image',target_count:count,business_domain:'local_preview',intra_batch_dedup:{policy_ref:'exact-content',config_version:'1'},cross_batch_dedup:{scope:'none',references:[],comparison_methods:[],reference_digest:null},refill_policy:{mode:automatic?'maintain_target':'input_only',target_count:count,approved_library_ids:automatic?refill.libraries:[],progression:automatic?'auto':'manual'},refill_limits:refill.limits};if(queryImage)query.query_input={type:'reference_image',query_image_id:queryImage.query_image_id,digest:queryImage.digest,exclude_exact_content:excludeExactContent===true};if(groupedExpansion)query.grouped_expansion=groupedExpansion;if(groupedExpansion?.model_binding)query.rules=[{rule_id:'semantic-match',predicate_ref:'image.semantic-match@1',operator:'eq',value:true,hardness:'hard',origin:'user_confirmed'}];return{dimension_id:'default',query_spec:query,node_modes:{expansion:'manual',retrieval:automatic?'auto':'manual',cleaning:automatic?'auto':'manual',refill:automatic?'auto':'manual',packaging:'manual'}}}

function groupPreviewBody(text,count,selection){const body=groupInputBody(text),llm=selection?.llm??$('llm-model')?.value,vlm=selection?.vlm??$('vlm-model')?.value;if(llm&&vlm){body.model_selection={llm,vlm};if(body.raw_request)body.target_count=count}return body}
function groupInputBody(text,lines=$('group-lines').value.trim()){if(!lines)return{raw_request:text,dimension_id:'default'};const groups=lines.split(/\n/).filter(line=>line.trim()).map(line=>{const parts=line.split('|');if(parts.length>2)throw new Error('每行仅使用一个 | 分隔主标签和召回词');return{primary_label:parts[0].trim(),auxiliary_terms:(parts[1]||'').split(/[，,、]/).map(t=>t.trim()).filter(Boolean)}});return{dimension_id:'default',parent_label:$('group-parent').value.trim()||null,groups}}
function editProposal(){if(!state.draft||state.task)return;state.draft=null;timeline.replaceChildren();$('new-request').classList.remove('hidden');$('request-text').focus()}
function retireDraftActions(){state.draft=null;timeline.querySelectorAll('.actions').forEach(box=>box.remove())}
function showSavedGroups(plan){if(!plan)return;for(const group of plan.groups){card('已确认分组 · '+group.primary_label,[['上级分类',plan.parent_label||'无'],['召回词',group.auxiliary_terms.join('、')||'仅使用主标签'],['交付设置',group.package_name+' · 独立包（待执行）'],['词来源',plan.generator==='llm@1'?'LLM 建议':plan.generator==='user-edited@1'?'用户编辑':'模板建议'],...(group.delivery_target==null?[]:[['目标数量',group.delivery_target+' 张']])])}}

async function previewBranches(){if(!state.task)return;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${state.task.task_id}/query-branches`);card('查询分支预览',[['绑定',`revision ${data.revision} · generation ${data.run_generation} · task version ${data.task_version}`],['分支数量',data.preview.branches.length],['预览摘要',data.preview.preview_digest],['状态','未选库 · 未批准 · 未执行']]);for(const branch of data.preview.branches){card('查询 · '+branch.query_role,[['检索文本',branch.query_text],['来源',branch.origins.map(o=>`${o.query_role}: ${o.source_term} (${o.skill_version})`).join('；')],['约束引用',branch.constraint_refs.join('、')||'无结构化约束；原文条件不代表已编译规则'],['指纹',branch.query_fingerprint]])}message('system','执行边界',data.preview.note)}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
function setBusy(v){document.querySelectorAll('button,#llm-model,#vlm-model,#llm-model-input,#vlm-model-input,#target-count,#auto-refill,#refill-library-ids,#refill-max-rounds,#refill-max-vlm-calls,#refill-no-progress-rounds').forEach(b=>{if(b.id!=='stop-request')b.disabled=v});if(!v&&!state.refillLibrariesAvailable&&!$('auto-refill').checked)$('auto-refill').disabled=true;const activity=typeof timeline==='undefined'?null:timeline;if(activity)activity.setAttribute('aria-busy',String(v));if(v)$('status').textContent=''}
function setDialogueBusy(v){$('prepare-task').disabled=v;if(v)$('status').textContent=''}
function setFollowupBusy(v){state.followupBusy=v;$('prepare-feedback').disabled=v;if(v)$('status').textContent=''}
function cancelDialogueRequest(){const controller=state.requestController;if(controller){state.requestController=null;controller.abort();$('stop-request')?.classList?.add('hidden');setDialogueBusy(false)}}
async function previewRouting(){if(!state.task)return;const taskId=state.task.task_id;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${taskId}/query-routing`);if(state.task?.task_id!==taskId)return;card('选库结果 · 未批准',[['任务版本',`revision ${data.revision} · generation ${data.generation} · version ${data.task_version}`],['路由摘要',data.routing_digest],['配置摘要',data.catalog_config_digest||'无独立配置版本'],['执行边界',data.note]]);for(const route of data.routes){card('查询 · '+route.query_text,[['分支',route.branch_id],['可用索引',route.options.length?route.options.map(o=>`${o.library_id} / ${o.binding_id} · ${o.index_version}\n编码器 ${o.encoder_ref} · ${o.vector_dimension} 维 · ${o.metric} · 每批上限 ${o.max_batch_size}`).join('\n\n'):'无匹配的已授权索引或已注册查询能力；请检查索引配置和别名']])}}catch(e){$('status').textContent=e.message+'；请先在“已有索引配置”中确认配置，管理员还需登记查询能力。'}finally{setBusy(false)}}
async function previewFirstSearch(){if(!state.task)return;const taskId=state.task.task_id;setBusy(true);try{const data=await api(`/v1/retrieval/tasks/${taskId}/first-search`);if(state.task?.task_id!==taskId)return;state.firstSearchPreview=data;state.firstSearchProposal=null;state.firstSearchChoices=[];card('本地首次检索 · 选索引',[['模式',data.mode==='LOCAL_CLIP'?'LOCAL_CLIP · 本地真实图文向量':'LOCAL_EXACT · 本地测试向量'],['计划状态','未批准、未执行'],['说明',data.note]]);for(const route of data.routes){const section=card('查询分支 · '+route.query_text,[['分支',route.branch_id],['可选索引',route.options.length]]);for(const option of route.options){const label=document.createElement('label');label.className='search-choice';const box=document.createElement('input');box.type='checkbox';box.setAttribute('aria-label',`选择 ${option.library_id} / ${option.binding_id}`);box.addEventListener('change',()=>{const key=route.branch_id+'|'+option.binding_id;state.firstSearchChoices=state.firstSearchChoices.filter(row=>row.key!==key);if(box.checked)state.firstSearchChoices.push({key,branch_id:route.branch_id,binding_id:option.binding_id,batch_size:Math.min(2,option.max_batch_size)})});const text=document.createElement('span');text.textContent=`${option.library_id} / ${option.binding_id} · ${option.index_version} · ${option.encoder_ref} · ${option.vector_dimension}维 · ${option.metric}`;label.append(box,text);section.append(label)}}card('下一步',[['说明','勾选一个或多个索引，再生成完整确认卡；当前不会发起检索。']],[['生成检索确认卡',prepareFirstSearch,false]])}catch(e){$('status').textContent=e.message+'；本地首次检索需要服务端显式启用测试夹具。'}finally{setBusy(false)}}
async function prepareFirstSearch(){const preview=state.firstSearchPreview;if(!state.task||!preview||!state.firstSearchChoices.length){$('status').textContent='请先选择至少一个索引';return}setBusy(true);try{const choices=state.firstSearchChoices.map(({branch_id,binding_id,batch_size})=>({branch_id,binding_id,batch_size}));const proposal=await api(`/v1/retrieval/tasks/${state.task.task_id}/first-search/prepare`,{method:'POST',body:{routing_digest:preview.routing_digest,choices}});state.firstSearchProposal=proposal;card('本地检索确认卡 · 尚未执行',[['分支与索引',proposal.members.map(m=>`${m.branch_id}: ${m.library_id} / ${m.binding_id} (${m.index_version})`).join('\n')],['每页数量',proposal.members.map(m=>`${m.binding_id}: ${m.batch_size}`).join('；')],['方案摘要',proposal.proposal_digest],['说明',proposal.note]],[['确认整组检索方案',confirmFirstSearch,false]])}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function confirmFirstSearch(){const proposal=state.firstSearchProposal;if(!state.task||!proposal)return;setBusy(true);try{const taskId=state.task.task_id;const confirmed=await api(`/v1/retrieval/tasks/${taskId}/first-search/confirm`,{method:'POST',keyValue:key('first-search-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});state.task=await api(`/v1/retrieval/tasks/${taskId}`);card('检索方案已确认 · 后台执行',[['摘要',confirmed.set_digest],['模式',confirmed.mode],['说明',confirmed.note]]);$('feedback-box').classList.remove('hidden');setBusy(false);await guidedTick()}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
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
function appendLocalCandidateGroups(bubble,items){
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
async function openDialogueSession(sessionId,previewText=null){
  if(typeof cancelDialogueRequest==='function')cancelDialogueRequest();
  let previous=null;try{previous=JSON.parse(sessionStorage.getItem('dsh-dialogue')||'null')}catch{}
  sessionStorage.setItem('dsh-dialogue',JSON.stringify({id:sessionId,proposal:previous?.id===sessionId?previous.proposal:null,models:readStoredModelChoice('dialogue:'+sessionId)||{llm:$('llm-model').value,vlm:$('vlm-model').value}}));
  localStorage.removeItem('dsh-task-id');state.task=null;state.conversationId=null;state.dialogueId=null;state.draft=null;state.resumeDraft=null;
  $('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden');
  await restoreDialogueSession(previewText);
}
function fillDraftForm(draft){const query=draft.proposal.query_spec;$('request-text').value=query.raw_request;$('target-count').value=query.target_count;const grouped=query.grouped_expansion;if(grouped?.model_binding)restoreModelChoice(grouped.model_binding);$('group-parent').value=grouped?.parent_label||'';$('group-lines').value=grouped?.groups?.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n')||'';const refill=query.refill_policy||{};$('auto-refill').checked=refill.mode==='maintain_target';for(const library of refill.approved_library_ids||[]){let option=Array.from($('refill-library-ids').options).find(item=>item.value===library);if(!option){option=document.createElement('option');option.value=library;option.textContent=library+'（原草稿范围，当前配置中不可见）';$('refill-library-ids').append(option)}option.selected=true}const limits=query.refill_limits||{};$('refill-max-rounds').value=limits.max_rounds??'';$('refill-max-vlm-calls').value=limits.max_vlm_calls??'';$('refill-no-progress-rounds').value=limits.no_progress_rounds??2;syncRefillFields()}
function showHistoryLoading(label){stopTaskEvents();clearExpansionPolling();clearTaskExpansionPolling();$('conversation-heading').textContent=label;$('conversation-status').textContent='读取中';timeline.replaceChildren();message('system','正在读取','先显示对话内容，随后补齐生产节点与结果。');$('task-sidebar').classList.add('hidden');resetResultWorkspace()}
function renderSavedTaskMessages(items){for(const item of items||[]){if(item.role==='user'||item.role==='assistant')message(item.role,'',item.text)}}
async function openConversation(conversationId,previewText=null){const token=state.historyOpenToken=(state.historyOpenToken||0)+1,started=performance.now();setBusy(true);showHistoryLoading('正在打开历史 Chat…');$('status').textContent='正在打开历史 Chat…';if(previewText){resetTimeline('历史需求摘要',previewText);timeline.dataset.firstTextMs=String(performance.now()-started);timeline.dataset.firstTextSource='history-summary'}try{const data=await api(`/v1/retrieval/conversations/${conversationId}`);if(token!==state.historyOpenToken)return;sessionStorage.removeItem('dsh-dialogue');state.dialogueId=null;state.conversationId=conversationId;state.task=null;state.resumeDraft=null;localStorage.removeItem('dsh-task-id');resetTimeline('历史 Chat','可以在下方继续这段对话。草稿消息不会进入业务执行时间线。');const clarification=data.clarification_messages||[];if(clarification.length)renderSavedTaskMessages(clarification);for(const item of clarification.length?data.messages.slice(1):data.messages){message('user','',item.text)}renderSavedTaskMessages(data.task_messages);timeline.dataset.fullTextMs=String(performance.now()-started);if(!previewText){timeline.dataset.firstTextMs=timeline.dataset.fullTextMs;timeline.dataset.firstTextSource='conversation'}renderExecutionHistory(data.executions);if(data.confirmations.length){const latest=data.confirmations[data.confirmations.length-1];const task=await api(`/v1/retrieval/tasks/${latest.task_id}/summary`);if(token!==state.historyOpenToken)return;const [progress,results]=await Promise.all([task.progress||api(`/v1/retrieval/tasks/${latest.task_id}/progress`),api(`/v1/retrieval/tasks/${latest.task_id}/results?view=accepted&limit=20`)]);if(token!==state.historyOpenToken)return;state.task=task;localStorage.setItem('dsh-task-id',task.task_id);showProgress(progress,results);showResults(results,task.task_id);startTaskEvents(task.task_id,progress.as_of_sequence);$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');refreshTaskSidebar();offerExistingSearch(task.task_id)}else{const last=data.messages[data.messages.length-1],draft=await api(`/v1/retrieval/projects/demo/drafts/${last.draft_id}`);if(token!==state.historyOpenToken)return;state.draft=draft;state.resumeDraft={draft_id:draft.draft_id,draft_version:draft.draft_version};fillDraftForm(draft);$('request-heading').textContent='继续这个 Chat';$('new-request').classList.remove('hidden');$('feedback-box').classList.add('hidden')}$('status').textContent='';refreshHistory()}catch(e){if(token===state.historyOpenToken)$('status').textContent=e.message}finally{if(token===state.historyOpenToken)setBusy(false)}}
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
function refillExecutionSummary(plan){const execution=plan.execution||{};return `目标 ${plan.target_count} · 累计有效 ${plan.accepted_count} · 缺口 ${plan.gap} · ${plan.stop_reason?'停止原因 '+refillStopLabel(plan.stop_reason):plan.action==='wait'?'补数进行中':'尚未停止'} · 已发 ${execution.issued_rounds??0} 轮 · 已完成 ${execution.completed_rounds??0} 轮 · VLM 调用 ${execution.vlm_calls??0}`}
async function loadRefillRounds(taskId,container,button,cursor=null,history=[]){const token=state.refillRoundsToken=(state.refillRoundsToken||0)+1;button.disabled=true;container.textContent='正在读取逐轮补数证据…';try{const params=new URLSearchParams({limit:'24'});if(cursor)params.set('cursor',cursor);const plan=await api(`/v1/retrieval/tasks/${taskId}/nodes/refill/items?${params}`);plan.execution={...plan.execution,rounds:plan.items};if(token!==state.refillRoundsToken||state.task?.task_id!==taskId)return;container.replaceChildren();const summary=document.createElement('p');summary.textContent=refillExecutionSummary(plan);container.append(summary);const rows=refillRoundRows(plan.execution);if(!rows.length){const empty=document.createElement('p');empty.textContent='尚未产生补数轮次。';container.append(empty)}for(const [title,detail] of rows){const row=document.createElement('p');row.textContent=`${title}：${detail}`;container.append(row)}const note=document.createElement('p');note.textContent='有效数量按 SHA-256 累计去重后的清洗通过结果计算，不把检索返回数计为有效。';container.append(note);const previous=document.createElement('button'),next=document.createElement('button');previous.textContent='上一页';previous.disabled=!history.length;previous.onclick=()=>loadRefillRounds(taskId,container,button,history[history.length-1],history.slice(0,-1));next.textContent='下一页';next.disabled=!plan.next_cursor;next.onclick=()=>loadRefillRounds(taskId,container,button,plan.next_cursor,[...history,cursor]);container.append(previous,next)}catch(error){if(token!==state.refillRoundsToken)return;container.textContent=error.code==='REFILL_NOT_CONFIGURED'?'当前方案未启用自动补数。':'补数证据读取失败：'+error.message}finally{button.disabled=false}}
function completedNodeOutput(node,progress,results){if(node.node==='cleaning'&&results?.available)return `有效 ${results.counts.accepted} · 淘汰 ${results.counts.failed} · 规则待复核 ${results.counts.pending_review} · SHA-256 精确重复已自动合并 ${results.counts.duplicate}`;if(node.node==='refill'&&results?.available)return `累计有效 ${results.counts.accepted} · 缺口 ${results.counts.gap} · 停止原因 ${refillStopLabel(results.stop_reason)}`;if(node.node==='packaging'&&results?.available)return `结果版本 ${results.result_set_digest} · 可交付 ${results.counts.accepted}`;return node.detail||'节点已完成，尚未发布独立输出。'}
function retrievalSourceSummary(candidates){const count=candidates?.sources?.length||0;return count?`数据来源：智能选择 · ${count} 个预制数据源`:'数据来源：智能选择'}
function nodeCard(node,index,progress,results,searchProgress=null,candidates=null,manifest=null){
  const currentSearch=index===1&&searchProgress?.status==='approved'&&searchProgress.revision===progress.revision&&searchProgress.generation===progress.run_generation&&searchProgress.task_version===progress.task_version;
  const fixedSearch=index===1&&manifest?.confirmed&&manifest.candidate_count>=0;
  const currentManifest=index===1&&manifest&&!manifest.confirmed&&manifest.expected_task_version===progress.task_version;
  const pendingManifest=currentManifest&&manifest.candidate_count>0;
  const emptyManifest=currentManifest&&manifest.candidate_count===0;
  const attempt=index===1&&state.searchAttempt?.taskId===progress.task_id&&state.searchAttempt.revision===progress.revision?state.searchAttempt:null;
  const statusText=node.node==='packaging'&&node.gate_status==='awaiting_approval'?'待发布':pendingManifest?'待确认':currentSearch?searchStatusLabel(searchProgress):fixedSearch?'已完成':attempt?.status==='unavailable'?'不可用':attempt?.status==='checking'?'进行中':index===0&&progress.revision>0&&node.node_status==='running'?'已完成':({pending:'未开始',running:'进行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'}[node.node_status]||'未开始');
  const completed=statusText==='已完成',expandable=index<=1||completed||node.node==='cleaning'||(node.node==='refill'&&statusText!=='未开始'),card=document.createElement(expandable?'details':'div');card.className='node-card';
  if(expandable){const key=`${progress.task_id}:${node.node}`;card.dataset.node=node.node;const saved=state.nodeOpen?.[key];card.open=saved===undefined?Boolean(currentSearch&&state.searchNodeOpen!==false):saved;card.addEventListener('toggle',()=>{(state.nodeOpen??={})[key]=card.open;if(currentSearch)state.searchNodeOpen=card.open})}
  if(currentSearch||attempt?.status==='checking')card.classList.add('search-active');else if(attempt?.status==='unavailable')card.classList.add('search-unavailable');
  const displayLabel=node.node==='expansion'?'扩检索词':node.label,header=nodeHeader(index+1,displayLabel,statusText,expandable?'summary':'div');if(!expandable)header.className='node-heading';card.append(header);
  if(node.node==='cleaning'){const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');summary.textContent=results?.available?`合格预览：入选 ${results.counts.accepted} · 候补 ${results.counts.reserve} · 规则待复核 ${results.counts.pending_review} · SHA-256 精确重复已自动合并 ${results.counts.duplicate}`:node.detail;const button=document.createElement('button');button.className='secondary';button.textContent='查看各步骤结果';const output=document.createElement('div');button.addEventListener('click',()=>loadCleaningSteps(progress.task_id,output,button));body.append(summary,button,output);card.append(body);return card}
  if(node.node==='packaging'){const body=document.createElement('div');body.className='node-body';const note=document.createElement('p');note.textContent=node.detail;const button=document.createElement('button');button.className='secondary';button.textContent='打包 / 发布 / 下载';button.onclick=()=>openDelivery(progress.task_id);body.append(note,button);card.append(body);return card}
  if(node.node==='refill'&&expandable){const body=document.createElement('div');body.className='node-body';const summary=document.createElement('p');summary.textContent=completed?completedNodeOutput(node,progress,results):(node.detail||'补数进行中。');const button=document.createElement('button');button.className='secondary';button.textContent='查看逐轮补数证据';const output=document.createElement('div');button.addEventListener('click',()=>loadRefillRounds(progress.task_id,output,button));body.append(summary,button,output);card.append(body);return card}
  if(index>1){if(completed){const body=document.createElement('div');body.className='node-body';const output=document.createElement('p');output.textContent=completedNodeOutput(node,progress,results);body.append(output);card.append(body)}return card}
  if(index===0){const body=document.createElement('div');body.className='node-body node-terms';const groups=confirmedQuery(progress)?.grouped_expansion?.groups||[];for(const group of groups){const row=document.createElement('div');row.className='node-term-group';const label=document.createElement('strong');label.textContent=group.primary_label;const terms=document.createElement('p');terms.textContent=group.auxiliary_terms?.join('、')||'仅主标签';row.append(label,terms);body.append(row)}if(!groups.length){const note=document.createElement('p');note.textContent='暂无已确认检索词。';body.append(note)}card.append(body);return card}
  const body=document.createElement('div');body.className='node-body';const note=document.createElement('p');
  note.textContent=pendingManifest?`补检索已返回 · 本轮新增 ${manifest.candidate_count} 张已校验候选，尚待确认。`:currentSearch?`${retrievalSourceSummary(candidates)} · 原始候选 ${candidates?.raw_hit_count??'读取中'} 条 · ${searchProgress.pagination_stopped?'检索已完成':searchProgress.initial_window_complete?'首轮候选已就绪，后续位置留给自动补数':'检索进行中'}`:fixedSearch?`检索已完成 · 已固定 ${manifest.candidate_count} 张候选图片 · 候选清单 ${manifest.manifest_ref}`:emptyManifest&&node.node_status==='succeeded'?'检索已完成 · 本轮没有新增稳定候选，不会重复进入清洗。':node.node_status==='succeeded'?(results?.available?'检索已完成 · 已进入清洗并发布合格预览。':node.detail||'检索已完成。'):attempt?.status==='unavailable'?attempt.reason:attempt?.status==='checking'?'正在智能选择预制数据源…':'尚未开始检索。';body.append(note);
  if(pendingManifest&&manifest.candidate_count){const approve=document.createElement('button');approve.className='secondary';approve.textContent=results?.available?'确认本轮新增候选':'确认候选清单';approve.addEventListener('click',showCandidateManifestReview);body.append(approve)}
  if(currentSearch){
    const counts=document.createElement('p');counts.textContent=searchSummary(searchProgress,candidates);body.append(counts);
    const snapshot=state.searchSnapshot;if(snapshot?.taskId===progress.task_id){const detail=document.createElement('p');detail.textContent=(snapshot.error?'结果读取失败：'+snapshot.error+'；保留上次状态。 ':'')+(snapshot.updated?'最近读取 '+snapshot.updated:'');body.append(detail)}
    const button=document.createElement('button');button.className='secondary';button.textContent='检索候选';button.addEventListener('click',openCandidateBrowser);body.append(button);
    const retry=document.createElement('button');retry.className='secondary';retry.textContent='刷新状态';retry.addEventListener('click',()=>refreshTaskSidebar());body.append(retry);
    for(const member of searchProgress.members||[]){if(member.last_error){const error=document.createElement('p');error.textContent='分支错误：'+(typeof member.last_error==='string'?member.last_error:JSON.stringify(member.last_error));body.append(error)}}
    if((searchProgress.pagination_stopped||searchProgress.initial_window_complete)&&candidates?.verified_count){const approve=document.createElement('button');approve.className='secondary';approve.textContent=state.candidateManifest?'候选清单已确认':'确认候选清单';approve.disabled=Boolean(state.candidateManifest);approve.addEventListener('click',showCandidateManifestReview);body.append(approve)}
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
function showProgress(progress,results,searchProgress=null,candidates=null,manifest=null){state.currentProgress=progress;if(results)state.currentResults=results;$('task-sidebar').classList.remove('hidden');$('task-sidebar-identity').classList.remove('hidden');$('task-sidebar-id').textContent=state.task?.task_id||progress.task_id||'';$('task-sidebar-summary').textContent=`版本 ${progress.revision} · 目标 ${progress.counts.target} · 有效 ${progress.counts.accepted===null?'未知':progress.counts.accepted}`;const nodes=$('task-sidebar-nodes');if(state.sidebarTaskId===progress.task_id)for(const card of nodes.querySelectorAll('details.node-card[data-node]'))(state.nodeOpen??={})[`${progress.task_id}:${card.dataset.node}`]=card.open;state.sidebarTaskId=progress.task_id;const signature=JSON.stringify([progress,results,state.confirmedRevision,manifest]);if(state.sidebarSignature===signature&&nodes.children.length===progress.nodes.length){const retrieval=progress.nodes.findIndex(node=>node.node==='retrieval');if(retrieval>=0)nodes.children[retrieval].replaceWith(nodeCard(progress.nodes[retrieval],retrieval,progress,results,searchProgress,candidates,manifest))}else{nodes.replaceChildren();progress.nodes.forEach((node,index)=>nodes.append(nodeCard(node,index,progress,results,searchProgress,candidates,manifest)));state.sidebarSignature=signature}}
function searchBranchCounts(progress){
 const groups=new Map();for(const m of progress?.members||[]){const key=m.branch_id||m.plan_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(m)}
 let success=0,failed=0,running=0,waiting=0;
 for(const members of groups.values()){const done=m=>Boolean(m.done||m.status==='stopped'||(m.stop_reason&&!['more_available','retry_pending'].includes(m.stop_reason)));const error=m=>Boolean(m.last_error||['failed','error'].includes(m.status)||['failed','error'].includes(m.stop_reason));if(members.every(done)){if(members.some(error))failed++;else success++}else if(progress.job_status==='failed'){failed++}else if(members.some(m=>m.pages||m.committed_pages||m.status==='pending'||m.status==='more_available'))running++;else waiting++}
 return {success,failed,running,waiting,total:groups.size};
}
function searchStatusLabel(progress){const counts=searchBranchCounts(progress);if(progress.job_status==='failed')return '检索失败';if(progress.job_status==='superseded')return '已替代';if(progress.pagination_stopped)return counts.failed?'部分分支失败':'检索已结束';if(progress.initial_window_complete)return '首轮候选已就绪';return counts.running||counts.success||progress.job_status==='running'?'检索中':'等待检索'}
function searchSummary(progress,candidates){const c=searchBranchCounts(progress);return `${c.success+c.failed}/${c.total} 分支已结束 · 成功 ${c.success} · 失败 ${c.failed} · 进行中 ${c.running} · 待启动 ${c.waiting}\n累计原始命中 ${candidates?.raw_hit_count??'未知'} 条 · 未清洗`}
async function refreshTaskSidebar(){
 if(!state.task)return;const taskId=state.task.task_id,revision=state.task.confirmed_revision,generation=state.task.run_generation;
 const readToken=state.sidebarReadToken=(state.sidebarReadToken||0)+1;
 const same=()=>state.sidebarReadToken===readToken&&state.task?.task_id===taskId&&state.task.confirmed_revision===revision&&state.task.run_generation===generation;
 if(state.searchSnapshot?.taskId!==taskId||state.searchSnapshot?.revision!==revision||state.searchSnapshot?.generation!==generation)state.searchSnapshot={taskId,revision,generation};
 const snapshot=state.searchSnapshot;
 try{
  // After cleaning advances the task version, the original search endpoints
  // answer REVISION_CONFLICT for that version every time. Once candidates are
  // frozen or a result exists, stop re-asking until the version moves again.
  const frozen=Boolean(snapshot.manifest?.confirmed||snapshot.results?.available),knownVersion=snapshot.progress?.task_version;
  snapshot.staleReads=snapshot.staleReads||{};
  const read=(name,path)=>frozen&&knownVersion!=null&&snapshot.staleReads[name]===knownVersion?Promise.resolve(null):api(path);
  const [progress,results,search,manifest]=await Promise.allSettled([api(`/v1/retrieval/tasks/${taskId}/summary`).then(summary=>summary.progress||api(`/v1/retrieval/tasks/${taskId}/progress`)),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=1`),read('search',`/v1/retrieval/tasks/${taskId}/first-search/progress`),read('manifest',`/v1/retrieval/tasks/${taskId}/first-search/manifest`)]);
  if(!same())return;snapshot.error=null;
  const versionNow=progress.status==='fulfilled'?progress.value.task_version:knownVersion;
  const staleRead=result=>result.status==='rejected'&&['REVISION_CONFLICT','PLAN_NOT_CURRENT'].includes(result.reason?.code);
  for(const [name,result] of [['search',search],['manifest',manifest]])if(staleRead(result)&&versionNow!=null)snapshot.staleReads[name]=versionNow;
  if(manifest.status==='fulfilled'&&manifest.value){
    snapshot.manifest=manifest.value;
    if(manifest.value.confirmed&&manifest.value.expected_task_version===state.task.task_version)state.candidateManifest=manifest.value;
    else if(state.candidateManifest?.expected_task_version!==manifest.value.expected_task_version)state.candidateManifest=null;
  }
  for(const [name,result] of [['progress',progress],['results',results]]){if(result.status==='fulfilled')snapshot[name]=result.value;else snapshot.error=result.reason.message}
  if(search.status==='fulfilled'){if(search.value)snapshot.search=search.value}else if(!snapshot.manifest?.confirmed&&!snapshot.results?.available&&!(frozen&&staleRead(search)))snapshot.error=search.reason.message;
  const currentSearch=snapshot.search?.status==='approved'&&snapshot.search.revision===revision&&snapshot.search.generation===generation&&snapshot.search.task_version===snapshot.progress?.task_version;
  if(currentSearch){try{const data=await api(`/v1/retrieval/tasks/${taskId}/first-search/candidates?limit=1`);if(!same())return;snapshot.candidates=data}catch(error){snapshot.error=error.message}}
  if(!same())return;if(!snapshot.error)snapshot.updated=new Date().toLocaleTimeString();
  if(!snapshot.progress){$('task-sidebar-summary').textContent='需求已保存；状态读取失败：'+snapshot.error;return}
  const cached=state.confirmedRevision?.taskId===taskId&&state.confirmedRevision.revision===revision;
  if(!cached){try{const revisions=await api(`/v1/retrieval/tasks/${taskId}/revisions`);if(!same())return;const item=revisions.items.find(r=>r.revision===revision);if(item)state.confirmedRevision={taskId,revision,proposal:item.proposal}}catch(error){snapshot.error=error.message}}
  if(!same())return;showProgress(snapshot.progress,snapshot.results,snapshot.search,snapshot.candidates,snapshot.manifest);
  const fixedSearch=snapshot.manifest?.confirmed;
  const retrievalSucceeded=snapshot.progress.nodes?.some(node=>node.node==='retrieval'&&node.node_status==='succeeded');
  $('retrieval-live-status').textContent=(currentSearch?searchStatusLabel(snapshot.search)+' · '+searchSummary(snapshot.search,snapshot.candidates):fixedSearch?`检索已结束 · 已固定 ${snapshot.manifest.candidate_count} 张候选图片`:retrievalSucceeded?'检索已结束':'需求已保存 · 等待检索')+(snapshot.error?' · 读取失败：'+snapshot.error+'（上次状态可能滞后）':'');
  if(currentSearch&&searchStatusLabel(snapshot.search)!=='等待检索'){const identity=taskId+':'+revision+':'+generation;state.searchNotified=state.searchNotified||new Set();if(!state.searchNotified.has(identity)){state.searchNotified.add(identity);message('assistant','检索已启动','可在右侧检索节点查看进度和打开检索候选。')}}
  // Auto-refill tasks keep a cursor open, so search never reads as "stopped"
  // and the only confirm button sat in a collapsed node: a real run waited
  // there indefinitely. Say once per version that candidates need the owner.
  if(currentSearch&&(snapshot.search.pagination_stopped||snapshot.search.initial_window_complete)&&snapshot.candidates?.verified_count&&!state.candidateManifest&&!snapshot.manifest?.confirmed){const identity=[taskId,revision,generation,snapshot.progress.task_version].join(':');state.manifestPrompted=state.manifestPrompted||new Set();if(!state.manifestPrompted.has(identity)){state.manifestPrompted.add(identity);guidedAction(message('assistant','检索候选已就绪',snapshot.manifest?.candidate_count?`已校验 ${snapshot.manifest.candidate_count} 张候选图片。确认候选清单后才会开始清洗。`:'候选图片已校验。确认候选清单后才会开始清洗。'),'确认候选清单',showCandidateManifestReview)}}
 }catch(error){if(same())$('retrieval-live-status').textContent='结果读取失败：'+error.message+'；需求仍已保存。'}
}
const resultViewLabels={accepted:'可用',review:'需要判断',failed:'规则淘汰',user_excluded:'已排除'};
function resetResultWorkspace(){$('result-details').open=false;state.resultTaskId=null;state.currentResults=null;state.resultView='accepted';$('result-sidebar').classList.add('hidden');renderResultCounts(null);syncResultTabs('accepted');for(const button of document.querySelectorAll('[data-result-view]'))button.disabled=true;$('result-list').replaceChildren()}
function renderResultCounts(results){$('result-compact-counts').textContent=results?.available?`可用 ${results.counts.accepted??'—'} · 待判断 ${results.counts.pending_review??'—'} · 缺口 ${results.counts.gap??'—'}`:'查看素材与复核';const counts=$('result-counts');counts.replaceChildren();if(!results?.available)return;for(const [label,value] of [['可用',results.counts.accepted],['候补',results.counts.reserve],['需判断',results.counts.pending_review],['规则淘汰',results.counts.failed],['已排除',results.counts.user_excluded??0],['缺口',results.counts.gap]]){const item=document.createElement('span');item.className='result-count';const strong=document.createElement('strong');strong.textContent=value??'—';item.append(strong,label);counts.append(item)}}
function syncResultTabs(view){for(const button of document.querySelectorAll('[data-result-view]'))button.setAttribute('aria-selected',String(button.dataset.resultView===view))}
async function loadResultView(view,cursor=null,append=false){const taskId=state.task?.task_id;if(!taskId)return;try{const page=await api(`/v1/retrieval/tasks/${taskId}/results?view=${view}&limit=20`+(cursor?'&cursor='+encodeURIComponent(cursor):''));state.resultView=view;showResults(page,taskId,append)}catch(error){$('status').textContent='结果读取失败：'+error.message}}
function openResultFeedback(mode,taskId,results,item,scope='candidate'){state.resultFeedback={mode,taskId,results,item,scope};const labels={exclude:'排除素材',rule:'升级为全任务规则',manual_accept:'人工通过',manual_reject:'人工淘汰'};$('result-feedback-title').textContent=labels[mode];const affected=scope==='near_duplicate_cluster'?(item.near_duplicate_matches?.length||0)+1:1;$('result-feedback-impact').textContent=mode==='manual_accept'?`人工通过后：待复核 -1，有效数最多 +1。\n旧结果版本保持只读。`:mode==='manual_reject'?`人工淘汰后：待复核 -1，淘汰 +1。\n旧结果版本保持只读。`:mode==='rule'?`这会先生成规则草稿；确认草稿后才创建新版本并重验受影响素材。`:`预计排除 ${affected} 张；系统会立即重算候补和缺口。`;$('result-feedback-reason-choice').value=mode==='manual_accept'?'人工确认符合需求':'';$('result-feedback-reason').value='';$('submit-result-feedback').textContent=mode==='rule'?'生成规则草稿':'确认并生成新结果版本';$('result-feedback-dialog').showModal()}
async function excludeResultItem(taskId,results,item,scope,reason){const label=scope==='candidate'?'仅排除此图':'排除相似簇';const body={expected_task_version:results.task_version,result_set_digest:results.result_set_digest,candidate_id:item.candidate_id,scope,reason};const changed=await api(`/v1/retrieval/tasks/${taskId}/result-exclusions`,{method:'POST',keyValue:key('result-exclusion'),body});message('assistant',label,`已排除 ${changed.applied_exclusion.candidate_ids.length} 张；有效数 ${changed.accepted_count}，缺口 ${changed.gap}。旧结果版本保持历史只读。`);await openTask(taskId)}
async function reviewResultItem(taskId,results,item,action,reason){const changed=await api(`/v1/retrieval/tasks/${taskId}/manual-reviews`,{method:'POST',keyValue:key('manual-review'),body:{expected_task_version:results.task_version,result_set_digest:results.result_set_digest,candidate_id:item.candidate_id,asset_version:item.asset_version,action,reason}});message('assistant',action==='accept'?'人工通过':'人工淘汰',`裁决已写入新结果版本；有效 ${changed.accepted_count}，待复核 ${changed.pending_review_count}，缺口 ${changed.gap}。`);await openTask(taskId)}
async function promoteResultRule(taskId,item,reason){if(state.feedback){throw new Error('请先处理上一条未确认反馈')}const text=`以后所有候选都要遵守：${reason}（由结果素材 ${item.candidate_id} 的反馈升级）`;state.feedback=await api(`/v1/retrieval/tasks/${taskId}/feedback`,{method:'POST',keyValue:key('promote-result-rule'),body:{text,scope:'all',expected_revision:state.task.confirmed_revision,expected_task_version:state.task.task_version}});const bubble=message('assistant','升级为规则 · 等待确认',`规则草稿：“${reason}”。确认后才会创建新版本，并从历史原始候选池增量重算；当前结果不变。`);guidedAction(bubble,'确认创建新版本',()=>decideFeedback('confirm'));guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);state.feedbackReviewBubble=bubble;$('feedback-box').classList.add('hidden')}
async function submitResultFeedback(event){event.preventDefault();const pending=state.resultFeedback;if(!pending)return;const choice=$('result-feedback-reason-choice').value,note=$('result-feedback-reason').value.trim(),reason=[choice&&choice!=='custom'?choice:'',note].filter(Boolean).join('：');if(!reason){$('result-feedback-impact').textContent='请选择原因或填写判断依据。';return}$('result-feedback-dialog').close();if(typeof candidateDialog!=='undefined'&&candidateDialog.open)closeCandidateBrowser();setBusy(true);try{if(pending.mode==='exclude')await excludeResultItem(pending.taskId,pending.results,pending.item,pending.scope,reason);else if(pending.mode==='rule')await promoteResultRule(pending.taskId,pending.item,reason);else await reviewResultItem(pending.taskId,pending.results,pending.item,pending.mode==='manual_accept'?'accept':'reject',reason)}catch(error){$('status').textContent='操作未生效：'+error.message}finally{state.resultFeedback=null;setBusy(false)}}
function showResults(results,taskId,append=false){
  const list=$('result-list');
  if(!append)list.replaceChildren();else $('result-more')?.remove();
  if(!results.available){resetResultWorkspace();return}
  if(state.resultTaskId!==taskId){$('result-details').open=false;state.resultTaskId=taskId}
  $('result-sidebar').classList.remove('hidden');
  for(const button of document.querySelectorAll('[data-result-view]'))button.disabled=false;
  state.currentResults=results;state.resultView=results.view||state.resultView||'accepted';if(typeof syncResultTabs==='function')syncResultTabs(state.resultView);if(typeof renderResultCounts==='function')renderResultCounts(results);
  if(!append&&(results.group_progress||[]).length)card('分组进度',(results.group_progress||[]).map(row=>[row.label,`合格 ${row.accepted_count}/${row.target_count} · 缺口 ${row.gap}`]),[],list);
  for(const item of results.items){const libraries=[...new Set((item.sources||[]).map(source=>source.library_id).filter(Boolean))],actions=[];if(results.view==='accepted'){actions.push(['仅排除此图',()=>openResultFeedback('exclude',taskId,results,item,'candidate'),true],['升级为规则',()=>openResultFeedback('rule',taskId,results,item),true]);if(item.near_duplicate_matches?.length)actions.splice(1,0,['排除相似簇',()=>openResultFeedback('exclude',taskId,results,item,'near_duplicate_cluster'),true])}else if(results.view==='review'){actions.push(['人工通过',()=>openResultFeedback('manual_accept',taskId,results,item),false],['人工淘汰',()=>openResultFeedback('manual_reject',taskId,results,item),true])}const verdict=item.manual_review?`人工${item.manual_review.action==='accept'?'通过':'淘汰'}：${item.manual_review.reason}`:item.cleaning_status==='pass'?'全部硬规则通过':item.cleaning_status==='fail'?'未通过规则':'等待人工复核';const resultCard=card(`${resultViewLabels[results.view]||'结果'}素材`,[['素材标识',item.candidate_id],['素材版本',item.asset_version],['当前结论',verdict],['规则证据',(item.reasons||[]).map(reason=>`${reason.rule_id}：${reason.verdict==='pass'?'通过':reason.verdict==='fail'?'未通过':'待复核'}${reason.reason_summary?'（'+reason.reason_summary+'）':''}`).join('；')||'未保存逐规则原因'],...(item.user_exclusion?[['用户排除原因',item.user_exclusion.reason]]:[]),['来源',item.source_count?`${item.source_count} 条命中 · ${libraries.join('、')}`:'未记录来源'],['预览',item.preview_status==='available'?'每次读取重新校验授权':'当前不可读取']],actions,list);if(item.preview_status==='available'){const img=document.createElement('img');img.alt=`${resultViewLabels[results.view]||'结果'}素材 · ${item.candidate_id}`;img.loading='lazy';resultCard.insertBefore(img,resultCard.querySelector('.actions'));observeCleaningImage(img,taskId,item.candidate_id)}}
  if(!results.items.length&&!append)card(`暂无${resultViewLabels[results.view]||'当前分类'}素材`,[],[],list);
  if(results.next_cursor){card('更多结果',[],[['加载更多',()=>loadResultView(results.view,results.next_cursor,true),false]],list).id='result-more'}
  if(!append&&results.view==='accepted'&&results.counts.gap>0&&state.task?.task_id===taskId&&!state.task.pending_draft&&state.task.confirmed_revision===results.revision){
    if(state.task.run_status==='PARTIAL')offerGapExpansion(results,list);
    else if(['RUNNING','AWAITING_INPUT'].includes(state.task.run_status))offerRefillAction(results,taskId,list)
  }
}
function offerGapExpansion(results,list){const gap=results.counts.gap;card('有效数量不足',[['缺口',gap],['下一步','当前批准位置不可继续；可生成补充检索词候选，新版词表仍须由你确认。']],[['根据缺口补充检索词',()=>showPostTaskExpansion(`当前权威结果缺口 ${gap} 张，请保持原目标数量和硬约束，补充不同可见场景的检索词。`,{kind:'expansion',target_count:null,requested_terms_per_group:null,count_edits:[],expansion_instruction:`当前权威结果缺口 ${gap} 张；保留原主体、目标数量和硬约束，只补充有助于覆盖缺口的具体画面检索词。`},'system'),false]],list)}
async function offerRefillAction(results,taskId,list){const token=(state.refillOfferToken||0)+1;state.refillOfferToken=token;try{const plan=await api(`/v1/retrieval/tasks/${taskId}/refill`);if(state.refillOfferToken!==token||state.task?.task_id!==taskId||state.task.confirmed_revision!==results.revision)return;if(plan.action==='await_owner'&&plan.next_position){const position=plan.next_position;card('继续补数',[['缺口',plan.gap],...(plan.group_gaps||[]).filter(row=>row.gap).map(row=>[row.label+' 缺口',row.gap]),...(position.group_labels?.length?[['补充分组',position.group_labels.join('、')]]:[]),['检索词',position.query],['素材库',position.library_id],['位置',position.position.cursor||`深度 ${position.position.depth}`],['说明','沿用当前已确认检索词和范围，只确认启动这一轮。']],[['确认启动本轮补数',()=>confirmRefillIteration(taskId,plan),false]],list);return}if(plan.action==='wait'){card('补数进行中',[['轮次',plan.iteration],['缺口',plan.gap],['说明','已预留当前批准位置，正在等待检索回传。'],...refillRoundRows(plan.execution)],[],list);return}if(plan.action==='partial_delivery'){const execution=plan.execution||{},actions=[];if(results.counts.accepted>0)actions.push(['按当前有效素材部分交付',()=>openDelivery(taskId),false]);card('自动补数已停止',[['缺口',plan.gap],['停止原因',refillStopLabel(plan.stop_reason)],['已发轮次',execution.issued_rounds??'未知'],['已完成轮次',execution.completed_rounds??'未知'],['连续无进展',execution.stagnant_rounds??'未知'],['已预留 VLM 调用',execution.vlm_calls??'未知'],...refillRoundRows(execution),['下一步',results.counts.accepted>0?'可部分交付，或修改方案重新检索':'当前无有效素材，请修改方案重新检索']],actions,list);offerGapExpansion(results,list)}}catch(error){if(state.refillOfferToken!==token)return;if(error.code==='REFILL_NOT_CONFIGURED'){offerGapExpansion(results,list);return}card('补数状态读取失败',[['说明',error.message]],[],list)}}
async function confirmRefillIteration(taskId,plan){setBusy(true);try{const result=await api(`/v1/retrieval/tasks/${taskId}/refill`,{method:'POST',keyValue:`refill:${taskId}:${plan.next_position.position_fingerprint}`,body:{expected_revision:plan.revision,expected_generation:plan.generation,expected_task_version:plan.task_version,position_fingerprint:plan.next_position.position_fingerprint,action:'confirm'}});message('assistant',result.replayed?'补数轮次已存在':'补数轮次已启动',`第 ${result.iteration} 轮已按当前确认检索词提交。`);const [task,progress,results]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}`),api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`)]);state.task=task;showProgress(progress,results);showResults(results,taskId);refreshTaskSidebar()}catch(error){$('status').textContent=error.message}finally{setBusy(false)}}
async function loadMoreResults(taskId,cursor){setBusy(true);try{const results=await api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20&cursor=${encodeURIComponent(cursor)}`);showResults(results,taskId,true)}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function startIncrementalRecompute(taskId){setBusy(true);try{const proposal=await api(`/v1/retrieval/tasks/${taskId}/cleaning/prepare`,{method:'POST',body:{incremental_recompute:true}});const operation=await api(`/v1/retrieval/tasks/${taskId}/cleaning/confirm`,{method:'POST',keyValue:key('incremental-recompute-confirm'),body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});message('assistant','增量重算已启动',`历史原始候选 ${proposal.candidate_count} 张已绑定当前 revision；后台状态：${operation.status}。`);state.cleaningOperation=operation;pollVlmCleaning(taskId,operation.operation_id)}catch(error){$('status').textContent='增量重算未启动：'+(error.detail||error.message)}finally{setBusy(false)}}
async function openTask(taskId){const token=state.historyOpenToken=(state.historyOpenToken||0)+1,started=performance.now();setBusy(true);showHistoryLoading('正在打开生产线路…');$('status').textContent='正在打开生产线路…';try{const task=await api(`/v1/retrieval/tasks/${taskId}/summary`);if(token!==state.historyOpenToken)return false;resetTimeline('历史需求',task.raw_request||`任务 ${taskId}`);timeline.dataset.firstTextMs=String(performance.now()-started);const [revisions,progress,results,history]=await Promise.all([api(`/v1/retrieval/tasks/${taskId}/revisions`),task.progress||api(`/v1/retrieval/tasks/${taskId}/progress`),api(`/v1/retrieval/tasks/${taskId}/results?view=accepted&limit=20`),task.conversation_id?api(`/v1/retrieval/conversations/${task.conversation_id}`).catch(()=>null):null]);if(token!==state.historyOpenToken)return false;state.task=task;state.conversationId=task.conversation_id||null;localStorage.setItem('dsh-task-id',taskId);showProgress(progress,results);showResults(results,taskId);startTaskEvents(taskId,progress.as_of_sequence);for(const revision of revisions.items){const changes=(revision.changes||[]).map(item=>item.label+(item.field==='target_count'?`：${item.before} → ${item.after}`:'')).join('；')||'初始确认';card(`正式版本 ${revision.revision}`,[['需求',revision.proposal.query_spec.raw_request],['规则变化',changes],['版本隔离',revision.revision_note||'只读取本版本结果'],['方案摘要',revision.proposal_digest]]);showSavedGroups(revision.proposal.query_spec.grouped_expansion)}if(task.confirmed_revision>1)try{const recompute=await api(`/v1/retrieval/tasks/${taskId}/cleaning/recompute`);if(token!==state.historyOpenToken)return false;if(recompute.available){const report=recompute.report,counts=report?.counts;card('反馈后的增量重算',[['候选来源','历史原始输入清单（包含此前淘汰项）'],['规则变化',(recompute.rule_changes||[]).map(item=>`${item.rule_id}：${item.change}`).join('；')||'规则完全兼容'],['复用已有事实',recompute.reused_candidate_count],['需要重验',recompute.revalidated_candidate_count],['可恢复的旧淘汰项',recompute.restorable_rejected_count],['重验素材',(report?.revalidated_candidate_ids||recompute.candidates.filter(item=>item.action==='revalidate').map(item=>item.candidate_id)).join('、')||'无'],['计数变化',counts?`有效 ${counts.before.accepted??'待发布'} → ${counts.after.accepted??'待发布'}；通过 ${counts.before.pass} → ${counts.after.pass}；淘汰 ${counts.before.fail} → ${counts.after.fail}；待复核 ${counts.before.unknown} → ${counts.after.unknown}`:'新版本尚未发布，不沿用旧数量'],['版本栅栏',`revision ${recompute.publication_fence.revision} · generation ${recompute.publication_fence.generation} · version ${recompute.publication_fence.task_version}`],['说明',recompute.note]],report?[]:[['确认增量重算',()=>startIncrementalRecompute(taskId),false]])}}catch(error){if(token!==state.historyOpenToken)return false;if(!['CLEANING_NOT_CONFIGURED','DETECTOR_NOT_RELEASED'].includes(error.code))card('增量重算状态',[['说明',error.message]])}renderSavedTaskMessages([...(history?.clarification_messages||[]),...(history?.task_messages||[])]);renderExecutionHistory(history?.executions);if(task.pending_draft){state.feedback=task.pending_draft;const bubble=message('assistant','有一条未确认的反馈','“'+state.feedback.feedback_text+'”仍是草稿，尚未改变当前需求。');guidedAction(bubble,'重新理解并处理',()=>routeTaskFollowup(state.feedback.feedback_text));guidedAction(bubble,'重新描述',editFeedback,true);guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);state.feedbackReviewBubble=bubble}$('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');$('status').textContent='';refreshTaskSidebar();offerExistingSearch(taskId);refreshHistory();return true}catch(e){if(token===state.historyOpenToken)$('status').textContent=e.message;return false}finally{if(token===state.historyOpenToken)setBusy(false)}}
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
      if(cleaning?.available){message('assistant','VLM 清洗已完成','检索候选已进入清洗结果；可在右侧“清洗”节点查看每一步明细。');return}
      if(state.task.active_cleaning_operation_id){message('assistant','VLM 清洗进行中','检索候选已固定，后台正在执行已确认的清洗方案。');return}
    }
    $('status').textContent='检索进度暂不可读：'+error.message
  }
}
function guidedAction(bubble,label,handler,secondary=false){const box=bubble.querySelector('.actions')||document.createElement('div');box.className='actions';const button=document.createElement('button');button.textContent=label;if(secondary)button.className='secondary';button.addEventListener('click',handler);box.append(button);bubble.append(box);return button}
function showTermReview(proposal,intro='我建议用这些检索词：'){
  const version=proposal.version||1;
  const referenceDigest=state.queryImage?.digest||null,excludeExact=$('query-image-exclude')?.checked===true;
  let refill;try{refill=refillFormValue()}catch(error){$('status').textContent=error.message;return}
  const groups=proposal.plan?.groups||[];const quotaNote=groups.some(group=>group.delivery_target!=null)?'\n\n各组目标（每组分别计数，任一组不足都不算完成）：'+groups.map(group=>group.primary_label+' '+group.delivery_target+' 张').join('、'):groups.length>1?'\n\n未单独分配各组数量，将只按总数计数；需要分别计数请写明，例如“狗 20 张、猫 30 张”。':'';
  const referenceNote=state.queryImage?'\n\n参考图：已绑定不可变摘要 '+state.queryImage.digest.slice(0,19)+'…；图片向量用于召回，以上文字用于选库和清洗约束；'+(excludeExact?'会排除与参考图 SHA-256 完全相同的内容。':'不会自动排除参考图相同内容。'):'';
  const bubble=message('assistant','',intro+'\n'+proposal.lines+quotaNote+referenceNote+'\n\n补数策略：\n'+refillSummary(refill)+'\n\n可以继续修改设置；确认后按以上完整方案开始检索。');
  guidedAction(bubble,state.queryImage?'确认参考图方案并开始检索':'确认检索词并开始检索',async()=>{
    if(state.proposedExpansion!==proposal||state.task||version!==(proposal.version||1)){$('status').textContent='这组检索词已变化，请确认最新词表。';return}
    if(referenceDigest!==(state.queryImage?.digest||null)||excludeExact!==($('query-image-exclude')?.checked===true)){bubble.querySelector('.actions')?.remove();showTermReview(proposal,'参考图设置已变化，请确认最新完整方案：');return}
    await confirmGuidedExpansion(proposal,{value:proposal.lines,disabled:false},refill);
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
function expansionJobPath(session=state.dialogueId){return '/v1/retrieval/projects/demo/dialogue-sessions/'+encodeURIComponent(session)+'/expansion-jobs'}
function clearExpansionPolling(){if(state.expansionPollTimer)clearTimeout(state.expansionPollTimer);state.expansionPollTimer=null;state.expansionJob=null;state.expansionJobBubble=null}
async function startExpansionJob(body,signal){
  if(!state.dialogueId||state.dialogueStage!=='terms')throw new Error('请先完成需求理解，再生成检索词');
  const session=state.dialogueId,version=state.dialogueVersion;
  const job=await api(expansionJobPath(session),{method:'POST',signal,body:{dialogue_session_id:session,dialogue_version:version,...body}});
  if(signal?.aborted||state.dialogueId!==session||state.dialogueVersion!==version)return;
  watchExpansionJob(job);
}
function watchExpansionJob(job){
  if(state.task||state.dialogueId!==job.dialogue_session_id||state.dialogueVersion!==job.dialogue_version)return;
  if(state.expansionPollTimer)clearTimeout(state.expansionPollTimer);
  if(state.expansionJob?.job_id!==job.job_id)state.expansionJobBubble=null;
  state.expansionJob=job;
  if(!state.expansionJobBubble)state.expansionJobBubble=message('assistant','扩词作业','');
  const bubble=state.expansionJobBubble,line=bubble.querySelector('p');
  const node=$('task-sidebar-nodes')?.querySelector('.node-card .node-status');
  if(node)node.textContent=job.status==='failed'?'批次失败':job.status==='completed'?'待确认':`已生成 ${job.completed_terms}/${job.target_terms||'…'}`;
  if(job.status==='completed'&&job.result?.plan){
    line.textContent='后台扩词已完成，等待你确认词表。';
    if(!job.result.plan.groups.length||job.result.plan.groups.every(group=>['图片','图像','素材','数据'].includes(group.primary_label))){message('assistant','','要找什么主体或场景？例如“狗”或“客厅”。');return}
    if(state.proposedExpansion?.jobId!==job.job_id){
      const previous=state.proposedExpansion;
      const lines=job.result.plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
      state.proposedExpansion={raw:job.raw_request,count:job.target_count,plan:job.result.plan,lines,version:(previous?.version||0)+1,jobId:job.job_id};
      state.guidedPhase='expansion';renderPlanningWorkflow('expansion');saveDialogueSession();
      showTermReview(state.proposedExpansion,job.feedback_text?'已按你的建议调整，完整词表：':'我建议用这些检索词：');
    }
    return;
  }
  if(job.status==='failed'){
    const batch=job.failed_batch?`（${job.failed_batch.group} · 从第 ${job.failed_batch.offset+1} 个词起的本批）`:'';
    line.textContent=`扩词暂停${batch}：${job.error?.message||'本批未完成'}。已生成 ${job.completed_terms}/${job.target_terms||'待确定'} 个词；旧词表未改动。`;
    bubble.querySelector('.actions')?.remove();
    guidedAction(bubble,'重试失败批次',async()=>{try{const next=await api(expansionJobPath(job.dialogue_session_id)+'/'+job.job_id+'/retry',{method:'POST',body:{}});watchExpansionJob(next)}catch(error){$('status').textContent='重试失败：'+(error.detail||error.message)}});
    return;
  }
  line.textContent=`正在扩词中：已生成 ${job.completed_terms}/${job.target_terms||'待确定'} 个词。可以切换 Chat，返回后继续查看。`;
  bubble.querySelector('.actions')?.remove();
  state.expansionPollTimer=setTimeout(async()=>{try{const latest=await api(expansionJobPath(job.dialogue_session_id)+'/'+job.job_id);watchExpansionJob(latest)}catch(error){if(state.dialogueId===job.dialogue_session_id){line.textContent='扩词进度暂时读取失败：'+(error.detail||error.message);state.expansionPollTimer=setTimeout(()=>watchExpansionJob(job),4000)}}},2000);
}
async function restoreExpansionJob(){if(!state.dialogueId||state.dialogueStage!=='terms')return;try{const job=await api(expansionJobPath());if(state.dialogueId===job.dialogue_session_id)watchExpansionJob(job)}catch(error){if(error.code!=='EXPANSION_JOB_NOT_FOUND')$('status').textContent='扩词状态读取失败：'+(error.detail||error.message)}}
function saveDialogueSession(){if(state.dialogueId){sessionStorage.setItem('dsh-dialogue',JSON.stringify({id:state.dialogueId,proposal:state.proposedExpansion,models:{llm:$('llm-model').value,vlm:$('vlm-model').value}}));saveCurrentModelChoice('dialogue:'+state.dialogueId)}else sessionStorage.removeItem('dsh-dialogue')}
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
    if(saved.models)restoreModelChoice(readStoredModelChoice('dialogue:'+saved.id)||saved.models);
    timeline.replaceChildren();
    if(!current.messages.length){message('system','新建数据需求','告诉我想找什么；不清楚的地方我会先问，确认检索词后才开始。');await refreshHistory();$('request-text').focus();return}
    if(current.stage==='terms'&&saved.proposal){state.proposedExpansion=saved.proposal;state.guidedPhase='expansion'}
    for(const item of current.messages){if(item.role==='assistant')message('assistant','',item.text);else message('user','',item.text)}
    if(state.proposedExpansion)showTermReview(state.proposedExpansion);
    else if(current.stage==='terms'&&current.request.target_count){const bubble=message('assistant','','历史消息已恢复。需要检索词时，可重新生成待确认的词表。');guidedAction(bubble,'重新生成检索词',async()=>{setBusy(true);try{await prepareGuidedExpansion(current.request.raw_request,current.request.target_count)}catch(error){$('status').textContent='生成检索词失败：'+(error.detail||error.message)}finally{setBusy(false)}})}
    else if(!current.messages.some(item=>item.role==='assistant'))message('assistant','',current.summary+'。'+(current.question==='confirm_summary'?'对吗？':'请继续补充。'));
    await restoreExpansionJob();
    await refreshHistory();
  }catch(error){if(openToken!==state.historyOpenToken)return;$('status').textContent='历史 Chat 加载失败：'+(error.detail||error.message);resetTimeline('历史 Chat','历史消息暂时读取失败，点击左侧 Chat 可重试。')}
}
async function prepareTask(){
  const input=$('request-text'),text=input.value.trim();if(!text||state.requestController)return;
  if(state.task){$('status').textContent='当前任务已确认；请在下方继续对话或新建 Chat。';return}
  const llm=$('llm-model').value;if(!llm){message('assistant','','请先在左下角“模型设置”选择 LLM，我才能理解你的需求。');return}
  const selection={llm,vlm:$('vlm-model').value};
  input.value='';message('user','',text);setDialogueBusy(true);
  const controller=new AbortController();state.requestController=controller;$('stop-request')?.classList?.remove('hidden');
  const active=()=>state.requestController===controller&&!controller.signal.aborted;
  const adjustingTerms=state.dialogueStage==='terms'&&Boolean(state.proposedExpansion);
  const pending=message('assistant','', adjustingTerms?'正在理解检索词调整要求……':'正在理解需求……');pending?.setAttribute?.('role','status');
  let waitStage=adjustingTerms?'正在理解检索词调整要求':'正在理解需求',waitSeconds=0;
  const renderWait=()=>{const line=pending?.querySelector?.('p');if(line)line.textContent=`${waitStage}……已等待 ${waitSeconds} 秒`};
  const waitTimer=setInterval(()=>{waitSeconds+=1;renderWait()},1000);
  try{
    const proposal=state.proposedExpansion;
    if(proposal&&state.targetCountTouched)throw new Error('词表已生成；如需改变图片数量，请直接在对话中说明');
    const hint=state.targetCountTouched&&$('target-count').value?Number($('target-count').value):null;
    if(hint!==null&&(!Number.isInteger(hint)||hint<1||hint>10000))throw new Error('目标数量须为 1 到 10000 张图片');
    const currentGroups=proposal?groupInputBody(proposal.raw,proposal.lines).groups:null;
    const turn=await api('/v1/retrieval/projects/demo/dialogue-turn',{method:'POST',signal:controller.signal,body:{session_id:state.dialogueId,expected_version:state.dialogueVersion,message:text,llm_id:llm,phase:state.dialogueStage,count_hint:hint,generate_terms_when_complete:true,...(currentGroups?{current_groups:currentGroups,current_target_count:proposal.count}:{})}});
    if(!active())return;
    state.targetCountTouched=false;
    state.dialogueId=turn.session_id;state.dialogueVersion=turn.version;state.dialogueStage=turn.stage;saveDialogueSession();
    if(typeof refreshHistory==='function')refreshHistory();
    if(!active())return;
    if(turn.action==='answer'||turn.action==='clarify'){message('assistant','',turn.reply);return}
    if(turn.action==='confirm'&&turn.request.media_type==='image'&&turn.request.target_count){
      waitStage='正在生成检索词（当前模型可能需要 1–2 分钟）';waitSeconds=0;renderWait();
      await prepareGuidedExpansion(turn.request.raw_request,turn.request.target_count,controller.signal,selection);return;
    }
    if(turn.stage==='terms'&&!proposal&&turn.request.target_count){
      waitStage='正在生成检索词（当前模型可能需要 1–2 分钟）';waitSeconds=0;renderWait();
      await prepareGuidedExpansion(turn.request.raw_request,turn.request.target_count,controller.signal,selection);return;
    }
    if(turn.action==='confirm_terms'&&proposal){showTermReview(proposal,'检索词还需要你亲自确认：');return}
    if(turn.action==='revise_terms'&&proposal){waitStage='正在扩词中（当前模型可能需要 1–2 分钟）';waitSeconds=0;renderWait();await reviseGuidedExpansion(proposal,{value:proposal.lines},{value:text},turn,selection,controller.signal);return}
    if(turn.action==='replace'){state.proposedExpansion=null;state.guidedPhase='input';state.resumeDraft=null;state.draft=null;saveDialogueSession()}
    if(turn.stage==='request'&&proposal){state.proposedExpansion=null;state.guidedPhase='input';saveDialogueSession()}
    message('assistant','',turn.reply);
  }catch(e){
    if(state.requestController!==controller)return;
    if(e.name==='AbortError'){$('status').textContent='已暂停等待，可以修改后重新发送。';message('assistant','','已暂停等待。原消息已放回输入框，可以修改后重新发送。');if(!input.value.trim())input.value=text;return}
    const explanation=(e.code==='DSH_TIMEOUT'?(waitStage.includes('扩词')||waitStage.includes('生成检索词')?'扩词 Agent 等待超时，尚未生成可用的新词表；原词表已保留，可重试。':'需求理解模型响应超时，这条消息已保留，未创建任务。'):
      e.code==='MODEL_OUTPUT_INVALID'?(e.detail||'这次生成的内容未通过校验，这条消息已保留。'):
      e.code==='MODEL_REQUEST_FAILED'?'模型服务暂时没有返回可用结果，这条消息已保留。':
      '这次消息没有处理成功：'+(e.detail||e.message));
    $('status').textContent=explanation;
    const bubble=message('assistant','这次没有提单',explanation+'你可以直接重试。');
    if(!input.value.trim())input.value=text;
    guidedAction(bubble,'重试这条消息',()=>prepareTask());
  }
  finally{clearInterval(waitTimer);pending?.remove?.();if(state.requestController===controller){state.requestController=null;$('stop-request')?.classList?.add('hidden');setDialogueBusy(false)}}
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
    await startExpansionJob({raw_request:proposal.raw,dimension_id:'default',target_count:count,model_selection:{llm,vlm},feedback_text:instruction,current_groups:current,count_edits:edits,current_target_count:proposal.count},signal);
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
async function confirmGuidedExpansion(proposal,editor,confirmedRefill){
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
    const path=state.resumeDraft?'/v1/retrieval/projects/demo/drafts/'+state.resumeDraft.draft_id:'/v1/retrieval/projects/demo/drafts';
    if(state.resumeDraft)body.expected_draft_version=state.resumeDraft.draft_version;
    state.draft=await api(path,{method:'POST',keyValue:key('draft'),body});
    state.conversationId=state.draft.conversation_id;
    state.resumeDraft={draft_id:state.draft.draft_id,draft_version:state.draft.draft_version};
    state.task=await api('/v1/retrieval/tasks',{method:'POST',keyValue:key('task'),body:{draft_id:state.draft.draft_id,expected_draft_version:state.draft.draft_version,proposal_digest:state.draft.proposal_digest,action:'confirm'}});
    if(state.dialogueId){await api(`/v1/retrieval/projects/demo/dialogue-sessions/${state.dialogueId}/archive`,{method:'POST',body:{task_id:state.task.task_id}}).catch(()=>{});state.dialogueId=null}
    sessionStorage.removeItem('dsh-dialogue');
    localStorage.setItem('dsh-task-id',state.task.task_id);
    await refreshTaskSidebar();
    state.guidedPhase='search';state.proposedExpansion=null;editor.disabled=true;retireDraftActions();
    $('new-request').classList.add('hidden');$('feedback-box').classList.remove('hidden');
    $('status').textContent='需求已保存 · 等待检索启动';
    await refreshHistory();
    setBusy(false);
    if(state.task.search_enqueue?.status==='queued')await guidedTick();else await guidedStartSearch();
  }catch(e){
    const explanation=e.code==='FEATURE_NOT_SUPPORTED'?'页面还是旧版本，请刷新后再次确认检索词。词表仍在，检索没有启动。':state.task?'需求已保存；后续状态读取失败，请刷新状态确认检索进度。':'暂时没能开始检索。请刷新页面后再确认一次检索词。';
    $('status').textContent=explanation;message('assistant',state.task?'需求已保存':'需求尚未保存',explanation);
  }
  finally{setBusy(false)}
}
async function guidedStartSearch(){
  if(!state.task)return;
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
  if(!state.task)return;const taskId=state.task.task_id;setBusy(true);
  try{const proposal=await api(`/v1/retrieval/tasks/${taskId}/first-search/manifest`);if(state.task?.task_id!==taskId)return;
    const incremental=Boolean(state.searchSnapshot?.results?.available);
    if(!proposal.candidate_count){message('assistant','候选清单暂不可确认','当前没有“已映射且内容摘要完整”的候选；不能用原始路径或未校验素材进入清洗。');return}
    const afterConfirm=manifest=>{state.candidateManifest=manifest;message('assistant',incremental?'本轮新增候选已固定':'候选清单已固定',`已固定 ${manifest.candidate_count} 条待清洗候选；尚未启动清洗。`);if(incremental)card('增量 VLM 将累计合并',[['本轮新增',manifest.candidate_count],['说明','本轮判定发布时会校验前序结果血缘，按内容摘要跨批去重，保留上一批结果并重新计算缺口。']],[]);offerVlmCleaning(manifest)};
    if(proposal.confirmed||proposal.kind==='candidate_input_manifest'){afterConfirm(proposal);await refreshTaskSidebar();return}
    card(incremental?'补数候选清单确认卡 · 尚未清洗':'候选清单确认卡 · 尚未清洗',[['已校验候选',proposal.candidate_count],['输入摘要',proposal.proposal_digest],['说明',proposal.note]],[['确认候选清单',async()=>{setBusy(true);try{const manifest=await api(`/v1/retrieval/tasks/${taskId}/first-search/manifest`,{method:'POST',keyValue:`candidate-manifest:${proposal.proposal_digest}`,body:{proposal_digest:proposal.proposal_digest,expected_task_version:proposal.expected_task_version,action:'confirm'}});afterConfirm(manifest);await refreshTaskSidebar()}catch(error){$('status').textContent=error.message}finally{setBusy(false)}} ,false]])
  }catch(error){$('status').textContent=error.message}finally{setBusy(false)}
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
  card('VLM 清洗确认卡 · 尚未执行',fields,[['确认并开始 VLM 清洗',()=>{if(dismissed){$('status').textContent='此卡已取消，请重新生成确认卡。';return}return confirmVlmCleaning(proposal,taskId,confirmationKey)},!model||!execution?.instruction],['暂不执行',cancel,false],['修改清洗条件',()=>{cancel();$('request-text').value='修改清洗条件：';$('request-text').focus();message('assistant','请补充需要修改的条件','修改条件需生成并确认新需求版本；当前卡不会执行。')},false]])
}
async function confirmVlmCleaning(proposal,taskId,confirmationKey){
  if(!state.task||!proposal||state.task.task_id!==taskId||proposal.task_id!==taskId){$('status').textContent='清洗卡不属于当前任务，请重新打开对应任务。';return}setBusy(true);
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
async function routeTaskFollowup(text){
  if(!state.task||state.followupBusy)return;
  const llm=$('llm-model').value;if(!llm){$('status').textContent='请先选择 LLM，让模型判断这条消息要修改检索还是清洗';return}
  const taskId=state.task.task_id,taskVersion=state.task.task_version;
  const selection={llm,vlm:$('vlm-model').value};setFollowupBusy(true);
  try{
    const preview=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===taskVersion?state.expansionPreviewPlan:null;
    if(state.followupContext?.taskId!==taskId||state.followupContext?.version!==taskVersion)state.followupContext={taskId,version:taskVersion,messages:[]};
    const intent=await api(`/v1/retrieval/tasks/${taskId}/followup-intent`,{method:'POST',body:{text,llm_id:llm,expected_revision:state.task.confirmed_revision,expected_task_version:taskVersion,recent_messages:state.followupContext.messages.slice(-20),...(preview?{draft_preview:{groups:preview.plan.groups.map(group=>({primary_label:group.primary_label,auxiliary_terms:group.auxiliary_terms})),target_count:preview.count}}:{})}});
    if(state.task?.task_id!==taskId||state.task.task_version!==taskVersion)return;
    state.followupContext.messages.push({role:'user',text},...(intent.reply?[{role:'assistant',text:intent.reply}]:[]));
    state.followupContext.messages=state.followupContext.messages.slice(-20);
    if(intent.kind==='new_request'){
      message('user','',text);
      const bubble=message('assistant','这是新的检索需求','需要新建需求单；当前需求会继续在后台检索，不会被这条消息改动。');
      guidedAction(bubble,'新建 Chat 处理',()=>{sessionStorage.setItem('dsh-new-request-prefill',text);startNewChat()});
      $('feedback-text').value='';
      return;
    }
    if(intent.kind==='expansion'){await showPostTaskExpansion(text,intent,'user',selection);return}
    if(intent.kind==='answer'||intent.kind==='clarify'){message('user','',text);message('assistant','',intent.reply||'你希望调整哪些内容？');return}
    if(state.feedback){
      if(state.feedback.feedback_text===text){await showCleaningSuggestion(selection);return}
      $('status').textContent='请先处理上一条未确认反馈，再提交新的清洗条件。';return;
    }
    message('user','',text);
    state.feedback=await api(`/v1/retrieval/tasks/${state.task.task_id}/feedback`,{method:'POST',keyValue:key('feedback'),body:{text,scope:'all',expected_revision:state.task.confirmed_revision,expected_task_version:state.task.task_version}});
    $('feedback-box').classList.add('hidden');
    await showCleaningSuggestion(selection);
  }catch(e){if(state.task?.task_id===taskId){const detail=e.detail||e.message;$('status').textContent='尚未理解这条消息：'+detail;message('assistant','这条消息未处理',detail+'。原需求和词表未改变，可在模型恢复后重试。')}}
  finally{setFollowupBusy(false)}
}
function taskExpansionPath(taskId){return '/v1/retrieval/tasks/'+encodeURIComponent(taskId)+'/expansion-jobs'}
function clearTaskExpansionPolling(){if(state.taskExpansionPollTimer)clearTimeout(state.taskExpansionPollTimer);state.taskExpansionPollTimer=null;state.taskExpansionJob=null;state.taskExpansionBubble=null}
function watchTaskExpansionJob(job){
  if(state.task?.task_id!==job.task_id||state.task.task_version!==job.task_version||state.task.confirmed_revision!==job.revision)return;
  if(state.taskExpansionPollTimer)clearTimeout(state.taskExpansionPollTimer);
  if(state.taskExpansionJob?.job_id!==job.job_id)state.taskExpansionBubble=null;
  state.taskExpansionJob=job;
  if(!state.taskExpansionBubble)state.taskExpansionBubble=message('assistant','重新扩词','');
  const status=state.taskExpansionBubble.querySelector('p');
  if(job.status==='completed'&&job.result?.plan){
    status.textContent=job.result.note?.includes('按用户逐字指定')
      ?'明确词表修改已完成：逐字保留你指定的词，未调用扩词模型；等待你确认新版词表。'
      :job.result.note?.includes('未调用扩词模型')
        ?'词数调整已完成：按原词表顺序保留指定数量，未调用扩词模型；等待你确认新版词表。'
        :'后台扩词已完成，等待确认新版词表。';
    if(state.expansionPreviewPlan?.jobId===job.job_id)return;
    if(!job.result.plan.groups.length){status.textContent='扩词结果为空，请重试。';return}
    const taskId=job.task_id,baseVersion=job.task_version,count=job.target_count,plan=job.result.plan;
    const feedbackText=job.confirmation_feedback_text||job.feedback_text;
    const lines=plan.groups.map(group=>group.primary_label+' | '+group.auxiliary_terms.join('，')).join('\n');
    if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();
    const token=key('expansion-preview');state.expansionPreviewToken=token;
    state.expansionPreviewPlan={taskId,baseVersion,plan,count,requestedTerms:job.requested_terms_per_group,feedbackText,jobId:job.job_id,sourceGroups:job.source_groups};
    const bubble=message('assistant','重新扩词结果','目标 '+count+' 张图\n'+lines);state.expansionPreviewBubble=bubble;
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
    const batch=job.failed_batch?`（${job.failed_batch.group} · 从第 ${job.failed_batch.offset+1} 个词起的本批）`:'';
    status.textContent=`上一次重新扩词未完成${batch}：${job.error?.message||'本批未完成'}。已生成 ${job.completed_terms}/${job.target_terms||'待确定'} 个词；原正式版本未改变。`;
    state.taskExpansionBubble.querySelector('.actions')?.remove();
    if(job.error?.code==='EXPANSION_UNCHANGED'){
      guidedAction(state.taskExpansionBubble,'重新提交这条修改',()=>{$('feedback-text').value=(job.confirmation_feedback_text||job.feedback_text||'').split('后续调整：').at(-1).trim();$('feedback-text').focus()});
    }else{
      guidedAction(state.taskExpansionBubble,'重试失败批次',async()=>{try{watchTaskExpansionJob(await api(taskExpansionPath(job.task_id)+'/'+job.job_id+'/retry',{method:'POST',body:{}}))}catch(error){$('status').textContent='重试失败：'+(error.detail||error.message)}});
    }
    return;
  }
  status.textContent=`正在扩词中：已生成 ${job.completed_terms}/${job.target_terms||'待确定'} 个词。切换页面后仍会继续。`;
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
    message(displayRole,'',text);
    const revisions=await api(`/v1/retrieval/tasks/${taskId}/revisions`);
    const revision=revisions.items.find(item=>item.revision===state.task.confirmed_revision),spec=revision?.proposal?.query_spec;
    if(!spec)throw new Error('当前需求版本不可读');
    let previous=state.expansionPreviewPlan?.taskId===taskId&&state.expansionPreviewPlan?.baseVersion===baseVersion?state.expansionPreviewPlan:null;
    if(!previous&&state.feedback?.feedback_text&&state.feedback.feedback_text!==text){
      const earlier=await api(`/v1/retrieval/tasks/${taskId}/followup-intent`,{method:'POST',body:{text:state.feedback.feedback_text,llm_id:llm,expected_revision:state.task.confirmed_revision,expected_task_version:baseVersion}});
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
    $('feedback-text').value='';
  }catch(error){if(state.task?.task_id===taskId){const detail=error.detail||error.message;$('status').textContent='重新扩词未完成：'+detail;message('assistant','重新扩词未完成',detail+'。原正式版本未改变。')}}
  finally{if(ownsBusy)setFollowupBusy(false)}
}
async function decideFeedback(action,expansion=false){setBusy(true);try{const result=await api(`/v1/retrieval/tasks/${state.task.task_id}/confirmations`,{method:'POST',keyValue:key(action),body:{draft_id:state.feedback.draft_id,expected_draft_version:state.feedback.draft_version,proposal_digest:state.feedback.proposal_digest,expected_task_version:state.feedback.base_task_version,action}});state.task=result.task;state.feedback=null;if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.querySelector('.actions')?.remove();if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.querySelector('.actions')?.remove();if(state.expansionPreviewBubble?.isConnected)state.expansionPreviewBubble.querySelector('.actions')?.remove();state.expansionPreviewToken=null;state.expansionPreviewPlan=null;await refreshTaskSidebar();message('assistant',action==='confirm'?(expansion?'新版词表已确认':'反馈已确认'):'已忽略这条反馈',action==='confirm'?(expansion?'已保存为同一需求 ID 的新版本；正在核对全部检索词的索引和编码能力，通过后才会启动检索。':'需求已更新为新版本；当前尚未创建或执行清洗管线。'):'原需求保持不变。');$('feedback-text').value='';$('feedback-box').classList.remove('hidden');await refreshHistory();setBusy(false);if(action==='confirm'&&expansion){if(result.search_enqueue?.status==='queued')await guidedTick();else await guidedStartSearch()}}catch(e){$('status').textContent=e.message}finally{setBusy(false)}}
async function editFeedback(){
  const text=state.feedback?.feedback_text||'';
  await decideFeedback('reject');
  if(state.feedback)return;
  $('feedback-text').value=text;$('feedback-text').focus();
}
function showFeedbackReview(suggestion){
  if(state.feedbackReviewBubble?.isConnected)state.feedbackReviewBubble.remove();
  if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();
  $('feedback-box').classList.add('hidden');
  const sentence=text=>{const copy=text.trim().replace(/^请用户/, '请你').replace(/^建议用户/, '建议你');return /[。！？!?]$/.test(copy)?copy:copy+'。'};
  let bubble;
  if(!suggestion){
    bubble=message('assistant','请审阅这条反馈','模型暂时无法判断这条反馈是否形成清洗条件。你可以重新描述，或手动核对完整需求后确认；目前没有自动执行。');
    guidedAction(bubble,'查看完整需求',()=>{if(state.feedbackManualCard?.isConnected)state.feedbackManualCard.remove();state.feedbackManualCard=card('完整反馈草稿 · 手动确认',[['原反馈',state.feedback.feedback_text],['完整需求',state.feedback.proposal.query_spec.raw_request],['执行说明',state.feedback.execution_note]],[['仅保存反馈文本',()=>decideFeedback('confirm'),false],['不采用',()=>decideFeedback('reject'),true]])});
  }else if(suggestion.feedback_status==='ready'){
    bubble=message('assistant','我理解你的反馈','我建议把这条反馈理解为：“'+suggestion.requirement+'”。'+sentence(suggestion.rationale)+'目前确认只保存你的反馈原文；这条模型建议尚未写入清洗规则，也不会自动执行。');
    guidedAction(bubble,'仅保存反馈文本',()=>decideFeedback('confirm'));
  }else{
    const unrelated=suggestion.feedback_status==='unrelated';
    bubble=message('assistant',unrelated?'这条反馈还不能用于清洗':'请再补充一点',sentence(suggestion.rationale)+'当前需求不会改变。');
  }
  guidedAction(bubble,'重新描述',editFeedback,true);
  guidedAction(bubble,'不采用',()=>decideFeedback('reject'),true);
  state.feedbackReviewBubble=bubble;
}
async function showCleaningSuggestion(selection){
  const taskId=state.task?.task_id;if(!taskId)return;
  const llm=selection?.llm??$('llm-model').value,vlm=selection?.vlm??$('vlm-model').value;
  if(!llm||!vlm){showFeedbackReview(null);return}
  try{
    const suggestion=await api(`/v1/retrieval/tasks/${taskId}/feedback-suggestions`,{method:'POST',body:{draft_id:state.feedback.draft_id,expected_draft_version:state.feedback.draft_version,proposal_digest:state.feedback.proposal_digest,model_selection:{llm,vlm}}});
    if(state.task?.task_id!==taskId)return;
    showFeedbackReview(suggestion);
  }catch(e){if(state.task?.task_id!==taskId)return;showFeedbackReview(null);$('status').textContent='模型暂时无法判断：'+e.message}
}
$('preview-branches').addEventListener('click',previewBranches);
$('preview-routing').addEventListener('click',previewRouting);
$('first-search').addEventListener('click',previewFirstSearch);
$('first-search-candidates').addEventListener('click',viewFirstSearchCandidates);
function startNewChat(){
  cancelDialogueRequest();
  // An unsent composer is not a historical conversation. Repeated clicks
  // simply return focus to the same empty workspace.
  if(!state.task&&!state.dialogueId&&!state.conversationId){$('request-text').focus();return}
  if(typeof stopTaskEvents==='function')stopTaskEvents();
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
    $('refill-library-status').textContent=available?'只列出“已有索引配置”中当前可用的素材库；可多选。':'尚未配置可用于补数的素材库，请先到“已有索引配置”保存范围。';
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
function readStoredModelChoice(scope=modelChoiceScope()){try{const value=JSON.parse(localStorage.getItem('dsh-model-choice:'+scope)||'null');return value&&typeof value.llm==='string'&&typeof value.vlm==='string'?value:null}catch{return null}}
function saveCurrentModelChoice(scope=modelChoiceScope()){localStorage.setItem('dsh-model-choice:'+scope,JSON.stringify({llm:$('llm-model').value,vlm:$('vlm-model').value}))}
$('prepare-task').addEventListener('click',prepareTask);$('request-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareTask()}});$('prepare-feedback').addEventListener('click',prepareFeedback);$('feedback-text').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();prepareFeedback()}});$('new-task').addEventListener('click',startNewChat);$('refresh-history').addEventListener('click',refreshHistory);$('save-api-config').addEventListener('click',saveApiConfig);document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();startNewChat()}});
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
  $('feedback-model-note').textContent=enabled?'发送后先判断是调整检索方案还是筛选已找到的图片。':'请先选择 LLM 以理解后续消息。';
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
  }catch(e){$('model-status').textContent='模型目录加载失败：'+e.message;updateModelMode()}
}
async function loadBuildVersion(){
  try{
    const build=await api('/v1/build');
    const commit=build.commit?build.commit.slice(0,10):'未知';
    const source=build.source_clean===true?'已提交':build.source_clean===false?'存在未提交修改':'源码状态未知';
    const engine=build.model_engine==='dsh'?'DSH':build.model_engine==='direct'?'直连模型':'未配置模型';
    const retrieval=build.retrieval_mode==='LOCAL_CLIP'?'本地真实 CLIP 测试索引':
      (build.retrieval_mode==='local_fixture'||build.retrieval_mode==='LOCAL_EXACT')?
      '本地测试索引':'未配置检索';
    $('build-version').textContent=`版本 ${commit} · ${source} · ${engine} · ${retrieval} · 启动 ${build.started_at||'未知'}`;
  }catch(error){$('build-version').textContent='运行版本暂不可读';}
}
(async function initialize(){setHistoryOpen(false);renderPlanningWorkflow('request');resetResultWorkspace();syncRefillFields();void loadBuildVersion();void loadRefillLibraries();void (async()=>{await loadModels();await loadModelSettings().catch(error=>{$('api-config-status').textContent=error.message});const savedModels=readStoredModelChoice();if(savedModels&&!state.modelChoiceTouched)restoreModelChoice(savedModels)})();await refreshHistory();const linkedTask=new URLSearchParams(location.search).get('task');if(linkedTask)history.replaceState(null,'','/chat');const taskId=/^rt_[a-f0-9]{32}$/.test(linkedTask||'')?linkedTask:localStorage.getItem('dsh-task-id');if(taskId){if(!await openTask(taskId))localStorage.removeItem('dsh-task-id')}else await restoreDialogueSession();if(!state.task){const prefill=sessionStorage.getItem('dsh-new-request-prefill');if(prefill){sessionStorage.removeItem('dsh-new-request-prefill');$('request-text').value=prefill;$('request-text').focus()}}})();
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
