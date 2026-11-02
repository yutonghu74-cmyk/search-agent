"""Deterministic offline "crawler" that synthesizes a multi-type image corpus.

This is a dev-only fixture generator. It never touches the network and never
reads or writes credentials, intranet addresses or real business data (see
AGENTS.md). It simulates crawling several different image data types
(animals/dogs, animals/cats, vehicles/cars, scenery/landscapes) and builds them
into the trusted JSON trio consumed by ``LocalFirstSearch`` (LOCAL_EXACT):

* ``local-catalog@1``    -- one :class:`Library` per data type, each with its own
  :class:`IndexBinding` (distinct encoder, vector dimension and metric).
* ``text-query-capabilities@1`` -- one capability registration per binding.
* ``local-first-search@1`` -- exact query vectors, explicit word rules and
  records with media mappings whose ``content_digest`` is a real SHA-256.

Run as a CLI::

    python3 -m dsh_retrieval.crawled_corpus --out DIR [--seed N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

SCHEMA_CATALOG = "local-catalog@1"
SCHEMA_CAPABILITIES = "text-query-capabilities@1"
SCHEMA_FIXTURE = "local-first-search@1"

ORIGINAL_QUERY = "找图片"
FILTER_TEMPLATE = "demo-public"
SCOPE = "public"
TENANT, OWNER, PROJECT = "tenant-a", "user-a", "demo"

# One entry per "crawled" data type. metric and vector_dimension deliberately
# differ so that a single broad query exercises heterogeneous indexes.
DATA_TYPES = (
    {
        "library_id": "dogs", "label": "动物·狗",
        "description": "找图片；动物图片；狗图片；宠物图片",
        "binding_id": "dog-v1", "index_version": "local-dog-v1",
        "encoder_ref": "local-dog-encoder-v1",
        "vector_dimension": 2, "metric": "COSINE",
        "tokens": ["狗", "犬", "动物", "宠物"],
        "specific_text": "找狗图片", "prototype": [1.0, 0.0],
    },
    {
        "library_id": "cats", "label": "动物·猫",
        "description": "找图片；动物图片；猫图片；宠物图片",
        "binding_id": "cat-v1", "index_version": "local-cat-v1",
        "encoder_ref": "local-cat-encoder-v1",
        "vector_dimension": 2, "metric": "L2",
        "tokens": ["猫", "动物", "宠物"],
        "specific_text": "找猫图片", "prototype": [0.0, 1.0],
    },
    {
        "library_id": "cars", "label": "车辆·汽车",
        "description": "找图片；车辆图片；汽车图片",
        "binding_id": "car-v1", "index_version": "local-car-v1",
        "encoder_ref": "local-car-encoder-v1",
        "vector_dimension": 3, "metric": "IP",
        "tokens": ["车", "汽车", "车辆", "轿车"],
        "specific_text": "找汽车图片", "prototype": [1.0, 0.5, -0.25],
    },
    {
        "library_id": "landscapes", "label": "风景·山水",
        "description": "找图片；风景图片；山水图片",
        "binding_id": "landscape-v1", "index_version": "local-landscape-v1",
        "encoder_ref": "local-landscape-encoder-v1",
        "vector_dimension": 4, "metric": "COSINE",
        "tokens": ["风景", "山水", "自然"],
        "specific_text": "找风景图片", "prototype": [1.0, 0.0, 0.0, 0.0],
    },
)

RECORDS_PER_TYPE = 3
MAX_DEPTH = 20
MAX_BATCH_SIZE = 4


def _binding(spec: dict) -> dict:
    return {
        "binding_id": spec["binding_id"],
        "library_id": spec["library_id"],
        "index_version": spec["index_version"],
        "encoder_ref": spec["encoder_ref"],
        "vector_dimension": spec["vector_dimension"],
        "metric": spec["metric"],
    }


def _record_vector(spec: dict, index: int, seed: int) -> list[float]:
    """Deterministic perturbation of a type prototype; normalized for COSINE."""
    rng = random.Random(f"{seed}|{spec['library_id']}|record|{index}")
    vector = [p + rng.uniform(-0.4, 0.4) for p in spec["prototype"]]
    if spec["metric"] == "COSINE":
        norm = math.sqrt(sum(value * value for value in vector))
        if norm < 1e-6:
            vector = list(spec["prototype"])
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        vector = [value / norm for value in vector]
    return [round(value, 6) for value in vector]


def build_crawled_corpus(
    out_dir,
    *,
    seed: int = 0,
    names: tuple[str, str, str] = ("catalog.json", "capabilities.json", "fixture.json"),
) -> dict:
    """Emit catalog/capabilities/fixture into ``out_dir`` and return the trio."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    catalog_name, capabilities_name, fixture_name = names

    libraries, capability_items = [], []
    query_vectors, query_rules, records = [], [], []
    for spec in DATA_TYPES:
        binding = _binding(spec)
        libraries.append({
            "library_id": spec["library_id"], "description": spec["description"],
            "media_type": "image", "bindings": [binding],
        })
        capability_items.append({
            "binding": binding, "media_type": "image",
            "filter_template_ref": FILTER_TEMPLATE, "max_batch_size": MAX_BATCH_SIZE,
        })
        # A broad original query and a type-specific query each carry an exact
        # vector; explicit word rules cover the remaining guided terms.
        query_vectors.append({"binding_id": spec["binding_id"], "text": ORIGINAL_QUERY,
                              "values": list(spec["prototype"])})
        query_vectors.append({"binding_id": spec["binding_id"], "text": spec["specific_text"],
                              "values": list(spec["prototype"])})
        query_rules.append({"binding_id": spec["binding_id"], "tokens": spec["tokens"],
                            "values": list(spec["prototype"])})
        for index in range(1, RECORDS_PER_TYPE + 1):
            prefix = spec["library_id"]
            content_digest = hashlib.sha256(f"{prefix}-{index}".encode()).hexdigest()
            records.append({
                "binding_id": spec["binding_id"], "vector_id": index,
                "values": _record_vector(spec, index, seed), "scope": SCOPE,
                "media": [{
                    "media_unit_id": f"{prefix}-{index}", "asset_version": "v1",
                    "source_namespace": f"crawled-{prefix}",
                    "source_asset_id": f"{prefix}-asset-{index}",
                    "authorized_media_ref": f"media:{prefix}-{index}",
                    "content_digest": "sha256:" + content_digest,
                }],
            })

    catalog = {
        "schema": SCHEMA_CATALOG, "libraries": libraries,
        "grants": [{"tenant_id": TENANT, "owner_id": OWNER, "project_id": PROJECT,
                    "library_ids": [spec["library_id"] for spec in DATA_TYPES],
                    "can_configure": True}],
    }
    capabilities = {"schema": SCHEMA_CAPABILITIES, "items": capability_items}
    fixture = {
        "schema": SCHEMA_FIXTURE, "max_depth": MAX_DEPTH,
        "filter_templates": {FILTER_TEMPLATE: [SCOPE]},
        "query_vectors": query_vectors, "query_rules": query_rules, "records": records,
    }

    (out / catalog_name).write_text(json.dumps(catalog, ensure_ascii=False, indent=2))
    (out / capabilities_name).write_text(json.dumps(capabilities, ensure_ascii=False, indent=2))
    (out / fixture_name).write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
    return {"catalog": catalog, "capabilities": capabilities, "fixture": fixture}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True,
                        help="directory to write catalog/capabilities/fixture")
    parser.add_argument("--seed", type=int, default=0,
                        help="deterministic crawl seed (default 0)")
    args = parser.parse_args(argv)
    build_crawled_corpus(args.out, seed=args.seed)
    print(f"wrote crawled corpus to {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
