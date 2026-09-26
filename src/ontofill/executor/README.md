# executor

Reactive, event-driven execution across the spectrum:
- `events`: event bus (new lead from silver, site change, metric drop)
- `modes`: D0 deterministic, D1 inference-assisted RPA, S1 agentic loop, S2 full computer use
- `controller`: picks mode per step within the TDD's allowed range; escalates on failure, stops on captcha/login
- `crystallize`: turns successful S1/S2 traces into D1 macros (rung-5 options in the site graph)
- `steplog`: observed / requested / executed / evaluated per step
