#!/usr/bin/env python3
"""Data-driven multi-turn requirement-understanding checks on an isolated dialogue ledger.

Real mode uses an existing model connection read-only. --fixture replays each
turn's ideal proposal through the same coordinator without any network call.
Neither mode creates a business task or starts search: only the pre-task
DialogueCoordinator is wired. Every check re-reads the persisted draft; the
assistant reply alone never counts as a saved change.
"""
import argparse
from collections import Counter
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.dialogue import DialogueCoordinator, DialogueStore
from dsh_retrieval.errors import ServiceError
from dsh_retrieval.intent_model import ModelConfig
from dsh_retrieval.model_runtime import ModelProfile, ModelRuntime
from dsh_retrieval.models import Actor

DEFAULT_SCENARIOS = ROOT / 'tools/requirement_scenarios/core-24.json'
CHECKS = ('categories', 'scene', 'count', 'scope', 'clarification', 'state')
ASKING = {'custom_clarification', 'subject', 'target_count', 'media_type', 'confirm_summary'}
TRANSIENT_ERRORS = {'MODEL_RATE_LIMIT', 'MODEL_REQUEST_FAILED', 'MODEL_TIME_BUDGET_EXCEEDED', 'DSH_TIMEOUT'}
# DialogueCoordinator turns a twice-rejected proposal into this clarify reply.
PROTOCOL_FALLBACK = '这次生成的修改未通过校验'
QUANTITY_QUESTION = re.compile(r'多少张|几张|多少幅|几幅|需要多少(?:图片|照片)')


class ObservedRuntime(ModelRuntime):
    """Record model calls, rejected proposals and the accepted proposal of a turn."""
    def reset_observation(self):
        self.rejected, self.accepted, self.model_called = [], None, False

    def interpret_dialogue_turn(self, *args, **kwargs):
        self.model_called = True
        self.accepted = super().interpret_dialogue_turn(*args, **kwargs)
        return self.accepted

    def _validate_dialogue_proposal(self, value, state, message, count_hint):
        try:
            return super()._validate_dialogue_proposal(value, state, message, count_hint)
        except ServiceError as error:
            self.rejected.append({'reason': error.message, 'proposal': deepcopy(value)})
            raise


class ScriptedTransport:
    """Serve the current turn's fixture proposal(s) as an OpenAI-style tool call."""
    def __init__(self):
        self.outputs, self.calls = [], []

    def open(self, request, timeout):
        self.calls.append(json.loads(request.data))
        if not self.outputs:
            raise ServiceError('MODEL_REQUEST_FAILED', 'No scripted fixture left for this turn', 502)
        # A repair request re-serves the ideal proposal: fixture mode measures
        # whether the backend accepts it, not whether a model can rephrase it.
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        tool = self.calls[-1]['tools'][0]['function']['name']
        message = {'tool_calls': [{'type': 'function', 'id': 'fixture-call', 'function': {
            'name': tool, 'arguments': json.dumps({'proposal': output}, ensure_ascii=False)}}]}
        return BytesIO(json.dumps({'choices': [{'message': message, 'finish_reason': 'tool_calls'}]}).encode())


def load_scenarios(path=DEFAULT_SCENARIOS, *, only=None, groups=None):
    data = json.loads(Path(path).read_text())
    if data.get('schema_version') != 'requirement-scenarios@1':
        raise ValueError('Unsupported scenario file')
    selected = []
    for scenario in data['scenarios']:
        if only and scenario['id'] not in only or groups and scenario['group'] not in groups:
            continue
        turns = deepcopy(data['prefixes'][scenario['prefix']]) if scenario.get('prefix') else []
        for turn in turns:
            turn['prefix'] = True
        selected.append({**scenario, 'turns': turns + deepcopy(scenario['turns'])})
    return selected


def expected_states(scenario):
    """Resolve carried expectations: full category maps, patches and removals."""
    categories, states = {}, []
    for index, turn in enumerate(scenario['turns']):
        expect = turn['expect']
        if 'categories' in expect:
            categories = deepcopy(expect['categories'])
        for alias, patch in expect.get('category_changes', {}).items():
            if patch is None:
                categories.pop(alias, None)
            else:
                categories[alias] = {**categories.get(alias, {}), **patch}
        states.append({'categories': deepcopy(categories), 'total': expect.get('total'),
                       'stage': expect.get('stage', 'terms'), 'ask': expect.get('ask', False),
                       'action': expect.get('action'), 'unchanged': expect.get('unchanged', False),
                       'replace': expect.get('replace', False), 'reply_forbid': expect.get('reply_forbid', []),
                       'may_change': (None if index == 0 or expect.get('replace') else expect.get('may_change', [])),
                       'common_may_change': expect.get('common_may_change', False),
                       'tracked': scenario.get('tracked_conditions', [])})
    return states


