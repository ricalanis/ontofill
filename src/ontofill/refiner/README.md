# Refiner

`Observation` is the only input to silver. Each observation contains an observed
supplier field, its bronze and screenshot evidence, the execution step ID, and
`generated_by` (`backend`, `model`, `at`).
Its deterministic `value_id` is available immediately for `trace.value_ids`.

```python
from ontofill.refiner import PostgresSilverStore, export_run, refine_observations

store = PostgresSilverStore()  # reads SILVER_DATABASE_URL
store.add(observation)
result = refine_observations(
    store.list_for_run(run_id), generated_by=run_provenance, shapes_ttl=ontology_shape_path
)
metrics = export_run(
    lake,
    case_dir,
    case_id,
    run_id,
    result.suppliers,
    trace=trace_steps,
    generated_by=run_provenance,
)
```

`MemorySilverStore` has the same API for synthetic tests. Refinement rejects
invalid provenance or SHACL values and marks absent core fields `missing`;
conflicting observed values stay `conflict`. Gold export validates all four
contract documents, verifies each value's trace and both bronze objects, then
writes `gold/<case_id>/<run_id>/` and finally `latest.json`. Only metrics are
copied into `case/runs/`.

Recorded observations require a `mock-` run ID. They never update either
`gold/<case_id>/latest.json` or `case/runs/latest/metrics.json`; their metrics
carry `inference_backend: recorded`. Export rejects mixed inference backends
across suppliers, values, trace steps, contracts, and phase lineage artifacts.

`ontofill.runfeed.RunFeed` writes `trace.live.jsonl` one step at a time, refreshes
`status.json` on state or phase changes and by a background heartbeat every
10 seconds, and points `runs/<case_id>/latest.json` at live runs only. Call
`close()` when the run ends, or use it as a context manager.
