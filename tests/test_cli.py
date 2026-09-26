import pytest

from ontofill.cli.main import build_parser, main


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


def test_cells_serve_reads_env_and_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    from ontofill.sandbox import cell_api

    class FakeServer:
        served = False
        closed = False

        def serve_forever(self) -> None:
            self.served = True

        def server_close(self) -> None:
            self.closed = True

    server = FakeServer()
    seen: dict[str, object] = {}

    def fake_serve(manager: object, *, token: str, port: int) -> FakeServer:
        seen.update(manager=manager, token=token, port=port)
        return server

    monkeypatch.setattr(cell_api, "serve_cells", fake_serve)
    monkeypatch.setenv("ONTOFILL_CELLS_TOKEN", "session-control-token")
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://celladmin@100.64.0.8")
    assert main(["cells", "serve", "--port", "8767"]) == 0
    assert (seen["token"], seen["port"]) == ("session-control-token", 8767)
    assert server.served and server.closed


@pytest.mark.parametrize(
    ("token", "docker_host"),
    [
        ("", "ssh://celladmin@100.64.0.8"),
        ("session-control-token", ""),
        ("session-control-token", "tcp://127.0.0.1:2375"),
        ("session-control-token", "ssh://user:password@100.64.0.8"),
        ("session-control-token", "ssh://user@100.64.0.8:22"),
    ],
)
def test_cells_serve_requires_token_and_ssh_host(
    monkeypatch: pytest.MonkeyPatch, token: str, docker_host: str
) -> None:
    monkeypatch.setenv("ONTOFILL_CELLS_TOKEN", token)
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", docker_host)
    with pytest.raises(SystemExit) as exc:
        main(["cells", "serve"])
    assert exc.value.code == 2