def _allowed(value, spec):
    return value in spec if isinstance(spec, list) else value == spec


def _text(category):
    return category['label'] + ' ' + category['scene']


def match_categories(expected, categories):
    """Category identity is all match substrings in label+scene, not exact wording."""
    mapping, errors = {}, []
    for category in categories:
        hits = [alias for alias, spec in expected.items() if all(m in _text(category) for m in spec['match'])]
        if not hits:
            errors.append(('categories', 'unexpected_category', category['label']))
        elif len(hits) > 1:
            errors.append(('categories', 'ambiguous_category', category['label'] + '->' + '/'.join(hits)))
        elif hits[0] in mapping:
            errors.append(('categories', 'duplicate_category', hits[0]))
        else:
            mapping[hits[0]] = category
    errors += [('categories', 'missing_category', alias) for alias in expected if alias not in mapping]
    return mapping, errors


def _comparable(category):
    return {'category_id': category['category_id'], 'label': category['label'], 'scene': category['scene'],
            'target_count': category['target_count'], 'conditions': [c['text'] for c in category['conditions']]}


def stored_view(state):
    draft = state.get('requirement_draft') or {'categories': []}
    return {'categories': deepcopy(draft['categories']), 'target_count': state.get('target_count'),
            'constraints': list(state.get('constraints') or []), 'stage': state.get('stage'),
            'question': state.get('question')}


def check_turn(expect, stored, result, previous=None, previous_mapping=None, restarted=None):
    """Return (mapping, errors) for one turn against the re-read backend state."""
    mapping, errors = match_categories(expect['categories'], stored['categories'])
    common = '；'.join(stored['constraints'])
    for alias, category in mapping.items():
        spec = expect['categories'][alias]
        errors += [('scene', 'lost_scene', f'{alias}:{word}') for word in spec.get('scene', ()) if word not in _text(category)]
        errors += [('scene', 'stale_scene', f'{alias}:{word}') for word in spec.get('scene_forbid', ()) if word in _text(category)]
        if 'count' in spec and not _allowed(category['target_count'], spec['count']):
            errors.append(('count', 'wrong_category_count', f"{alias}:{category['target_count']}"))
        effective = '；'.join([c['text'] for c in category['conditions']] + ([common] if common else []))
        required = spec.get('conditions', [])
        errors += [('scene', 'lost_condition', f'{alias}:{word}') for word in required if word not in effective]
        errors += [('scope', 'condition_leak', f'{alias}:{word}') for word in expect['tracked']
                   if word not in required and word in effective]
    if not _allowed(stored['target_count'], expect['total']):
        errors.append(('count', 'wrong_total', str(stored['target_count'])))
    if previous is not None and expect['may_change'] is not None:
        allowed = set(expect['may_change'])
        for alias, old in (previous_mapping or {}).items():
            if alias in allowed:
                continue
            if alias not in mapping:
                errors.append(('scope', 'unrequested_removal', alias))
            elif _comparable(mapping[alias]) != _comparable(old):
                errors.append(('scope', 'unrequested_change', alias))
        errors += [('scope', 'unrequested_addition', alias) for alias in mapping
                   if alias not in (previous_mapping or {}) and alias not in allowed]
        if not expect['common_may_change'] and stored['constraints'] != previous['constraints']:
            errors.append(('scope', 'common_constraints_changed', common))
    question, reply = result.get('question'), result.get('reply') or ''
    ask = expect['ask']
    if ask is False:
        if question in ASKING or result.get('action') == 'clarify':
            errors.append(('clarification', 'unnecessary_question', f'{question}: {reply}'))
        elif stored['target_count'] is not None and QUANTITY_QUESTION.search(reply):
            errors.append(('clarification', 'repeated_quantity_question', reply))
    else:
        ask = {} if ask is True else ask
        if question not in set(ask.get('questions', ASKING)):
            errors.append(('clarification', 'missing_question', f'{question}: {reply}'))
        errors += [('clarification', 'unspecific_question', f'missing {word}: {reply}')
                   for word in ask.get('mentions', ()) if word not in reply]
        errors += [('clarification', 'asks_known_information', f'{word}: {reply}')
                   for word in ask.get('forbid', ()) if word in reply]
    if stored['stage'] != expect['stage']:
        errors.append(('state', 'wrong_stage', str(stored['stage'])))
    if expect['action'] and result.get('action') != expect['action']:
        errors.append(('state', 'wrong_action', str(result.get('action'))))
    if expect['unchanged'] and previous is not None:
        for field in ('categories', 'target_count', 'constraints', 'stage'):
            if stored[field] != previous[field]:
                errors.append(('state', 'mutated_without_edit', field))
    errors += [('state', 'false_execution_claim', word) for word in expect['reply_forbid'] if word in reply]
    if result.get('action') in ('confirm', 'confirm_terms'):
        errors.append(('state', 'unexpected_confirmation', result['action']))
    reported = result.get('request') or {}
    if (reported.get('requirement_draft') or {'categories': []})['categories'] != stored['categories'] \
            or reported.get('target_count') != stored['target_count']:
        errors.append(('state', 'response_differs_from_saved_draft', ''))
    if restarted is not None and restarted != stored:
        errors.append(('state', 'restart_mismatch', ''))
    return mapping, [{'check': check, 'code': code, 'detail': detail} for check, code, detail in errors]


