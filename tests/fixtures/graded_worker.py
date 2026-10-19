#!/usr/bin/env python3
"""Native-boundary C6 worker fixture. No model, NPU or hardware claim."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mindie_knowledge.graded.distill import validate_request
from mindie_knowledge.materials.summarizer import canonical, digest, outcome, policy_identity

IDENTITY = policy_identity(
    model="fixture-no-model", effort="none", implementation={"fixture": "dsh-graded-worker-v1"},
)
IDENTITY["capabilities"] = ["mindie-distill-request/1"]
IDENTITY["fingerprint"] = digest({key: value for key, value in IDENTITY.items() if key != "fingerprint"})

if "--identity" in sys.argv:
    print(canonical(IDENTITY))
    raise SystemExit(0)

request = validate_request(json.load(sys.stdin))
marker = os.environ.get("MINDIE_GRADED_FIXTURE_CALLS")
if marker:
    path = Path(marker)
    path.write_text(str(int(path.read_text()) + 1) if path.exists() else "1")

source = request["sanitized_inputs"][0]
frontmatter = {
    "schema": "mindie-graded-unit/1",
    "id": "unit-fixture-dsh-distill",
    "tier": "L2",
    "category": "sop",
    "evidence_strength": "single_observation",
    "domain_path": "serving/graph",
    "inherits_from": [],
    "title": "Fixture DSH distill probe",
    "triggers": {"error_signatures": [], "keywords": ["graph", "dsh"]},
    "conditions": {"soc": ["Ascend910B"]},
    "provenance": {
        "source_type": "session_distill",
        "source_locator": source["source_locator"],
        "source_digest": source["source_digest"],
        "evidence_refs": source["evidence_refs"],
        "supersedes": [],
    },
}
decision = {
    "schema_version": "mindie-distill-decision/1",
    "session_digest": request["session_digest"],
    "evidence_inputs": request["evidence_inputs"],
    "action": "propose_graded_units",
    "drop_reason": None,
    "units": [
        {
            "target_unit_id": frontmatter["id"],
            "target_tier": "L2",
            "category": "sop",
            "evidence_strength": "single_observation",
            "domain_path": frontmatter["domain_path"],
            "operation": "create",
            "frontmatter": frontmatter,
            "markdown_body": "Fixture DSH distill probe; no hardware claim.\n",
        }
    ],
}
print(canonical(outcome(request, status="returned", result=decision, model_calls=1, usage_known=False, elapsed_ms=1)))
