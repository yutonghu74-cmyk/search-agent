"""Deterministic owner review digests over original plans and page manifests."""
from .candidate_contracts import merge_manifest_items
from .query_spec import digest

def collection_review(value, proposals):
    links = value['links']
    return {'checkpoint': 'candidates', 'proposal_digest': digest({
        'kind': 'agent-job-candidates@2', 'plan': links['approved_digest'],
        'pages': [p.proposal_digest for p in proposals], 'max_pages': links['max_pages'],
        'target_count': links['target_count'], 'stop_reason': links['stop_reason']}),
        'expected_task_version': value['task'].task_version,
        'candidate_count': len(merge_manifest_items(proposals)),
        'raw_hit_count': sum(len(i.hits) for p in proposals for i in p.items),
        'page_count': len(proposals), 'stop_reason': links['stop_reason'], 'accepted_count': None}


def retrieval_review_digest(value):
    return digest({'kind': 'agent-job-retrieval-review@2', 'plan': value['proposal'].proposal_digest,
        'max_pages': value['links']['max_pages'], 'target_count': value['links']['target_count']})
