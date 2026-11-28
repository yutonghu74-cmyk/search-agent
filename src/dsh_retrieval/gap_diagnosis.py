"""Evidence-based, read-only gap explanation. Never authorizes or retries work."""


def diagnose_gap(report):
    observed = report.get('execution_observation') or {}
    cleaning = report.get('cleaning') or {}
    totals = report['totals']
    continuation = report.get('continuation') or {}
    current = cleaning.get('status') == 'observed'
    gap = cleaning.get('gap') if current else None
    groups = cleaning.get('group_gaps', []) if current else []
    facts = []

    def fact(code, text, **evidence):
        facts.append({'code': code, 'summary': text, 'evidence': evidence})

    pending = observed.get('pending_operations', 0)
    if pending:
        fact('EXECUTION_UNRESOLVED', '检索操作尚无已提交结果，不能判断是否已执行或把它当作零命中。', count=pending)
    if observed.get('queue_state') == 'failed':
        fact('EXECUTION_FAILED', '检索调度报告失败，已有提交结果保留；失败不说明素材不足。',
             error_code=observed.get('queue_error'))
    if not report['mapping_complete'] or totals.get('content_unknown_candidates', 0):
        fact('MAPPING_INCOMPLETE', '部分命中缺少稳定素材映射或内容摘要，不能纳入完整诊断。',
             unresolved_hits=totals['unresolved_hits'], content_unknown=totals.get('content_unknown_candidates', 0))
    if not report['complete']:
        fact('WINDOW_INCOMPLETE', '当前检索窗口尚未完成，不能据此判断来源不足。')
    if current:
        if cleaning['pending_review_count']:
            fact('REVIEW_PENDING', '当前发布结果含待复核素材，未知不计为有效或淘汰。', count=cleaning['pending_review_count'])
        if cleaning['failed_count']:
            fact('CLEANING_REJECTIONS', '当前发布结果中存在清洗淘汰；规则原因是观测证据，不等于模型判定已被人工验证。',
                 count=cleaning['failed_count'], rule_reasons=cleaning.get('reason_counts', []))
        if cleaning['duplicate_count']:
            fact('CONTENT_DUPLICATES', '当前发布结果存在相同内容去重，不降低去重要求凑数。', count=cleaning['duplicate_count'])
        if cleaning.get('historical_exclusion_count'):
            fact('HISTORICAL_EXCLUSIONS', '当前发布结果含历史基准排除，不能重复计入新产出。', count=cleaning['historical_exclusion_count'])
        if any(row['gap'] for row in groups):
            fact('GROUP_GAPS', '存在分组缺口，其他类别的富余不能填平该组目标。', groups=groups)
    else:
        fact('EFFECTIVE_COUNT_UNKNOWN', '尚无当前已发布清洗结果，有效数量和业务缺口未知。', status=cleaning.get('status', 'not_configured'))
    if report['complete'] and totals['assignable_candidates'] < report['target_count']:
        fact('WINDOW_CANDIDATE_SHORTFALL', '当前窗口可进入候选范围的数量少于目标，不代表全库缺少素材。',
             count=totals['assignable_candidates'], target=report['target_count'])
    if (totals.get('repeated_hit_ratio') or 0) >= .5:
        fact('REPEATED_COVERAGE', '当前已映射命中至少一半重复覆盖已有候选。', ratio=totals['repeated_hit_ratio'])
    stop = continuation.get('stop_reason') or (report.get('execution_budget') or {}).get('stop_reason')
    if stop:
        fact('PLANNER_STOP', '原执行计划已有停止原因，诊断不解除停止或扩充授权。', stop_reason=stop)
    available = observed.get('more_available', 0) + observed.get('not_started', 0)
    if available:
        fact('APPROVED_POSITIONS_REMAIN', '原计划仍有可续页或未执行位置；是否可继续由原补数服务复核。', count=available)

    codes = {row['code'] for row in facts}
    if pending:
        action, text = 'reconcile_operation', '先核对原检索操作；刷新只读取状态，不自动重发结果未知的请求。'
    elif 'EXECUTION_FAILED' in codes:
        action, text = 'inspect_failure', '先查看原检索失败记录并核对完成状态，确认可安全重试后再走原恢复入口。'
    elif 'MAPPING_INCOMPLETE' in codes:
        action, text = 'repair_mapping', '先核对素材映射和受控读取，再重新读取诊断。'
    elif (report.get('execution_budget') or {}).get('stop_reason'):
        action, text = 'review_budget', '已达到原执行限制；保留已提交结果，扩大预算须重新确认。'
    elif not report['complete']:
        action, text = 'wait_for_result', '等待原运行完成，保留已提交页面；可刷新诊断，不重复执行。'
    elif 'REVIEW_PENDING' in codes:
        action, text = 'review_cleaning', '先进入原待复核列表处理，复核发布后再评估补数。'
    elif current and gap == 0 and not any(row['gap'] for row in groups):
        action, text = 'target_reached', '当前有效数量及分组目标已达标，可按原交付流程继续。'
    elif continuation.get('action') in ('wait_for_result', 'wait_for_cleaning'):
        action, text = 'wait_for_result', '等待原检索或清洗发布当前结果，不重跑已完成步骤。'
    elif stop == 'EXECUTION_LIMIT_REACHED' or (report.get('execution_budget') or {}).get('stop_reason'):
        action, text = 'review_budget', '已达到原执行限制；可保留部分结果，扩大预算须重新确认。'
    elif continuation.get('action') == 'continue_approved':
        action, text = 'continue_approved', '原补数服务允许在已批准范围内继续，由原协调器推进；本次读取不派发。'
    elif continuation.get('action') == 'owner_confirmation':
        action, text = 'owner_confirmation', '继续补数需发起人确认，请使用原补数确认入口。'
    elif stop in ('NO_PROGRESS', 'SEARCH_STRATEGIES_EXHAUSTED', 'INPUT_SCOPE_COMPLETE'):
        action, text = 'review_strategy', '当前批准策略已停止，可审阅词表或来源调整，也可交付部分结果；变化仍须确认。'
    elif not current:
        action, text = 'review_candidates', '先查看候选及原清洗状态，尚不能按业务缺口自动补数。'
    elif available:
        action, text = 'check_continuation', '先核对原补数计划的授权、预算和当前位置，不能仅凭剩余页直接执行。'
    else:
        action, text = 'review_strategy', '依据淘汰、重复覆盖和分组缺口审阅检索策略；新增词表或来源须确认，素材总量仍未知。'
    return {'schema_version': 'gap-diagnosis@1', 'scope': dict(report['scope']),
        'coverage': 'current_retrieval_window_and_published_results',
        'effective_gap': gap, 'group_gaps': groups,
        'corpus_sufficiency': 'unknown', 'facts': facts,
        'next_step': {'action': action, 'summary': text, 'execution_allowed': False},
        'requires_confirmation_for': ['new_terms', 'new_sources', 'higher_budget', 'relaxed_conditions']}
