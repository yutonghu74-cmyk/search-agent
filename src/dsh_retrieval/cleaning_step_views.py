"""Read-only step projections of complete, immutable cleaning results."""
from .multi_rule_contracts import require_complete_cleaning_result


def cleaning_step_view(job, result):
    require_complete_cleaning_result(job, result)
    steps = []
    for step in job.recipe.steps:
        rules = [rule for rule in job.recipe.rules if rule.step_id == step.step_id]
        ids = {rule.rule_id for rule in rules}
        items = []
        counts = dict(input=len(job.candidates), retained=0, rejected=0, review=0)
        for candidate in result.candidates:
            verdicts = [rule for rule in candidate.rules if rule.rule_id in ids]
            status = ('rejected' if any(rule.verdict == 'fail' for rule in verdicts)
                      else 'review' if not verdicts or any(rule.verdict == 'unknown' for rule in verdicts)
                      else 'retained')
            counts[status] += 1
            items.append({'candidate_id': candidate.candidate_id,
                          'asset_version': candidate.asset_version, 'status': status,
                          'preview_status': 'unavailable',
                          'reasons': [{'rule_id': rule.rule_id, 'verdict': rule.verdict,
                                       'reason_code': rule.reason_code,
                                       'reason_summary': rule.reason_summary,
                                       'reason_truncated': rule.reason_truncated}
                                      for rule in verdicts]})
        steps.append({'step_id': step.step_id, 'backend_ref': step.backend_ref,
                      'status': 'completed', 'counts': counts, 'items': items,
                      'rules': [{'rule_id': rule.rule_id, 'predicate': rule.predicate_ref,
                                 'operator': rule.operator, 'value': rule.value} for rule in rules]})
    return {'operation_id': job.binding.operation_id,
            'revision': job.binding.revision, 'generation': job.binding.generation,
            'recipe_digest': job.recipe.recipe_digest, 'output_version': result.result_digest,
            'execution_mode': 'shared_input_detection', 'steps': steps,
            'note': '各步骤检测同一候选集；本步保留不代表最终合格，淘汰数量不可直接相加。'}
