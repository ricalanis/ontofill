from ontofill.cli.main import build_parser


def test_cli_contract_arguments() -> None:
    parser = build_parser()
    run = parser.parse_args(
        [
            "run",
            "case",
            "--from-phase",
            "2",
            "--to-phase",
            "4",
            "--run-id",
            "r1",
            "--budget-usd",
            "1.5",
            "--preview-past-checkpoints",
        ]
    )
    assert (run.command, run.from_phase, run.to_phase, run.run_id, run.budget_usd) == (
        "run",
        2,
        4,
        "r1",
        1.5,
    )
    assert parser.parse_args(["refine", "case"]).command == "refine"
    assert parser.parse_args(["export", "case"]).command == "export"
    assert run.preview_past_checkpoints is True
