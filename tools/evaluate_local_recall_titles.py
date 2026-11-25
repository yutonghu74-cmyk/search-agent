"""Offline known-item title check, NOT semantic recall or cleaned-data acceptance."""
import argparse
import json
from pathlib import Path
import time

from dsh_retrieval.adapters.retrieval.local_vector import LocalVectorRecord, LocalVectorRetrieval
from dsh_retrieval.adapters.retrieval.simulated import ApprovedFixture
from dsh_retrieval.integration_models import AccessContext, IndexBinding, Library, SearchRequest
from dsh_retrieval.local_clip import LocalCLIPEncoder
from dsh_retrieval.models import Actor


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--capabilities', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    fixture = json.loads(args.fixture.read_text())
    caps = json.loads(args.capabilities.read_text())['items']
    binding = IndexBinding(**caps[0]['binding'])
    records = tuple(LocalVectorRecord(binding, r['vector_id'], tuple(r['values']), r['scope'],
                                     search_text=r.get('search_text', ''))
                    for r in fixture['records'] if r['binding_id'] == binding.binding_id)
    eligible = [r for r in records if r.scope == 'public' and r.search_text]
    if len(eligible) < 10:
        raise ValueError('10 public records with original title metadata required')
    context = AccessContext(Actor('evaluation', 'evaluation'), 'isolated-title-check', 'search', 'local')

    class Authority:
        def require_access(self, selected, resource_ref, action):
            if (selected != context or resource_ref != 'library:' + binding.library_id
                    or action != 'search'):
                raise ValueError('outside isolated local evaluation scope')

    encoder = LocalCLIPEncoder(args.model)
    rows = []
    for i in range(10):
        chosen = eligible[i * len(eligible) // 10]
        query = chosen.search_text
        vector = encoder.encode_query(query, binding)
        request = SearchRequest('title-' + str(i), 1, binding, vector, 'public-only', 10)
        common = dict(libraries=(Library(binding.library_id, 'local evaluation', 'image', (binding,)),),
                      query_vectors={}, approved=(ApprovedFixture(context, request, (), len(records)),),
                      records=records, filter_templates={'public-only': frozenset({'public'})},
                      cursor_secret=b'isolated-evaluation-cursor-key-01')
        row = {'sample': i + 1}
        for name, texts in [('vector', {}), ('hybrid', {request.plan_id: query})]:
            adapter = LocalVectorRetrieval(Authority(), **common, query_texts=texts)
            start = time.perf_counter()
            page = adapter.search(context, request)
            ids = [h.vector_id for h in page.hits]
            row[name + '_hit_at_10'] = chosen.vector_id in ids
            row[name + '_ms'] = round((time.perf_counter() - start) * 1000, 3)
        rows.append(row)
    report = {'kind': 'known-item-title-check', 'samples': 10,
              'vector_hits_at_10': sum(r['vector_hit_at_10'] for r in rows),
              'hybrid_hits_at_10': sum(r['hybrid_hit_at_10'] for r in rows),
              'semantic_recall_measured': False, 'vlm_calls': 0, 'rows': rows}
    with args.output.open('x') as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
