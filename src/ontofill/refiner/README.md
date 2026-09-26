# Refiner

`Observation` is the only input to silver. Each observation contains an observed
supplier field, its bronze and screenshot evidence, and the execution step ID.
Its deterministic `value_id` is available immediately for `trace.value_ids`.

```python
from ontofill.refiner import PostgresSilverStore, export_run, refine_observations

store = PostgresSilverStore()  # reads SILVER_DATABASE_URL
store.add(observation)
result = refine_observations(store.list_for_run(run_id), shapes_ttl=ontology_shape_path)
metrics = export_run(lake, case_dir, case_id, run_id, result.suppliers, trace=trace_steps)
```

`MemorySilverStore` has the same API for synthetic tests. Refinement rejects
invalid provenance or SHACL values and marks absent core fields `missing`;
conflicting observed values stay `conflict`. Gold export validates all four
contract documents, verifies each value's trace and both bronze objects, then
writes `gold/<case_id>/<run_id>/` and finally `latest.json`. Only metrics are
copied into `case/runs/`.
