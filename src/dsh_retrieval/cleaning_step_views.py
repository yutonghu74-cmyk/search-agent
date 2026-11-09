"""Read-only step projections of immutable results and durable checkpoints."""
import hashlib
import json

from .multi_rule_contracts import require_complete_cleaning_result


def _step_execution_id(job, step_id):
    value = json.dumps({"operation_id": job.binding.operation_id,
                        "recipe_digest": job.recipe.recipe_digest,
                        "step_id": step_id}, sort_keys=True,
                       separators=(",", ":")).encode()
    return "step_" + hashlib.sha256(value).hexdigest()


def _reasons(verdicts):
    return [{'rule_id': rule.rule_id, 'verdict': rule.verdict,
             'reason_code': rule.reason_code,
             'reason_summary': rule.reason_summary,
             'reason_truncated': rule.reason_truncated}
            for rule in verdicts]


def _status(verdicts):
    return ('rejected' if any(rule.verdict == 'fail' for rule in verdicts)
            else 'review' if not verdicts or any(
                rule.verdict == 'unknown' for rule in verdicts)
            else 'retained')


def _applies(candidate, rules):
    scopes = {rule.group_id for rule in rules}
    return None in scopes or bool(scopes & set(candidate.group_ids))


def _step(step, job, rules, items, *, status):
    counts = dict(input=len(items), retained=0, rejected=0,
                  review=0, pending=0, blocked=0)
    for item in items:
        counts[item['status']] += 1
    return {'step_id': step.step_id,
            'step_execution_id': _step_execution_id(job, step.step_id),
            'input_version': job.binding.input_manifest_digest,
            'backend_ref': step.backend_ref, 'status': status,
            'counts': counts, 'items': items,
            'rules': [{'rule_id': rule.rule_id, 'predicate': rule.predicate_ref,
                       'operator': rule.operator, 'value': rule.value}
                      for rule in rules]}


def _checkpoint_output_version(job, checkpoint):
    def rule(value):
        return {'rule_id': value.rule_id, 'verdict': value.verdict,
                'reason_code': value.reason_code,
                'reason_summary': value.reason_summary,
                'reason_truncated': value.reason_truncated,
                'group_id': value.group_id}

    value = {'kind': 'cleaning-checkpoint@1',
             'operation_id': job.binding.operation_id,
             'input_version': job.binding.input_manifest_digest,
             'recipe_digest': job.recipe.recipe_digest,
             'updated_at': checkpoint['updated_at'],
             'completed': [
                 {'candidate_id': item.candidate_id,
                  'asset_version': item.asset_version,
                  'verdict': item.verdict,
                  'rules': [rule(saved) for saved in item.rules]}
                 for item in checkpoint['completed']],
             'partial_candidate_id': checkpoint['partial_candidate_id'],
             'partial_steps': [[rule(saved) for saved in values]
                               for values in checkpoint['partial_steps']]}
    return 'sha256:' + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False).encode()).hexdigest()


def cleaning_step_view(job, result):
    require_complete_cleaning_result(job, result)
    steps = []
    for step in job.recipe.steps:
        rules = [rule for rule in job.recipe.rules if rule.step_id == step.step_id]
        ids = {rule.rule_id for rule in rules}
        items = []
        for candidate in result.candidates:
            verdicts = [rule for rule in candidate.rules if rule.rule_id in ids]
            if verdicts and all(rule.verdict == 'not_applicable' for rule in verdicts):
                # This group's check was never owed: not this step's input.
                continue
            status = _status(verdicts)
            items.append({'candidate_id': candidate.candidate_id,
                          'asset_version': candidate.asset_version, 'status': status,
                          'preview_status': 'unavailable', 'reasons': _reasons(verdicts)})
        steps.append(_step(step, job, rules, items, status='completed'))
    return {'operation_id': job.binding.operation_id,
            'revision': job.binding.revision, 'generation': job.binding.generation,
            'recipe_digest': job.recipe.recipe_digest, 'output_version': result.result_digest,
            'status': 'completed', 'candidate_preview_available': True,
            'execution_mode': 'shared_input_detection', 'steps': steps,
            'note': '各步骤检测同一候选集；本步保留不代表最终合格，淘汰数量不可直接相加。'}


def cleaning_checkpoint_view(job, checkpoint):
    """Freeze a durable in-flight checkpoint into a versioned page source."""
    completed = {item.candidate_id: item for item in checkpoint['completed']}
    partial_id = checkpoint['partial_candidate_id']
    partial_steps = checkpoint['partial_steps']
    steps = []
    for step_index, step in enumerate(job.recipe.steps):
        rules = [rule for rule in job.recipe.rules if rule.step_id == step.step_id]
        ids = {rule.rule_id for rule in rules}
        items = []
        for candidate in job.candidates:
            if not _applies(candidate, rules):
                continue
            saved = completed.get(candidate.candidate_id)
            verdicts = ([rule for rule in saved.rules if rule.rule_id in ids]
                        if saved is not None else
                        list(partial_steps[step_index])
                        if candidate.candidate_id == partial_id
                        and step_index < len(partial_steps) else None)
            status = 'pending' if verdicts is None else _status(verdicts)
            items.append({'candidate_id': candidate.candidate_id,
                          'asset_version': candidate.asset_version,
                          'status': status, 'preview_status': 'unavailable',
                          'reasons': [] if verdicts is None else _reasons(verdicts)})
        pending = sum(item['status'] == 'pending' for item in items)
        step_status = ('pending' if pending == len(items) else
                       'running' if pending else 'completed')
        steps.append(_step(step, job, rules, items, status=step_status))
    return {'operation_id': job.binding.operation_id,
            'revision': job.binding.revision, 'generation': job.binding.generation,
            'recipe_digest': job.recipe.recipe_digest,
            'output_version': _checkpoint_output_version(job, checkpoint),
            'status': 'running', 'candidate_preview_available': False,
            'execution_mode': 'shared_input_detection', 'steps': steps,
            'note': ('运行中检查点按固定输出版本展示；各步骤共享冻结输入，'
                     '未处理项为 pending，当前执行模式不虚构上游 blocked。')}
