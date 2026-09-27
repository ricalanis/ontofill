# Phase 3: Fan out

Source discovery + authority check, spider manager building type/instance site graphs, mapping page types to the ontology, ranked (source, objective) pairs.

`discovery_loop.py` runs discovery as a bounded `PhaseLoop` (gather gaps -> propose leads and
capture the best in the sandbox -> critic -> revise -> coverage check). `leads.py` holds the
lead-only providers: Vultr model-proposed publishers, Wikidata official websites (P856), CKAN
`package_search` on catalog domains the approved policy lists, and Tavily (`TAVILY_API_KEY`,
basic depth, capped by `ONTOFILL_TAVILY_MAX_CREDITS`, cached by request fingerprint). A lead is
never evidence: only a captured, critic-accepted page becomes an objective, and a publisher
outside the approved primary tier waits for source review.
