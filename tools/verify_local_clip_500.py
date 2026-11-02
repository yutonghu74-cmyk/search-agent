"""Isolated smoke check for a generated local CLIP index and authorized preview.

Run with PYTHONPATH=src:tests and the same Python environment used for CLIP.
No existing task or user ledger is touched; the temporary task is not retained.
"""
import argparse
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time

from dsh_retrieval.adapters.projects import LocalProjectAccess
from dsh_retrieval.adapters.task_storage.sqlite import SQLiteTaskRepository
from dsh_retrieval.first_search_local import LocalFirstSearch
from dsh_retrieval.local_catalog import build_local_catalog
from dsh_retrieval.local_test_media import LocalTestMediaCorpus
from dsh_retrieval.model_runtime import ModelRuntime
from dsh_retrieval.models import Actor
from dsh_retrieval.service import TaskService
from draft_helpers import BODY, confirmation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--vlm-settings', type=Path,
                        help='existing encrypted model settings; copied into the temporary test directory')
    parser.add_argument('--model-key-file', type=Path)
    parser.add_argument('--model-allowed-url')
    args = parser.parse_args()
    if bool(args.vlm_settings) != bool(args.model_key_file) or bool(args.vlm_settings) != bool(args.model_allowed_url):
        parser.error('VLM test requires --vlm-settings, --model-key-file and --model-allowed-url together')
    with TemporaryDirectory() as directory:
        directory = Path(directory)
        actor = Actor('tenant-a', 'user-a')
        runtime = ModelRuntime()
        if args.vlm_settings:
            from dsh_retrieval.model_settings import ModelSettings
            clone = directory / 'model-settings.sqlite3'
            with sqlite3.connect(args.vlm_settings) as source, sqlite3.connect(clone) as target:
                source.backup(target)
            runtime.settings = ModelSettings(clone, args.model_key_file,
                                             [args.model_allowed_url])
        service = TaskService(SQLiteTaskRepository(directory / 'tasks.sqlite3'),
            LocalProjectAccess([('tenant-a', 'user-a', 'demo', 'default')]))
        body = json.loads(json.dumps(BODY))
        body['query_spec']['raw_request'] = args.query
        body['query_spec']['target_count'] = 1
        body['query_spec']['refill_policy']['target_count'] = 1
        if args.vlm_settings:
            from dsh_retrieval.expansion_models import preview_edited_groups
            with runtime.settings.scope(actor.tenant_id, actor.user_id):
                defaults = runtime.settings.defaults()
                plan = preview_edited_groups(None, [{
                    'primary_label': args.query, 'auxiliary_terms': []}])
                body['query_spec']['grouped_expansion'] = runtime.bind(plan,
                    runtime.binding(defaults['llm'], defaults['vlm']))['plan']
            body['query_spec']['rules'] = [{
                'rule_id': 'semantic-match', 'predicate_ref': 'image.semantic-match@1',
                'operator': 'eq', 'value': True, 'hardness': 'hard',
                'origin': 'user_confirmed'}]
        created, _ = service.create_task(actor, 'clip-smoke',
            confirmation(service, actor, body=body))
        task_id = created['task_id']
        configs = build_local_catalog(args.index / 'catalog.json',
                                      directory / 'catalog.sqlite3', runtime)
        fixture_path = args.index / 'fixture.json'
        if args.vlm_settings:
            fixture = json.loads(fixture_path.read_text())
            fixture['max_depth'] = 1  # One external VLM image call in this smoke test.
            fixture_path = directory / 'fixture.json'
            fixture_path.write_text(json.dumps(fixture))
        local = LocalFirstSearch(service, configs, ledger_path=directory / 'tasks.sqlite3',
            source_path=args.index / 'catalog.json',
            capabilities_path=args.index / 'capabilities.json',
            fixture_path=fixture_path,
            cursor_secret=b'local-clip-isolated-smoke-secret-123',
            clip_model_path=args.model)
        try:
            local.media_gateway = LocalTestMediaCorpus(args.manifest, service)
            preview = local.preview(actor, task_id)
            if any(route['availability'] != 'available' for route in preview['routes']):
                raise RuntimeError(f'Query unavailable: {preview["routes"]}')
            started = local.auto_start(actor, task_id, key='clip-smoke-start')
            if started['status'] != 'queued':
                raise RuntimeError(f'CLIP search did not queue: {started}')
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                progress = local.progress(actor, task_id)
                if progress['pagination_stopped']:
                    break
                time.sleep(.2)
            if not progress['pagination_stopped']:
                raise RuntimeError('CLIP search did not finish within 90 seconds')
            candidates = local.candidates(actor, task_id, limit=5)
            if not candidates['items'] or candidates['accepted_count'] is not None:
                raise RuntimeError('Candidate and VLM verdict boundaries are incorrect')
            first = candidates['items'][0]
            media_type, content = local.candidate_preview(actor, task_id,
                first['candidate_id'], ref=first['preview_ref'])
            manifest = local.preview_manifest(actor, task_id)
            outcome = {
                'query': args.query, 'mode': preview['mode'],
                'search_stopped': progress['pagination_stopped'],
                'candidate_count': candidates['raw_hit_count'],
                'first_candidate_id': first['candidate_id'],
                'first_preview_type': media_type, 'first_preview_bytes': len(content),
                'verified_manifest_count': manifest['verified_count'],
                'accepted_count': candidates['accepted_count'],
            }
            if args.vlm_settings:
                from dsh_retrieval.adapters.task_storage.multi_rule import SQLiteMultiRuleApprovalRepository
                from dsh_retrieval.confirmed_cleaning import ConfirmedCleaningService
                from dsh_retrieval.vlm_registry import ConfirmedTaskVLMRegistry
                published, _ = local.confirm_manifest(actor, task_id,
                    proposal_digest=manifest['proposal_digest'],
                    expected_task_version=manifest['expected_task_version'],
                    key='clip-smoke-manifest', action='confirm')
                repository = SQLiteMultiRuleApprovalRepository(directory / 'tasks.sqlite3',
                    projects=service.projects, authorization=local._authority())
                cleaning = ConfirmedCleaningService(repository,
                    registry_provider=ConfirmedTaskVLMRegistry(runtime),
                    gateway=local.media_gateway)
                proposal = cleaning.prepare(actor, task_id, published['manifest_ref'])
                approval, _ = cleaning.confirm(actor, task_id, proposal['proposal_digest'],
                    key='clip-smoke-cleaning',
                    expected_task_version=proposal['expected_task_version'], action='confirm')
                result, _ = cleaning.execute(actor, task_id, approval['operation_id'])
                outcome['vlm_accepted_count'] = result['accepted_count']
                outcome['vlm_steps_available'] = cleaning.steps(actor, task_id)['available']
            print(json.dumps(outcome, ensure_ascii=False))
        finally:
            local.close()


if __name__ == '__main__':
    main()