def resolve_fixture(value, mapping):
    """Replace @alias targets with the stored category_id of the previous turn."""
    value = deepcopy(value)
    for edit in value.get('requirement_edits', ()):
        edit['targets'] = [mapping[t[1:]]['category_id'] if t.startswith('@') and t[1:] in mapping else t
                           for t in edit['targets']]
    return value


def retry_turn(coordinator, actor, body, attempts, retry_transient=0):
    """Retry only uncommitted pre-task turns, retaining each failure as evidence."""
    for attempt in range(retry_transient + 1):
        started = time.monotonic()
        try:
            result = coordinator.turn(actor, 'evaluation', body)
        except ServiceError as error:
            attempts.append({'status': 'failed', 'error_code': error.code, 'message': error.message,
                             'seconds': round(time.monotonic() - started, 3)})
            if error.code not in TRANSIENT_ERRORS or attempt == retry_transient:
                raise
        else:
            attempts.append({'status': 'completed', 'seconds': round(time.monotonic() - started, 3)})
            return result


def outcome(row):
    if row.get('error_code'):
        if row['error_code'] in TRANSIENT_ERRORS:
            return 'service_failure'
        return 'save_rejected' if row['error_code'] == 'MODEL_OUTPUT_INVALID' else 'error'
    if PROTOCOL_FALLBACK in (row.get('reply') or ''):
        return 'protocol_failure'
    if row['errors']:
        return 'semantic_failure'
    if len(row['attempts']) > 1:
        return 'passed_after_retry'
    return 'passed_after_repair' if row['rejected_proposals'] else 'passed_first'


def attribution(row, mode):
    """First-pass cause hint; fixture mode isolates backend merge/projection."""
    if row['outcome'].startswith('passed'):
        return None
    if row['outcome'] == 'service_failure':
        return 'service'
    if mode == 'fixture':
        return 'backend'
    if not row['model_called']:
        return 'deterministic_path'
    return 'model_protocol' if row['outcome'] in ('protocol_failure', 'save_rejected') else 'model_or_context'


def evaluate(runtime, llm, scenarios, *, mode='real', transport=None, retry_transient=0, output=None, model_name=None):
    rows = []
    with tempfile.TemporaryDirectory(prefix='requirement-eval-') as directory:
        actor = Actor('evaluation', 'requester')
        projects = LocalProjectAccess([('evaluation', 'requester', 'evaluation', 'default')])
        path = Path(directory) / 'dialogue.sqlite'
        store = DialogueStore(path)
        coordinator = DialogueCoordinator(store, runtime, projects)
        for scenario in scenarios:
            previous_result, previous, mapping = None, None, {}
            failed = False
            for index, (turn, expect) in enumerate(zip(scenario['turns'], expected_states(scenario))):
                row = {'scenario': scenario['id'], 'group': scenario['group'], 'turn': index + 1,
                       'prefix': bool(turn.get('prefix')), 'message': turn['message'], 'attempts': [], 'errors': []}
                if failed:
                    row.update(outcome='not_run', attribution=None)
                    rows.append(row)
                    continue
                runtime.reset_observation()
                if transport is not None:
                    transport.outputs = [resolve_fixture(turn['fixture'], mapping)] if turn.get('fixture') else []
                started = time.monotonic()
                try:
                    result = retry_turn(coordinator, actor, {
                        'session_id': previous_result['session_id'] if previous_result else None,
                        'expected_version': previous_result['version'] if previous_result else 0,
                        'phase': previous_result['stage'] if previous_result else 'request',
                        'llm_id': llm, 'message': turn['message'], 'generate_terms_when_complete': True},
                        row['attempts'], retry_transient)
                except ServiceError as error:
                    row.update(error_code=error.code, error_message=error.message)
                else:
                    stored = stored_view(store.read(actor, 'evaluation', result['session_id']))
                    fresh = DialogueStore(path)
                    try:
                        restarted = stored_view(fresh.read(actor, 'evaluation', result['session_id']))
                    finally:
                        fresh.close()
                    mapping_now, row['errors'] = check_turn(expect, stored, result, previous, mapping, restarted)
                    row.update(action=result['action'], stage=result['stage'], question=result['question'],
                               reply=result['reply'], stored=stored)
                    previous_result, previous, mapping = result, stored, mapping_now
                row.update(model_called=runtime.model_called, rejected_proposals=runtime.rejected,
                           accepted_proposal=runtime.accepted, seconds=round(time.monotonic() - started, 3))
                row['outcome'] = outcome(row)
                row['attribution'] = attribution(row, mode)
                failed = not row['outcome'].startswith('passed')
                rows.append(row)
                if output is not None:
                    write_report(output, rows, mode, model_name)
                print(json.dumps({'scenario': row['scenario'], 'turn': row['turn'], 'outcome': row['outcome'],
                                  'codes': [e['code'] for e in row['errors']], 'seconds': row['seconds']},
                                 ensure_ascii=False), flush=True)
        store.close()
    if output is not None:
        write_report(output, rows, mode, model_name)
    return rows


