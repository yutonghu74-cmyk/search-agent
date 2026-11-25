"""One read-only workflow interpretation for dialogue and the task sidebar.

Inputs are current, authorized business projections. Raw hits never become
accepted counts, nor does a successful search imply a published delivery.
"""
from copy import deepcopy
import re

from .query_spec import digest


def binding(task):
    return {'task_id': task['task_id'], 'revision': task['confirmed_revision'],
            'generation': task['run_generation'], 'task_version': task['task_version']}


def current_source(source, task, *, allow_unapproved=False):
    if not isinstance(source, dict) or source.get('task_id') != task['task_id']:
        return False
    if allow_unapproved and source.get('status') == 'unapproved' and 'revision' not in source:
        return True
    return all(source.get(key) == value for key, value in binding(task).items())


def branch_counts(search):
    groups = {}
    for member in search.get('members', []):
        groups.setdefault(member.get('branch_id') or member.get('plan_id'), []).append(member)
    counts = dict(success=0, failed=0, running=0, waiting=0, total=len(groups))
    for members in groups.values():
        done = lambda row: bool(row.get('done') or row.get('status') == 'stopped' or
                               row.get('stop_reason') and row['stop_reason'] not in ('more_available', 'retry_pending'))
        failed = lambda row: bool(row.get('last_error') or row.get('status') in ('failed', 'error')
                                  or row.get('stop_reason') in ('failed', 'error'))
        if all(done(row) for row in members):
            counts['failed' if any(failed(row) for row in members) else 'success'] += 1
        elif search.get('job_status') == 'failed':
            counts['failed'] += 1
        elif any(row.get('pages') or row.get('committed_pages') or row.get('status') in ('pending', 'more_available') for row in members):
            counts['running'] += 1
        else:
            counts['waiting'] += 1
    return counts


