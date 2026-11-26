#!/usr/bin/env python3
"""Targeted real-model evaluation with synthetic requests and an isolated dialogue ledger.

Uses an existing model connection read-only. Never creates a business task or starts search.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.model_settings import ModelSettings
from dsh_retrieval.models import Actor


class ObservedRuntime(ModelRuntime):
    def _validate_dialogue_proposal(self, value, state, message, count_hint):
        try:
            return super()._validate_dialogue_proposal(value, state, message, count_hint)
        except ServiceError as error:
            self.rejected.append({'reason': error.message, 'proposal': value})
            raise


CASES = [
    {'name': 'three_scenes_each_20', 'turns': [
        ('一只毛驴在奔跑、一只狗在吃饭、一个兔子在睡觉的图，每个类型要20张', 60),
        ('狗改成30张，其他不变', 70),
        ('只有狗不要水印', 70),
        ('兔子改成吃草', 70),
        ('兔子不要了', 50)]},
    {'name': 'quantity_followup', 'turns': [
        ('想找毛驴奔跑、狗吃饭和兔子睡觉的图片', None),
        ('每个类型要20张', 60)]},
    {'name': 'unallocated_total', 'turns': [
        ('找猫在窗边和狗在草地的图片，总共20张，不规定各类数量', 20)]},
    {'name': 'scene_objects_are_not_images', 'turns': [
        ('要一只狗正在吃饭、两只兔子在睡觉的图片', None)]},
]


def check_semantics(case, index, row):
    categories = (row.get('draft') or {}).get('categories', [])
    errors = []
    # Category display names may be literal scene phrases, not only entity nouns.
    # Match identity in both label and scene, and reject omissions/duplicates.
    by_label = {}
    for category in categories:
        identities = [label for label in ('毛驴', '狗', '兔子')
                      if label in category['label']
                      and not any(other in category['scene'] for other in ('毛驴', '狗', '兔子') if other != label)]
        if len(identities) != 1 or identities[0] in by_label:
            if case in ('three_scenes_each_20', 'quantity_followup'):
                errors.append('ambiguous_category_identity')
        else:
            by_label[identities[0]] = category
    if case in ('three_scenes_each_20', 'quantity_followup'):
        labels = {'毛驴', '狗'} if case == 'three_scenes_each_20' and index == 4 else {'毛驴', '狗', '兔子'}
        if set(by_label) != labels:
            errors.append('wrong_categories')
        for label, action in [('毛驴', '奔跑'), ('狗', '吃饭'), ('兔子', '吃草' if case == 'three_scenes_each_20' and index >= 3 else '睡觉')]:
            if label in labels and action not in by_label.get(label, {}).get('scene', ''):
                errors.append('wrong_scene:' + label)
        for label, category in by_label.items():
            expected = (None if case == 'quantity_followup' and index == 0 else
                        30 if case == 'three_scenes_each_20' and index >= 1 and label == '狗' else 20)
            if category['target_count'] != expected:
                errors.append('wrong_quota:' + label)
            conditions = '；'.join(c['text'] for c in category['conditions'])
            if case == 'three_scenes_each_20' and index >= 2:
                if (label == '狗') != ('水印' in conditions):
                    errors.append('wrong_condition_scope:' + label)
    if case == 'unallocated_total':
        if len(categories) != 2 or any(c['target_count'] is not None for c in categories):
            errors.append('invented_category_quota')
    if case == 'scene_objects_are_not_images':
        scenes = '；'.join(c['scene'] for c in categories)
        if not categories or any(c['target_count'] is not None for c in categories):
            errors.append('invented_category_quota')
        if not all(fragment in scenes for fragment in ('一只狗', '吃饭', '两只兔子', '睡觉')):
            errors.append('lost_scene_objects_or_actions')
    return errors


TRANSIENT_ERRORS = {'MODEL_RATE_LIMIT', 'MODEL_REQUEST_FAILED', 'MODEL_TIME_BUDGET_EXCEEDED', 'DSH_TIMEOUT'}


def retry_turn(coordinator, actor, body, attempts, retry_transient=0):
    """Retry only uncommitted pre-task turns, retaining each failure as evidence."""
    for attempt in range(retry_transient + 1):
        started = time.monotonic()
        try:
            result = coordinator.turn(actor, 'evaluation', body)
        except ServiceError as error:
            attempts.append({'status': 'failed', 'error_code': error.code,
                             'seconds': round(time.monotonic() - started, 3)})
            if error.code not in TRANSIENT_ERRORS or attempt == retry_transient:
                raise
        else:
            attempts.append({'status': 'completed', 'seconds': round(time.monotonic() - started, 3)})
            return result


def evaluate(runtime, llm, output, *, retry_transient=0):
    rows = []
    with tempfile.TemporaryDirectory(prefix='requirement-eval-') as directory:
        actor = Actor('evaluation', 'requester')
        projects = LocalProjectAccess([('evaluation', 'requester', 'evaluation', 'default')])
        store = DialogueStore(Path(directory) / 'dialogue.sqlite')
        coordinator = DialogueCoordinator(store, runtime, projects)
        for case in CASES:
            previous = None
            for turn_index, (message, expected_count) in enumerate(case['turns']):
                runtime.rejected = []
                started = time.monotonic()
                row = {'case': case['name'], 'message': message, 'expected_count': expected_count, 'attempts': []}
                try:
                    result = retry_turn(coordinator, actor, {
                        'session_id': previous['session_id'] if previous else None,
                        'expected_version': previous['version'] if previous else 0,
                        'phase': previous['stage'] if previous else 'request',
                        'llm_id': llm, 'message': message, 'generate_terms_when_complete': True}, row['attempts'], retry_transient)
                    row.update(actual_count=result['request']['target_count'], action=result['action'],
                               stage=result['stage'], question=result['question'], reply=result['reply'],
                               draft=result['request'].get('requirement_draft'),
                               effective_request=result['request']['effective_request'])
                    row['passed'] = (row['actual_count'] == expected_count
                        and row['action'] not in ('clarify', 'answer')
                        and (expected_count is None or row['stage'] == 'terms')
                        and row['draft'] is not None)
                    errors = check_semantics(case['name'], turn_index, row)
                    row['semantic_errors'] = errors
                    row['passed'] = row['passed'] and not errors
                    previous = result
                except ServiceError as error:
                    row.update(passed=False, error_code=error.code)
                row['rejected_proposals'] = runtime.rejected
                row['seconds'] = round(time.monotonic() - started, 3)
                rows.append(row)
                output.write_text(json.dumps({'scope': 'synthetic requests; real model; isolated pre-task ledger',
                    'model': runtime.select(llm, 'llm').config.model, 'turns': rows}, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps({'case': row['case'], 'passed': row['passed'], 'seconds': row['seconds']}, ensure_ascii=False), flush=True)
                if not row['passed']:
                    break
        store.connection.close()
    return all(row['passed'] for row in rows) and len(rows) == sum(len(case['turns']) for case in CASES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings-store', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--allowed-url', action='append', required=True)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--llm')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--retry-transient', type=int, choices=range(3), default=0,
                        help='explicit bounded retries of failed pre-task turns; all attempts remain in the report')
    args = parser.parse_args()
    if not args.settings_store.is_file() or not args.key_file.is_file():
        parser.error('Existing settings and key files are required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='requirement-model-settings-') as directory:
        copied = Path(directory) / 'settings.sqlite'
        with sqlite3.connect(args.settings_store.resolve().as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(copied) as target:
                source.backup(target)
        settings = ModelSettings(copied, args.key_file, args.allowed_url)
        with settings.scope(args.tenant, args.user):
            defaults = settings.defaults()
            llm = args.llm or defaults.get('dialogue_llm') or defaults.get('llm')
            profile = settings.select(llm, 'llm')
            if profile is None:
                parser.error('No selected conversation model is available')
            runtime = ObservedRuntime((profile,), opener=settings.opener)
            success = evaluate(runtime, llm, args.output, retry_transient=args.retry_transient)
        settings.db.close()
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
