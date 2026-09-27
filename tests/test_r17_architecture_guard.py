"""Guard control-plane source from target HTTP and bronze parsing regressions."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = ROOT / "src/ontofill"
HTTP_METHODS = {"delete", "get", "patch", "post", "put", "request", "send", "stream"}
HTTP_CLIENT_FACTORIES = {"httpx.Client", "httpx.AsyncClient", "requests.Session"}

# These are deliberately call-site allowlists, not module allowlists. The named
# clients talk to fixed public APIs, the configured inference gateway, or the
# browser-agent controller. A new target-domain request in leads.py still fails
# unless it is added to one explicit API-client method.
ALLOWED_API_CALLS = {
    ("src/ontofill/phases/p3_fanout/leads.py", "WikidataClient._get"): {"get"},
    ("src/ontofill/phases/p3_fanout/leads.py", "TavilyLeadProvider._post"): {"post"},
    ("src/ontofill/inference/decision.py", "VultrDecisionClient.from_env"): {"get"},
    ("src/ontofill/inference/decision.py", "VultrDecisionClient.complete_json"): {"post"},
    ("src/ontofill/browser_agent.py", "BrowserAgentClient._post"): {"post"},
}

BRONZE_PARSER_CALLS = {
    "file_parse",
    "openpyxl.load_workbook",
    "pandas.read_csv",
    "pandas.read_excel",
    "pd.read_csv",
    "pd.read_excel",
    "pdfplumber.open",
    "fitz.open",
    "pypdf.PdfReader",
    "PyPDF2.PdfReader",
}


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _imports(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                aliases[item.asname or item.name] = f"{module}.{item.name}".strip(".")
    return aliases


def _resolve_name(name: str | None, aliases: dict[str, str]) -> str | None:
    if name is None:
        return None
    first, separator, rest = name.partition(".")
    resolved = aliases.get(first, first)
    return f"{resolved}.{rest}" if separator else resolved


def _http_client_aliases(tree: ast.Module, aliases: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None or not any(
                isinstance(item, ast.Call)
                and _resolve_name(_dotted_name(item.func), aliases) in HTTP_CLIENT_FACTORIES
                for item in ast.walk(value)
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = _dotted_name(target)
                if name:
                    names.add(name)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                factory_call = item.context_expr
                if (
                    item.optional_vars is not None
                    and isinstance(factory_call, ast.Call)
                    and _resolve_name(_dotted_name(factory_call.func), aliases)
                    in HTTP_CLIENT_FACTORIES
                ):
                    name = _dotted_name(item.optional_vars)
                    if name:
                        names.add(name)
    return names


class _CallSiteVisitor(ast.NodeVisitor):
    def __init__(self, path: str, tree: ast.Module) -> None:
        self.path = path
        self.aliases = _imports(tree)
        self.client_aliases = _http_client_aliases(tree, self.aliases)
        self.classes: list[str] = []
        self.functions: list[str] = []
        self.network_calls: list[str] = []
        self.parser_calls: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _scope(self) -> str:
        return ".".join([*self.classes, *self.functions]) or "<module>"

    def visit_Call(self, node: ast.Call) -> None:
        func_name = _resolve_name(_dotted_name(node.func), self.aliases)
        is_network_call = False
        method = None
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            receiver_name = _dotted_name(node.func.value)
            receiver = _resolve_name(receiver_name, self.aliases)
            if method in HTTP_METHODS:
                is_network_call = (
                    receiver in {"httpx", "requests"}
                    or receiver in self.client_aliases
                    or receiver in HTTP_CLIENT_FACTORIES
                    or receiver in {"self.client", "self.http", "self.session", "self.transport"}
                    or receiver in {"client", "http", "session", "transport"}
                    or (
                        isinstance(node.func.value, ast.Call)
                        and _resolve_name(_dotted_name(node.func.value.func), self.aliases)
                        in HTTP_CLIENT_FACTORIES
                    )
                )
        elif func_name and func_name.rsplit(".", 1)[-1] in HTTP_METHODS:
            is_network_call = func_name.startswith(("httpx.", "requests."))
            method = func_name.rsplit(".", 1)[-1]

        if is_network_call:
            allowed = ALLOWED_API_CALLS.get((self.path, self._scope()), set())
            if method not in allowed:
                self.network_calls.append(f"{self.path}:{node.lineno} ({self._scope()})")

        is_bronze_parser = bool(
            func_name
            and (func_name in BRONZE_PARSER_CALLS or func_name.rsplit(".", 1)[-1] == "file_parse")
        )
        # P3's BeautifulSoup call sites parse HTML returned by a sandbox capture.
        # D0's P5 table parser consumes untrusted downloaded bronze bytes and must
        # move to the networkless parse pod.
        is_p5_beautifulsoup = (
            func_name in {"bs4.BeautifulSoup", "BeautifulSoup"}
            and "/phases/p5_execute/" in f"/{self.path}"
        )
        if is_bronze_parser or is_p5_beautifulsoup:
            self.parser_calls.append(f"{self.path}:{node.lineno} ({func_name})")

        self.generic_visit(node)


def _violations(path: str, source: str) -> tuple[list[str], list[str]]:
    tree = ast.parse(source, filename=path)
    visitor = _CallSiteVisitor(path, tree)
    visitor.visit(tree)
    return visitor.network_calls, visitor.parser_calls


def _engine_sources() -> list[tuple[str, str]]:
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(ENGINE_ROOT.rglob("*.py"))
    ]


def test_engine_has_no_in_process_target_http_outside_named_clients() -> None:
    violations = [
        violation
        for path, source in _engine_sources()
        for violation in _violations(path, source)[0]
    ]
    assert violations == [], "in-process HTTP call outside named API clients: " + ", ".join(
        violations
    )


def test_engine_does_not_parse_bronze_files_outside_the_sandbox() -> None:
    violations = [
        violation
        for path, source in _engine_sources()
        for violation in _violations(path, source)[1]
    ]
    assert violations == [], "bronze parser outside sandbox: " + ", ".join(violations)


def test_guard_rejects_injected_target_domain_httpx_requests() -> None:
    injected_sources = {
        "src/ontofill/phases/p3_fanout/injected.py": """
import httpx

def fetch_target(url: str) -> dict:
    return httpx.get(url).json()
""",
        "src/ontofill/phases/p3_fanout/injected_client.py": """
import httpx

def fetch_target(url: str) -> dict:
    return httpx.Client(follow_redirects=True).get(url).json()
""",
        "src/ontofill/phases/p3_fanout/injected_assigned_client.py": """
import httpx

def fetch_target(url: str) -> dict:
    client = httpx.Client(follow_redirects=True)
    return client.get(url).json()
""",
    }

    for path, source in injected_sources.items():
        network, _ = _violations(path, source)
        assert network, f"guard missed injected request in {path}"


def test_guard_rejects_injected_bronze_parser_outside_the_sandbox() -> None:
    path = "src/ontofill/phases/p5_execute/injected.py"
    source = """
from ontofill_scrape import file_parse

def parse_downloaded_bytes(data: bytes) -> object:
    return file_parse(data, format="csv")
"""

    _, parsers = _violations(path, source)
    assert parsers