def summarize(rows):
    scenarios = {}
    for row in rows:
        scenarios.setdefault(row['scenario'], []).append(row)
    first_failure = {sid: next((r for r in items if not r['outcome'].startswith('passed')), None)
                     for sid, items in scenarios.items()}
    return {
        'scenarios': len(scenarios), 'turns': len(rows),
        'scenarios_passed': sorted(sid for sid, failure in first_failure.items() if failure is None),
        'turn_outcomes': dict(Counter(r['outcome'] for r in rows)),
        'failed_checks': dict(Counter(e['check'] for r in rows for e in r['errors'])),
        'failed_codes': dict(Counter(e['code'] for r in rows for e in r['errors'])),
        'attribution': dict(Counter(r['attribution'] for r in rows if r.get('attribution'))),
        'by_group': {group: {'scenarios': len({r['scenario'] for r in rows if r['group'] == group}),
                             'passed': sum(1 for sid, f in first_failure.items() if f is None
                                           and scenarios[sid][0]['group'] == group)}
                     for group in dict.fromkeys(r['group'] for r in rows)},
        'first_failures': [{'scenario': sid, 'turn': f['turn'], 'message': f['message'], 'outcome': f['outcome'],
                            'attribution': f['attribution'], 'error_code': f.get('error_code'),
                            'codes': [e['code'] + (':' + e['detail'] if e['detail'] else '') for e in f['errors']]}
                           for sid, f in first_failure.items() if f is not None]}


def write_report(output, rows, mode, model_name):
    scope = ('scripted ideal proposals; no network; isolated pre-task ledger' if mode == 'fixture'
             else 'synthetic requests; real model; isolated pre-task ledger')
    output.write_text(json.dumps({'scope': scope, 'mode': mode, 'model': model_name,
                                  'summary': summarize(rows), 'turns': rows}, ensure_ascii=False, indent=2) + '\n')


def fixture_runtime():
    transport = ScriptedTransport()
    runtime = ObservedRuntime((ModelProfile('fixture', 'llm',
        ModelConfig('fixture', 'fixture-v1', 'https://fixture.invalid/v1', 'fixture')),), opener=transport)
    return runtime, transport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenarios', type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument('--only', help='comma-separated scenario ids, e.g. A1,C1')
    parser.add_argument('--group', action='append', help='scenario group name; repeatable')
    parser.add_argument('--fixture', action='store_true', help='replay ideal fixture proposals; no model call')
    parser.add_argument('--settings-store', type=Path)
    parser.add_argument('--key-file', type=Path)
    parser.add_argument('--allowed-url', action='append')
    parser.add_argument('--tenant')
    parser.add_argument('--user')
    parser.add_argument('--llm')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--retry-transient', type=int, choices=range(3), default=0,
                        help='explicit bounded retries of failed pre-task turns; all attempts remain in the report')
    args = parser.parse_args()
    scenarios = load_scenarios(args.scenarios, only=set(args.only.split(',')) if args.only else None,
                               groups=set(args.group) if args.group else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.fixture:
        runtime, transport = fixture_runtime()
        rows = evaluate(runtime, 'fixture', scenarios, mode='fixture', transport=transport,
                        output=args.output, model_name='fixture')
    else:
        missing = [name for name in ('settings_store', 'key_file', 'allowed_url', 'tenant', 'user') if not getattr(args, name)]
        if missing:
            parser.error('Real mode requires ' + ', '.join('--' + m.replace('_', '-') for m in missing))
        if not args.settings_store.is_file() or not args.key_file.is_file():
            parser.error('Existing settings and key files are required')
        from dsh_retrieval.model_settings import ModelSettings
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
                rows = evaluate(runtime, llm, scenarios, retry_transient=args.retry_transient,
                                output=args.output, model_name=profile.config.model)
            settings.db.close()
    summary = summarize(rows)
    print(json.dumps({k: summary[k] for k in ('scenarios', 'turns', 'turn_outcomes', 'failed_checks', 'attribution')},
                     ensure_ascii=False))
    return 0 if len(summary['scenarios_passed']) == summary['scenarios'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
