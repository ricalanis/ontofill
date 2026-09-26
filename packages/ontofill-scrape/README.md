# ontofill-scrape

Resilience-first extraction toolkit (not a scrape-anything crawler). Every run is scoped by a technical
definition document. Exposed as plain Python and as MCP tools. See "The resilient scraping toolkit" in
`docs/planning/01-engine-definition.md`.

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
