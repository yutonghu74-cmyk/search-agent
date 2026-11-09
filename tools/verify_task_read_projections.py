"""Offline semantic comparison against the authoritative copied ledgers."""
import json

from dsh_retrieval.adapters.task_storage.sqlite import encode, decode
from dsh_retrieval.node_read_views import node_sources
from dsh_retrieval.result_views import build_result_page, VIEWS as RESULT_VIEWS


def verify(repository):
    def compare(memory, retrieval, candidates, state, db):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        checked = {'tasks': 0, 'result_views': 0, 'result_items': 0, 'node_items': 0, 'step_items': 0}
        def equal(actual, expected, label):
            if actual != expected:
                raise ValueError('migration semantic mismatch: ' + label)
        expected_chats = set()
        for cid, messages in memory._conversation_messages.items():
            if not messages:
                continue
            first = min(messages.values(), key=lambda m: (m.created_at, m.message_id))
            from dsh_retrieval.models import Actor
            summary = memory.get_conversation(Actor(first.tenant_id, first.owner_user_id), cid).to_summary()
            row = db.execute('SELECT tenant_id,user_id,project_id,summary_json FROM chat_read_summaries WHERE chat_id=?', (cid,)).fetchone()
            if row is None:
                raise ValueError('migration chat summary missing')
            equal(row[:3], (first.tenant_id, first.owner_user_id, first.project_id), 'chat owner')
            actual = json.loads(row[3])
            equal({key: actual[key] for key in summary}, summary, 'chat summary counts and dates')
            expected_chats.add(cid)
        expected_chats.update(task.task_id for task in memory._tasks.values() if not task.conversation_id)
        equal({r[0] for r in db.execute('SELECT chat_id FROM chat_read_summaries')}, expected_chats, 'chat completeness')
        for task_id, task in memory._tasks.items():
            binding = db.execute('SELECT task_json FROM task_read_summaries WHERE task_id=?', (task_id,)).fetchone()
            if binding is None:
                raise ValueError('migration task binding is missing')
            equal(json.loads(binding[0]), encode(task), 'task binding')
            for node, (summary, items) in node_sources(db, task, tables).items():
                head = db.execute('SELECT item_count,summary_json FROM task_node_read_heads WHERE task_id=? AND node=?', (task_id,node)).fetchone()
                if head is None:
                    raise ValueError('migration node head is missing')
                equal(head[0],len(items),'node count')
                equal(json.loads(head[1]),summary,'node summary')
                rows = [json.loads(r[0]) for r in db.execute('SELECT item_json FROM task_node_read_items WHERE task_id=? AND node=? ORDER BY ordinal', (task_id,node))]
                equal(rows,items,'node items')
                checked['node_items'] += len(rows)
            result_id = state['current_result_sets'].get(task_id)
            if result_id:
                preview, version = state['result_sets'][result_id]
                original = repository._result_set_view(result_id,preview,version)
                original.update(repository._result_audit_view(state,preview))
                snap = db.execute('SELECT payload FROM task_result_read_snapshots WHERE task_id=?', (task_id,)).fetchone()
                equal(decode(json.loads(snap[0])), original, 'result snapshot and lineage')
                for category in sorted(RESULT_VIEWS):
                    # Same public business builder as the compatible pre-projection reader.
                    expected = build_result_page({**task.to_summary(),'task_version':version},json.loads(task.confirmed_proposal_json),original,
                        view_type=category,limit=1_000_000,_projection=True)
                    items = expected.pop('items');expected.pop('next_cursor')
                    head = db.execute('SELECT item_count,metadata_json FROM task_result_read_views WHERE task_id=? AND view_type=?', (task_id,category)).fetchone()
                    equal(head[0],len(items),'result count')
                    equal(json.loads(head[1]),expected,'result metadata, quotas and gap')
                    rows = [json.loads(r[0]) for r in db.execute('SELECT item_json FROM task_result_read_items WHERE task_id=? AND view_type=? ORDER BY position',(task_id,category))]
                    equal(rows,items,'result ordered items and reasons')
                    checked['result_views'] += 1;checked['result_items'] += len(rows)
                expected_bindings = set()
                for operation_id in preview.source_operation_ids or (preview.operation_id,):
                    approval = state['approvals'][operation_id]
                    manifest_ref = approval.proposal.manifest_ref
                    manifest = (candidates['manifests'].get(manifest_ref)
                        or candidates['_plan_sets']['candidate_manifests'].get(manifest_ref)
                        or state['recompute_manifests'].get(manifest_ref))
                    for item in manifest.proposal.items:
                        expected_bindings.add((item.candidate_id,operation_id,item.media.asset_version,item.media.content_digest,item.media.authorized_media_ref))
                actual = set(db.execute('SELECT candidate_id,operation_id,asset_version,content_digest,media_ref FROM task_asset_read_bindings WHERE task_id=?',(task_id,)))
                equal(actual,expected_bindings,'media ownership and digest')
            event = db.execute('''SELECT s.snapshot_json,m.sequence,o.sequence
                FROM task_event_state s JOIN task_event_task_mutations m ON m.task_id=s.task_id
                JOIN task_event_observed_mutations o ON o.task_id=s.task_id WHERE s.task_id=?''',
                (task_id,)).fetchone()
            if event is None or event[1] != event[2] or 'node_versions' not in json.loads(event[0]):
                raise ValueError('migrated task event cannot be replayed')
            equal(json.loads(event[0])['binding'],
                  {'task_id':task_id, 'revision':task.confirmed_revision,
                   'generation':task.run_generation, 'task_version':task.task_version},
                  'event current binding')
            checked['tasks'] += 1
        from dsh_retrieval.cleaning_step_views import cleaning_checkpoint_view, cleaning_step_view
        for task_id, operation_id, payload in db.execute('SELECT task_id,operation_id,summary_json FROM task_step_read_runs'):
            approval = state['approvals'][operation_id]
            result = state['results'].get(operation_id)
            run = cleaning_step_view(approval.job,result) if result else cleaning_checkpoint_view(approval.job,repository._checkpoint_record(db,approval.job))
            saved = json.loads(payload)
            equal([s['counts'] for s in saved['steps']],[s['counts'] for s in run['steps']],'step counts')
            for step in run['steps']:
                rows = [json.loads(r[0]) for r in db.execute('SELECT item_json FROM task_step_read_items WHERE task_id=? AND operation_id=? AND step_execution_id=? ORDER BY position',(task_id,operation_id,step['step_execution_id']))]
                equal(rows,step['items'],'step verdicts and evidence')
                checked['step_items'] += len(rows)
        return checked
    return repository._transaction(False,compare,with_connection=True)
