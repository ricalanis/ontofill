"""Containment plumbing: run the existing hostile-page and destructive-loop fixtures
against the production sandbox substrate and append their proof to a real run's feed.

The command is `ontofill-containment --case <case_dir> --run-id <id>` (see README.md).
It reuses the engine's own pieces unchanged:

* `ontofill.sandbox.capture.capture_url` — the hostile page through a real gVisor pod
  and the allowlist egress proxy;
* `ontofill.repair.run_code_repair` with `DockerRepairExecutor` — the destructive-loop
  fixture through the repair runner on runsc;
* `ontofill.sandbox.jobs.build_job_record` / `append_job_record` — the six-checkpoint
  job records;
* `ontofill.runfeed.RunFeed` — append-only trace steps for the run view.

It authors no new hostile content: it serves the engine's existing
`sandbox/fixtures/hostile.html` and runs `sandbox/fixtures/destructive_loop.py`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
