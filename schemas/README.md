# Ontofill contract schemas

These Draft 2020-12 JSON Schemas formalize the engine CLI, the case documents,
the lake pointer, and the gold export in `coord/CONTRACT.md` sections 2–4. Each
line of `suppliers.jsonl`, `contracts.jsonl`, and `trace.jsonl` is validated as a
separate JSON object. `metrics.json` and `gold/<case_id>/latest.json` are single
objects. `lake-pointer.schema.json` validates `lake.yaml` **after YAML parsing**;
the YAML file contains environment variable names, never credential values.
Its `case_id` is a safe path segment. Bronze can point to an S3 API or a local
directory with `kind: file`; both use `bronze/sha256/<hex>` for the object and
`bronze/sha256/<hex>.meta.json` for its sidecar. S3 credentials use the standard
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables.

| File | Document |
| --- | --- |
| `run-request.schema.json` | Parsed `ontofill run`, `refine`, or `export` arguments |
| `global-prd.schema.json` | Phase 1 PRD |
| `objectives.schema.json` | Phase 3 discovered source objectives |
| `local-prd.schema.json` | Phase 4 requirements for a source and objective |
| `tdd.schema.json` | Phase 4 technical definition document |
| `approval-pending.schema.json` | Structured metadata rendered in `APPROVAL_PENDING.md` |
| `approved.schema.json` | JSON content of the `APPROVED` marker (approver and date) |
| `supplier.schema.json` | One gold supplier row |
| `contract.schema.json` | One gold contract row |
| `trace-step.schema.json` | One trace step row |
| `metrics.schema.json` | Gold metrics snapshot |
| `latest.schema.json` | Current gold run pointer |
| `lake-pointer.schema.json` | Parsed lake configuration |
| `bronze-sidecar.schema.json` | Metadata alongside each content-addressed bronze object |

`common.schema.json` holds shared IDs, evidence, and field shapes. Gold and
conflict values require a `val:` ID and a bronze capture with a screenshot.
Missing core fields remain present as `status: "missing"`, `value: null`, and
an empty evidence array. A trace row uses null source, objective, or TDD paths
when the early phase has not produced those documents yet. Cross-document
references (value → step → TDD → objective → ontology → PRD → brief), the sum of
metric counts, and whether a step's starting mode belongs to its allowed modes
require a separate semantic check; JSON Schema cannot establish them alone.

`APPROVAL_PENDING.md` remains a human-readable Markdown file. The pending
schema validates the metadata used to render it. `APPROVED` can contain the
JSON object validated by `approved.schema.json`, including the two required
items from the contract: approver and date.