def project_workflow(task, progress, *, search=None, manifest=None, cleaning=None, errors=(), capabilities=None,
                     execution_job=None):
    """Pure projection; no mapping, writes, confirmations or model calls."""
    nodes = deepcopy(progress['nodes'])
    indexed = {row['node']: row for row in nodes}
    counts = deepcopy(progress['counts'])
    actions = []
    reasons = list(errors)
    labels = {'pending': '未开始', 'running': '进行中', 'succeeded': '已完成',
              'failed': '失败', 'cancelled': '已取消'}
    for node in nodes:
        node['display_status'] = ('待发布' if node['node'] == 'packaging' and node['gate_status'] == 'awaiting_approval'
                                  else labels.get(node['node_status'], '状态未知'))
    def action(identifier, label, node, kind='view'):
        item = {'id': identifier, 'label': label, 'node': node, 'kind': kind}
        if item not in actions:
            actions.append(item)
    def set_node(name, status, display, detail, gate=None):
        node = indexed[name]
        node.update(node_status=status, display_status=display, detail=detail)
        if gate is not None:
            node['gate_status'] = gate

    scoped_search = search if current_source(search, task, allow_unapproved=True) else None
    if search is not None and scoped_search is None:
        reasons.append({'source': 'retrieval', 'code': 'STALE_SOURCE'})
    scoped_manifest = manifest if current_source(manifest, task) and manifest.get('confirmed') else None
    if manifest is not None and scoped_manifest is None:
        reasons.append({'source': 'manifest', 'code': 'STALE_SOURCE'})
    if indexed['expansion']['node_status'] == 'running':
        set_node('expansion', 'succeeded', '已完成', '检索词方案已确认。', 'approved')
    retrieval = indexed['retrieval']
    if scoped_search is not None and scoped_search.get('status') == 'approved':
        branches = branch_counts(scoped_search)
        if (scoped_search.get('execution_budget') or {}).get('stop_reason'):
            set_node('retrieval', 'failed', '预算已用尽', '本任务检索预算已用尽，已停止继续召回；可查看已有结果。')
        elif scoped_search.get('execution_queue', {}).get('state') == 'failed':
            set_node('retrieval', 'failed', '检索已停止', '检索执行已停止，请查看记录后重试。')
        elif scoped_search.get('job_status') == 'failed':
            set_node('retrieval', 'failed', '检索失败', '检索作业失败，请查看检索结果与失败记录。')
        elif scoped_search.get('job_status') == 'superseded':
            set_node('retrieval', 'pending', '已替代', '此检索作业已被替代，请核对当前方案。')
        elif scoped_search.get('pagination_stopped'):
            set_node('retrieval', 'succeeded', '部分分支失败' if branches['failed'] else '检索已结束',
                     '检索已结束；候选清单已确认。' if scoped_manifest else '检索已结束，候选清单尚未确认。',
                     'approved' if scoped_manifest else 'awaiting_approval')
        elif scoped_search.get('initial_window_complete'):
            set_node('retrieval', 'running', '首轮候选已就绪', '首轮检索已返回，后续位置留给补数。')
        elif scoped_search.get('execution_queue', {}).get('state') == 'queued':
            set_node('retrieval', 'running', '排队中', '正在等待检索执行槽位。')
        else:
            set_node('retrieval', 'running', '检索中', '检索正在进行。')
    elif scoped_search is not None and scoped_search.get('status') == 'unapproved' and retrieval['node_status'] != 'succeeded':
        set_node('retrieval', 'pending', '未开始', '检索尚未启动。')
    elif retrieval['node_status'] == 'running' and not scoped_manifest:
        set_node('retrieval', 'pending', '状态待核对', '当前检索状态暂时无法核对。')
    if scoped_manifest:
        set_node('retrieval', 'succeeded', '已完成', '检索已结束，候选清单已确认。', 'approved')

    if cleaning and cleaning.get('status') in ('queued', 'running', 'retry_wait'):
        label, detail = {'queued': ('排队中', '清洗已批准，等待后台执行。'),
                         'running': ('进行中', '清洗正在进行。'),
                         'retry_wait': ('等待重试', '清洗等待后台重试。')}[cleaning['status']]
        set_node('cleaning', 'running', label, detail)
    elif cleaning and cleaning.get('status') == 'failed':
        set_node('cleaning', 'failed', '失败', '清洗未完成，请查看失败原因；失败不计入合格结果。')

    run_status = task['run_status']
    stage, summary = 'unknown', '当前执行进度暂时无法核对，请刷新状态。'
    packaging = indexed['packaging']
    clean = indexed['cleaning']
    if run_status in ('CANCELLED', 'PAUSED'):
        stage = 'cancelled' if run_status == 'CANCELLED' else 'paused'
        summary = '任务已取消。' if stage == 'cancelled' else '任务已暂停。'
        for row in nodes:
            if row['node_status'] == 'running':
                row['display_status'] = '已取消' if stage == 'cancelled' else '已暂停'
    elif packaging['node_status'] == 'succeeded' and packaging['gate_status'] == 'approved' and run_status in ('COMPLETED', 'PARTIAL'):
        stage = 'published'
        summary = '部分交付已发布。' if run_status == 'PARTIAL' else '交付已发布。'
        action('view_delivery', '查看交付', 'packaging')
    elif run_status == 'FAILED':
        stage, summary = 'failed', '任务运行失败，请查看失败节点的记录。'
        failed = next((row['node'] for row in nodes if row['node_status'] == 'failed'), 'retrieval')
        identifier = {'cleaning': 'view_cleaning', 'refill': 'view_refill', 'packaging': 'view_delivery'}.get(failed, 'view_retrieval')
        action(identifier, '查看失败记录', failed)
    elif packaging['gate_status'] == 'awaiting_approval':
        stage, summary = 'awaiting_publication', '待发布包已生成，等待你确认发布。'
        action('view_delivery', '查看待发布包', 'packaging')
    elif clean['node_status'] in ('running', 'failed'):
        stage = 'cleaning' if clean['node_status'] == 'running' else 'cleaning_failed'
        summary = (clean['detail'] if cleaning else
                   '当前处于清洗阶段。' if stage == 'cleaning' else '清洗未完成，请查看失败原因。')
        action('view_cleaning', '查看清洗结果', 'cleaning')
    elif scoped_search and (scoped_search.get('execution_budget') or {}).get('stop_reason'):
        stage, summary = 'retrieval_budget_exhausted', '本任务检索预算已用尽，已停止继续召回；已有结果仍可查看。'
        action('view_retrieval', '查看已有检索结果', 'retrieval')
    elif str(task.get('stage', '')).upper() == 'REFILL' and run_status == 'RUNNING':
        ready = scoped_search and scoped_search.get('job_status') not in ('failed', 'superseded') and (scoped_search.get('pagination_stopped') or scoped_search.get('initial_window_complete'))
        if ready and not scoped_manifest:
            stage, summary = 'awaiting_candidates', '补检索已返回，新增候选清单尚未确认。'
            action('confirm_candidates', '确认新增候选清单', 'retrieval', 'confirm')
        else:
            stage, summary = 'refill', '当前处于补数阶段。' + indexed['refill']['detail']
        action('view_refill', '查看补数状态', 'refill')
    elif counts.get('accepted') is not None:
        accepted, gap = counts['accepted'], counts.get('gap')
        stage = 'results_ready'
        summary = f'清洗结果已发布，当前入选 {accepted} 张' + (f'，缺口 {gap} 张。' if gap is not None else '。')
        if gap:
            action('view_refill', '查看补数状态', 'refill')
        elif accepted > 0:
            action('view_delivery', '查看打包方案', 'packaging')
        action('view_cleaning', '查看清洗结果', 'cleaning')
    elif clean['node_status'] == 'succeeded':
        stage, summary = 'cleaning_results_pending', '清洗作业已结束，结果计数尚未发布。'
        action('view_cleaning', '查看清洗结果', 'cleaning')
    elif scoped_manifest:
        stage, summary = 'awaiting_cleaning', '检索已结束，候选清单已确认。下一步准备并确认清洗方案。'
        action('prepare_cleaning', '生成清洗确认卡', 'cleaning', 'prepare')
    elif retrieval['node_status'] == 'failed':
        stage, summary = 'retrieval_failed', retrieval['detail']
        action('view_retrieval', '查看检索结果', 'retrieval')
    elif scoped_search and scoped_search.get('status') == 'approved':
        superseded = scoped_search.get('job_status') == 'superseded'
        ready = not superseded and (scoped_search.get('pagination_stopped') or scoped_search.get('initial_window_complete'))
        stage = 'unknown' if superseded else 'awaiting_candidates' if ready else 'retrieving'
        summary = retrieval['detail']
        if ready:
            action('confirm_candidates', '确认候选清单', 'retrieval', 'confirm')
        action('view_retrieval', '查看检索结果', 'retrieval')
    elif scoped_search and scoped_search.get('status') == 'unapproved':
        stage, summary = 'awaiting_retrieval', '需求已确认，检索尚未启动。'
        action('start_retrieval', '开始检索', 'retrieval', 'confirm')
    execution_current = (isinstance(execution_job, dict)
        and execution_job.get('workflow_ref') == 'confirmed-terms-retrieval@1'
        and (execution_job.get('revision'), execution_job.get('generation'), execution_job.get('task_version'))
            == (task['confirmed_revision'], task['run_generation'], task['task_version']))
    if execution_current and stage in ('unknown', 'awaiting_retrieval', 'retrieving'):
        state = execution_job['status']
        actions = [item for item in actions if item['id'] != 'start_retrieval']
        if state in ('queued', 'running') and (not scoped_search or scoped_search.get('status') == 'unapproved'):
            stage = 'awaiting_retrieval'
            summary = '词表已确认，检索 Agent 已接收，正在排队准备检索。'
            retrieval['display_status'] = '检索 Agent 准备中'
            retrieval['detail'] = summary
        elif state in ('unknown', 'failed'):
            stage = 'unknown' if state == 'unknown' else 'retrieval_failed'
            summary = ('检索请求的执行结果待核对，已暂停，不会自动重发。' if state == 'unknown' else
                       '检索 Agent 未能完成查找，已确认的词表仍保留。')
            retrieval['display_status'] = '结果待核对' if state == 'unknown' else '检索未完成'
            retrieval['detail'] = summary
            reasons.append({'source':'retrieval', 'code':execution_job.get('code') or 'AGENT_SEARCH_FAILED'})
    intake_policy = (execution_job.get('execution_scope') or {}) if execution_current else {}
    automatic_cleaning = intake_policy.get('schema_version') == 'confirmed-terms-execution@2'
    if automatic_cleaning and (stage == 'awaiting_cleaning' or
            stage == 'awaiting_candidates' and intake_policy.get('candidate_intake') == 'auto'):
        actions = [item for item in actions if item['id'] not in ('confirm_candidates', 'prepare_cleaning')]
        if execution_job['status'] in ('failed', 'unknown'):
            stage = 'intake_blocked'
            summary = '自动清洗交接未完成，已有候选保留，请查看原因；不会跳过校验。'
            reasons.append({'source': 'cleaning', 'code': execution_job.get('code') or 'AUTO_INTAKE_FAILED'})
            set_node('cleaning', 'failed', '交接未完成', summary, 'blocked')
        else:
            stage, summary = 'preparing_cleaning', '候选将自动固定并按已确认条件进入现有清洗流程，无需再次确认。'
            set_node('retrieval', 'succeeded', '候选已就绪', summary, 'approved')
            set_node('cleaning', 'pending', '准备清洗中', summary, 'approved')
        action('view_retrieval', '查看检索候选', 'retrieval')
    if (execution_current and stage in ('awaiting_candidates', 'preparing_cleaning') and execution_job['status']=='completed'
            and execution_job.get('code')=='NO_CANDIDATES'):
        stage, summary = 'retrieval_empty', '检索已结束，目前没有可确认的候选。可以调整词表后重新确认。'
        actions = [item for item in actions if item['id'] != 'confirm_candidates']
        set_node('retrieval','succeeded','检索已结束，无可确认候选',summary,'blocked')
    if stage == 'unknown':
        action('refresh', '刷新状态', None)
    if task.get('pending_draft'):
        summary = '有一份需求修改待确认。' + summary
        actions.insert(0, {'id': 'review_change', 'label': '查看待确认修改', 'node': None, 'kind': 'view'})
    if run_status in ('PAUSED', 'CANCELLED'):
        actions = [item for item in actions if item['kind'] == 'view']
    capabilities = capabilities or {}
    if not capabilities.get('cleaning', True) and any(item['id'] == 'prepare_cleaning' for item in actions):
        actions = [item for item in actions if item['id'] != 'prepare_cleaning']
        summary = '检索已结束，候选清单已确认。清洗服务尚未配置。'
        reasons.append({'source': 'cleaning', 'code': 'CLEANING_NOT_CONFIGURED'})
        action('view_retrieval', '查看检索结果', 'retrieval')
    if not capabilities.get('delivery', True):
        actions = [item for item in actions if item['id'] != 'view_delivery']
    actions = [item for item in actions if (item['kind'] != 'confirm' or capabilities.get('can_approve', True))
               and (item['kind'] != 'prepare' or capabilities.get('can_create', True))]
    value = {'schema_version': 'task-workflow@1', 'binding': binding(task), 'state': stage,
             'summary': summary, 'nodes': nodes, 'counts': counts,
             'available_actions': actions, 'next_action': actions[0] if actions else None,
             'quality': 'partial' if reasons else 'current', 'blocking_reasons': reasons}
    next_action = value['next_action']
    value['status_reply'] = summary + ('下一步：' + next_action['label'] + '。' if next_action else '')
    value['slow_reply'] = value['status_reply'] + '当前没有足够的阶段耗时证据判断慢在哪里。'
    value['state_digest'] = digest(value)
    return value


def status_question_topic(text):
    question = re.sub(r'\s+', '', text)
    if re.search(r'增加|新增|添加|删除|删掉|修改|改成|改为|替换|调整|只要|不要|保留|排除|补充(?:条件|检测)', question):
        return None
    if (question == '慢' or re.search(r'为什么.*慢|怎么.*慢|(?:检索|清洗|扩词|响应|处理|加载|运行|速度|生成|对话|模型).{0,12}慢|太慢|很慢|这么慢|好慢|卡住|等了|一直等|还没(?:结束|完成|好)', question)):
        return 'slow'
    if re.search(r'下一步|接下来|怎么继续|该做什么|点哪里|该点哪|接着做', question):
        return 'next_step'
    if re.search(r'状态|进度|完成了吗|结束了吗|好了没|有结果|到哪一步|进行到哪|现在怎么样', question):
        return 'progress'
    return None


def workflow_reply(workflow, topic='progress'):
    if not isinstance(workflow, dict) or workflow.get('schema_version') != 'task-workflow@1':
        return '当前执行进度暂时无法核对，请刷新状态。'
    return workflow['slow_reply' if topic == 'slow' else 'status_reply']
