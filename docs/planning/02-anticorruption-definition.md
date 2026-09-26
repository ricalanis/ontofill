# Project 2: anti-corruption application (working name "Proveedor Abierto")

**One line:** starting from a single open question and no dataset, the engine researches the problem, defines the case, discovers the ontology, and then uses computer-use agents to find and fill it from the public web. The application layer turns the result into investigation tools.

Status: definition only. This is the reference case for the engine (Project 1).

**Repo layout:** this project is **repo 2**. It holds the case package (brief, PRD, ontology, objectives, technical definition documents and macros), the application layer below, and the pointer to its data lake. It runs the engine from repo 1. The collected data lives in the external lake, not in the repo, and the app reads gold from there.

## The starting point: an open brief, not a dataset
The only input is a brief like:

> "Who receives public money in Mexico through government contracts, and are they legitimate companies?"

There is no seed CSV and no pre-chosen source list. Everything else, including the sources, is discovered by the engine. The point of the demo is **definition to execution**, and it depends heavily on computer use.

## How the engine should run this case
| Phase | What the agents do (computer use in sandboxes) | What should come out (expected, not given) |
|-------|-----------------------------------------------|---------------------------------------------|
| 1. Scope | Browse news, audit reports, NGO investigations, procurement law and complaint channels to find who fights procurement corruption and how. Draft personas, then refine them by the jobs they're really trying to get done. | Refined personas and jobs, with evidence, a global PRD, and a definition of done |
| 2. Ontology | Read how investigators and standards describe the domain (for example, open-contracting standards and audit methods), and study how existing public records are structured. | Classes such as supplier, contract, procedure, buying unit, address, legal representative, tax listing and sanction; red-flag rules as shapes; alignment to OCDS and schema.org |
| 3. Fan out | Search for where each class and property is published, and send spiders to map those sites. | Surface graph and (source, objective) pairs for sites the agents found themselves, for example procurement portals, tax-authority lists, sanction registries, official gazettes, company registries and suppliers' own websites |
| 4. Local scoping | For each source and objective, the requester and source-engineer agents probe the site and negotiate a technical definition document. An example: "for the tax-authority list, the objective is fake-invoice status for all discovered suppliers. The negotiated result: use the published file rather than the per-company search, match on RFC, and refresh monthly." | One agreed technical definition document per source and objective, with its negotiation log; rejected sources go back to Phase 3 |
| 5. Execute | Computer-use agents navigate those sites (search forms, pagination, PDFs, downloads), fill bronze, silver and gold, and follow new leads. For example, a new supplier name found in a contract becomes a new entity to complete. | A growing, linked graph of suppliers and contracts, with evidence behind every value. For example: the tax-authority list runs deterministically (a file download), a procurement portal search starts as an agentic loop and crystallizes into an RPA script, and a scanned gazette PDF needs full computer use with vision. |

**Self-expanding scope:** extraction feeds discovery. New entities and sources found in Phase 4 go back to Phase 3 as new local PRDs, within the budget and allowed-domain rules the PRD sets. That's what makes it open-ended rather than a fixed pipeline.

**Guardrails on open-endedness:** public sites only, no logins, no captcha bypass, polite crawl rates, and a budget in the PRD. Sources the agents discover are allowed only after passing an authority check (for example, government domains and recognized open-data publishers). Anything else goes to a human for approval.

## Hypotheses the engine should confirm or overturn
These are listed to judge the engine's output, **not as inputs**:
- Likely personas: investigative journalist, procurement officer, civil-society watchdog, auditor or researcher.
- Likely key signals: presence on the tax authority's fake-invoice list, sanctions, companies created shortly before an award, and shared addresses or representatives between bidders.
- If the engine finds different personas or signals with good evidence, that counts as a success, not a failure.

## Application layer (what shows the value)
The main feature is investigation, not a dashboard:
1. **Supplier dossier:** one page per company. Every field has its evidence (screenshot and link) and a confidence level.
2. **Red-flag explainer:** each flag comes with its rule, evidence and a plain-language explanation. Flags are signals, not accusations.
3. **Relationship view:** suppliers linked by shared address, representative or procedure.
4. **Case journal:** a replay of how the engine got from the brief to each finding (research, then PRD, then ontology, then source, then the technical definition negotiation, then value). This shows the engine and the application together.
5. **Watchlist and open export:** follow entities and get alerts. Export gold from the lake as OCDS JSON, CSV and RDF. The case package is forkable from this repo.

## Definition of done (draft, becomes Global PRD criteria)
- From the brief alone, the engine produces personas, a PRD and an ontology that a human approves without major rewrites.
- Agents discover and map **at least 4 distinct public source types** on their own, and none of them is hard-coded.
- For the suppliers found (target: 50 or more linked to real contracts), at least 80% have a completed core profile: identity, address, founding date, tax-list status and sanction status.
- Every gold value has at least 1 official source, and every red flag can be reproduced from its evidence.
- The case journal can trace any value back to the brief.

## Ethics
- Profiles are of **legal entities**. Data about individuals is limited to what public records publish in their role (such as a legal representative), and is never enriched from social media.
- Flags are framed as signals needing verification, with a way for a company to dispute them.

## Non-goals
- Accusing anyone or scoring "corruption probability".
- Private, login-walled or paid sources.
