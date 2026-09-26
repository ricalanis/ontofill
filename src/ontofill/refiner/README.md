# Refiner

`Observation` contains an ontology class, entity ID, property ID, typed scalar
value, bronze and screenshot evidence, execution step ID, and `generated_by`.
Its deterministic `value_id` is available before export for `trace.value_ids`.

```python
from ontofill.refiner import PostgresSilverStore, export_run, refine_observations

store = PostgresSilverStore()  # reads SILVER_DATABASE_URL
store.add(observation)
result = refine_observations(
    store.list_for_run(run_id),
    ontology=ontology,
    generated_by=run_provenance,
    shapes_ttl=ontology_shape_path,
)
metrics = export_run(
    lake,
    case_dir,
    case_id,
    run_id,
    result.entities,
    ontology=ontology,
    dod_queries=dod_queries,
    trace=trace_steps,
    generated_by=run_provenance,
)
```

`MemorySilverStore` has the same API for synthetic tests. Ontology datatypes and
SHACL validate observations. Each class's declared properties appear in gold;
absent properties are `missing`, and incompatible observations remain `conflict`
without counting as complete. Silver retains all candidate observations.

Export validates case artifacts, phase lineage, typed value IDs, trace steps,
relations, and bronze objects before writing `gold/<case_id>/<run_id>/`.
The run contains `entities.jsonl`, `ontology.json`, `trace.jsonl`, and
`metrics.json`. A bounded declarative DoD query document computes metrics;
queries cannot execute model-generated code. `latest.json` is moved last.
Recorded/preview runs never satisfy DoD criteria or update the live pointer.

`ontofill.runfeed.RunFeed` publishes trace steps and status during execution.
