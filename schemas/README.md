# Ontofill contract schemas

These Draft 2020-12 JSON Schemas formalize the engine CLI, case documents,
lake pointer, and generic gold export in `coord/CONTRACT.md` sections 2–4 and 11.
Each line of `entities.jsonl` and `trace.jsonl` is validated as a separate JSON
object. `metrics.json` and `gold/<case_id>/latest.json` are single objects.
`lake-pointer.schema.json` validates `lake.yaml` **after YAML parsing**;
the YAML file contains environment variable names, never credential values.
Its `case_id` is a safe path segment. Bronze can point to an S3 API or a local
directory with `kind: file`; both use `bronze/sha256/<hex>` for the object and
`bronze/sha256/<hex>.meta.json` for its sidecar. S3 credentials use the standard
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables.

| File | Document |
| --- | --- |
| `run-request.schema.json` | Parsed `ontofill run`, `refine`, or `export` arguments |
| `global-prd.schema.json` | Phase 1 PRD, including publisher authority policy |
| `factors.schema.json` | Phase 2 proposed factors with kind and evidence |
| `ontology.schema.json` | Phase 2 taxonomies, classes, properties, relations, rules, and SHACL path |
| `dod-queries.schema.json` | Safe declarative queries compiled from PRD criteria |
| `objectives.schema.json` | Phase 3 discovered source objectives |
| `local-prd.schema.json` | Phase 4 requirements for a source and objective |
| `tdd.schema.json` | Phase 4 technical definition document |
| `approval-pending.schema.json` | Structured metadata rendered in `APPROVAL_PENDING.md` |
| `approved.schema.json` | JSON content of the `APPROVED` marker (approver and date) |
| `entity.schema.json` | One generic gold entity row |
| `supplier.schema.json`, `contract.schema.json` | Legacy case-specific rows, superseded by entities |
| `trace-step.schema.json` | One trace step row, with optional inference usage |
| `run-status.schema.json` | Live run status and partial metric snapshot |
| `metrics.schema.json` | Generic class, property, source, and DoD metrics snapshot |
| `latest.schema.json` | Current gold run pointer |
| `lake-pointer.schema.json` | Parsed lake configuration |
| `bronze-sidecar.schema.json` | Metadata alongside each content-addressed bronze object |

`common.schema.json` holds shared IDs, evidence, and field shapes. Gold and
conflict values require a `val:` ID and a bronze capture with a screenshot.
Missing entity properties remain present as `status: "missing"`, `value: null`,
and an empty evidence array. Values may be strings, numbers, or booleans.
A trace row uses null source, objective, or TDD paths
when the early phase has not produced those documents yet. Cross-document
references (value → step → TDD → objective → ontology → PRD → brief), ontology
class/property references, the sum of metric counts, and whether a step's
starting mode belongs to its allowed modes
require a separate semantic check; JSON Schema cannot establish them alone.

`APPROVAL_PENDING.md` starts with YAML front matter validated by the pending
schema and then continues with human-readable Markdown. `APPROVED` contains a
JSON object with approver and date; factor approvals may also include a
`decisions` map of factor IDs to `accept` or `reject`.

During a run, each `trace.live.jsonl` row uses `trace-step.schema.json` and may
include `screenshot_key`. `status.json` uses `run-status.schema.json`: its
`metrics` object may contain any subset of the final metrics while work is in
progress. `runs/<case_id>/latest.json` uses `latest.schema.json`.

Engine-written PRD, factors, ontology, DoD queries, objectives, local PRD,
TDD, entities, trace, metrics, status, and approval-pending metadata include
`generated_by: {backend, model, at}`. Each entity property includes it as well,
including a missing property. `at` is an ISO-8601 timestamp. Final metrics
require `inference_backend` (`recorded` or `vultr`), which must match
`generated_by.backend`. Trace usage may additionally record the model, backend,
input and output token counts, and estimated cost. The `mock-` run-ID rule and
regeneration of recorded artifacts require workflow checks outside JSON Schema.
