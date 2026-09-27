# Phase 2: Ontology (Simula, lean)

- `factors`: factor disentanglement from the PRD (human accept/reject)
- `taxonomy`: breadth-first expansion, Best-of-N proposals + critic (hackathon: depth 2, Best-of-3, one critic)
- `sampling`: sampling strategies and mixes -> objectives
- `schema`: inferred classes, properties, SHACL shapes and alignment; DoD criteria compiled to queries
- `recommendations`: intake of evidence-backed ontology recommendations from Phase 4; accepted ones version the ontology

When schema proposals remain semantically invalid after three attempts, P2 preserves the otherwise valid ontology and DoD queries. It moves only invalid executable rules or typed relations to `02-ontology/recommendations/unresolved.json`. Each set-aside item records its `kind`, stable `id`, validator `reason`, and full original `proposal`; the artifact also records `schema_version`, `ontology_path`, and `generated_by`. A later successful clean draft writes `unresolved: []` so a reviewer does not see old objections as current. The artifact follows `schemas/ontology-recommendations.schema.json`.
