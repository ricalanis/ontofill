# ontofill-scrape

Resilience-first extraction toolkit (not a scrape-anything crawler). Every run is scoped by a technical
definition document. The hackathon API is plain Python, exported from `ontofill_scrape`.
See `TOOL_CONTRACTS` for each tool's risk tier, ladder rung and completion condition.

The package never opens a browser or makes a network request by itself. `source_discover(brief,
search_client)` needs a sandbox-backed client with `search(query) -> Sequence[SearchResult]`.
`file_fetch(url, allowed_domains, client)` needs a sandbox-backed client with `get(url) ->
FetchResponse`; it checks every redirect against the TDD allowlist. Production callers enforce
network egress in the sandbox as well. Search results are runtime results, never bundled source URLs.

`page_snapshot`, `page_query`, `page_forms`, `page_links` and `page_pagination` read captured HTML.
`extract_selector` reads CSS/XPath matches. `extract_llm(html, fields, decision)` calls the
injected `DecisionInterface.decide(prompt, response_schema)`; the engine supplies its Vultr-backed
implementation. `entity_lookup` prefers an exact normalized tax ID, then a normalized name.
`ontology_gaps` returns missing fields by entity. `emit_observation` checks ontology property,
confidence, content-addressed evidence, timestamp and a caller-provided SHACL/schema validator
before calling the silver writer. It is the only observation write path.

`file_parse` returns `ParsedFile(format, rows, text)`. CSV and XLSX rows are `ParsedRow(sheet,
row_number, values)` with original 1-based row numbers. Every nonempty XLSX row is kept, so header
rows can be found even when they are not first. PDF text is in `.text`.

`code_write`, `code_test` and `code_promote` require an explicit `SandboxWorkspace`. `code_test`
hands only captures under `replay_root` to an injected `SandboxRunner`; it never executes
generated code in the engine process. Promotion returns a replay-backed macro descriptor for the
engine to store.

| Folder | Contents |
|--------|----------|
| `src/ontofill_scrape/contract/` | Tool contract: typed I/O, action-ladder rung, risk tier, idempotency, termination predicate, raisable failures |
| `src/ontofill_scrape/failures/` | Failure taxonomy: network, rate_limited, blocked, layout_drift, stale_binding, empty_yield, validation_failed, conflict, injection_detected |
| `src/ontofill_scrape/policies/` | Per-failure resilience policies: retry, inference repair, strategy ladder, corroborate, stop |
| `src/ontofill_scrape/tools/` | Tool groups: site exploration, graph exploration, extraction, code, resilience |
| `src/ontofill_scrape/health/` | Rolling source health records |
| `src/ontofill_scrape/macros/` | Versioned macro format (macros themselves live in the case package) |
| `src/ontofill_scrape/replay/` | Regression harness replaying stored bronze captures |
| `src/ontofill_scrape/mcp/` | MCP server exposing the tools |
