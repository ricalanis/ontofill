"""Action guard (skyvern-jev.md §6b role 2): a deterministic code floor, then a Jev risk tier that can only raise it.

final tier = max(code floor, Jev). Jev can never lower a code-forced HIGH, and a Jev failure falls back to the code
floor alone (the service runs without Jev). A gateway injection flag on the current page adds scrutiny: the floor
for any state-changing action becomes at least LOW.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from controller.backend import CALL_ERRORS, Action, Observation, host_allowed

TIERS = ("SAFE", "LOW", "HIGH")
# Read-only archetype: data files a source publishes may be fetched; anything executable or unknown may not.
DOWNLOAD_ALLOWLIST = frozenset({".pdf", ".csv", ".json", ".xml", ".xls", ".xlsx", ".ods", ".txt", ".html", ".htm",
                                ".zip"})
DOWNLOAD_HINT = frozenset({".exe", ".msi", ".dmg", ".pkg", ".apk", ".bat", ".cmd", ".sh", ".ps1", ".jar", ".deb",
                           ".rpm", ".scr", ".bin", ".iso", ".docm", ".xlsm", ".7z", ".rar", ".gz", ".tar"})
# Words on a control that commit something. A button with one of these is HIGH even outside a <form>.
COMMIT_WORDS = re.compile(
    r"\b(send|submit|enviar|mandar|pay|pagar|buy|comprar|order|delete|borrar|eliminar|confirm|confirmar|register|"
    r"registrar|sign ?up|sign ?in|log ?in|iniciar sesi[oó]n|apply|solicitar|complaint|queja|denuncia|report|"
    r"subscribe|suscribir|upload|subir|transfer|accept|aceptar|checkout|post|publish|publicar)\b", re.IGNORECASE)
SEARCH_WORDS = re.compile(r"\b(search|buscar|b[uú]squeda|filter|filtrar|find|go|ir|consultar|lookup)\b", re.IGNORECASE)

# Reasons the approver cannot override: the pod's egress allowlist would block them anyway.
HARD_REASONS = ("navigation off the allowed domains", "link off the allowed domains", "navigation to a non-web URL")

ACTION_GUARD_Q = {"tier": {
    "type": "choice",
    "instructions": "A read-only research agent proposes `action` on the page `page`. Classify the risk that "
                    "executing it changes something outside the agent: submits data, sends a message, creates an "
                    "account, buys, deletes, or downloads an executable. Reading, searching, filtering, paging "
                    "and following links is not risky.",
    "criteria": {
        "SAFE": "Pure navigation or reading: follow a link, page through results, type into a search box.",
        "LOW": "Changes only the agent's own view: fills a field that is not submitted, toggles a filter.",
        "HIGH": "Submits a form that is not a search, sends or publishes content, logs in, pays, deletes, or "
                "downloads a program.",
    }}}


@dataclass
class GuardDecision:
    tier: str
    decided_by: str  # code | jev
    code_tier: str
    code_reason: str
    jev: dict | None = None  # {"tier", "confidence", "model"} or {"error"}
    hard: bool = False  # outside the TDD's allowlist: denied outright, an approver cannot override it

    def as_dict(self) -> dict:
        return {"tier": self.tier, "decided_by": self.decided_by, "code_tier": self.code_tier,
                "code_reason": self.code_reason, "jev": self.jev, "hard": self.hard}


def _max(a: str, b: str) -> str:
    return a if TIERS.index(a) >= TIERS.index(b) else b


def _extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower()


def code_floor(action: Action, obs: Observation, allowed_domains: list[str], scrutiny: bool = False) -> tuple[str, str]:
    """Deterministic tier and the reason for it."""
    tier, reason = "SAFE", "navigation or reading"
    tool = action.tool
    el = obs.element(action.element_id)
    if tool == "navigate":
        url = urljoin(obs.url or "", str(action.args.get("url", "")))
        if urlsplit(url).scheme not in ("http", "https"):
            return "HIGH", f"navigation to a non-web URL ({urlsplit(url).scheme or 'none'})"
        if not host_allowed(urlsplit(url).hostname, allowed_domains):
            return "HIGH", f"navigation off the allowed domains ({urlsplit(url).hostname})"
        if _extension(url) in DOWNLOAD_HINT:
            return "HIGH", f"download of a non-allowlisted file type ({_extension(url)})"
    elif tool in ("click", "type", "select"):
        if el is None:
            return "LOW", "unknown element"
        form = el.form or {}
        in_search_form = bool(form) and bool(form.get("search"))
        if tool == "click":
            if el.kind == "link" and el.href:
                url = urljoin(obs.url or "", el.href)
                if not host_allowed(urlsplit(url).hostname, allowed_domains):
                    return "HIGH", f"link off the allowed domains ({urlsplit(url).hostname})"
                ext = _extension(url)
                if ext and ext not in DOWNLOAD_ALLOWLIST and (ext in DOWNLOAD_HINT or el.name.lower().startswith("download")):
                    return "HIGH", f"download of a non-allowlisted file type ({ext})"
            elif el.kind in ("submit", "button"):
                if form and not in_search_form:
                    return "HIGH", "submit control of a form that is not a search/filter form"
                if not form and COMMIT_WORDS.search(el.name) and not SEARCH_WORDS.search(el.name):
                    return "HIGH", f"button that commits something (\"{el.name[:40]}\")"
                if in_search_form:
                    tier, reason = "SAFE", "submit of a search/filter form"
                else:
                    tier, reason = "LOW", "button outside a form"
        elif tool == "type":
            if el.input_type == "password":
                return "HIGH", "typing into a password field"
            if in_search_form or el.input_type == "search":
                tier, reason = "SAFE", "typing into a search box"
            else:
                tier, reason = "LOW", "typing into a field of a non-search form"
        elif tool == "select":
            tier, reason = ("SAFE", "filter in a search form") if in_search_form else ("LOW", "select outside a search form")
    if scrutiny and tier == "SAFE":
        tier, reason = "LOW", reason + "; the page was flagged for injection (added scrutiny)"
    return tier, reason


def decide(action: Action, obs: Observation, allowed_domains: list[str], jev_call=None, *, scrutiny: bool = False,
           step_id: str | None = None) -> GuardDecision:
    """Code floor, then Jev. `jev_call(state, questions, step_id)` returns Jev's body or raises."""
    code_tier, code_reason = code_floor(action, obs, allowed_domains, scrutiny)
    decision = GuardDecision(code_tier, "code", code_tier, code_reason,
                             hard=code_reason.startswith(HARD_REASONS))
    if jev_call is None or code_tier == "HIGH":  # nothing Jev says can change a code-forced HIGH
        return decision
    el = obs.element(action.element_id)
    state = {"action": action.describe(obs), "page": {"url": obs.url, "title": obs.title},
             "element": el.brief() if el else None}
    try:
        body = jev_call(state, ACTION_GUARD_Q, step_id)
        ans = body["answers"]["tier"]
        jev_tier = ans["choice"] if ans.get("choice") in TIERS else "HIGH"
        decision.jev = {"tier": jev_tier, "confidence": ans.get("confidence"), "model": body.get("model")}
    except CALL_ERRORS as exc:  # Jev unavailable or malformed: the code floor stands alone
        decision.jev = {"error": str(exc)[:200]}
        return decision
    final = _max(code_tier, jev_tier)
    if final != code_tier:
        decision.tier, decision.decided_by = final, "jev"
    return decision
