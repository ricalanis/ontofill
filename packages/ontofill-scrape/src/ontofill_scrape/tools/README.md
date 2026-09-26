# tools

One folder per tool group.

- `site/`: page.snapshot, page.screenshot, page.query, page.forms/links/pagination, net.observed, site.meta, file.fetch/parse, page.diff
- `graph/`: graph.frontier, graph.expand, graph.cluster, graph.map, ontology.gaps, source.discover, ontology.recommend
- `extract/`: extract.selector, extract.table, extract.llm, entity.lookup, emit.observation (the only output channel)
- `code/`: code.write, code.test, code.promote, code.repair
- `resilience/`: strategy ladder, retries with backoff, checkpointing, yield monitoring, hard stops, inference diagnosis

Hackathon minimum: page.snapshot, page.query, page.forms/links/pagination, file.fetch/parse, ontology.gaps, source.discover, extract.selector/llm, entity.lookup, emit.observation, code.write/test/promote.
