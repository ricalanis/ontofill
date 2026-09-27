"""Draft a reviewed PRD with grounded completion criteria and human steering."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path

from jsonschema import ValidationError

from ontofill.case.checkpoints import (
    checkpoint_revisions,
    load_json,
    load_verified_approval,
    write_json,
    write_markdown,
    write_prd_budget_pending,
)
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import (
    DecisionClient,
    ModelValidationExhausted,
    complete_validated,
    generated_by,
)
from ontofill.phase_loop import CheckResult, LoopBudget, PhaseLoop

NUMBER_TOKEN = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*%?")
PERCENT_METRIC = re.compile(
    r"^(?:%|percent(?:age)?|porcentaje|ratio|proportion|share)\b|(?:_|\s)(?:pct|percent|ratio|share)$",
    re.IGNORECASE,
)
SECONDARY_REQUEST = re.compile(r"secondary|secundari|cross[ -]?check|contraste", re.IGNORECASE)
PRIMARY_REQUEST = re.compile(r"\bprimary\b|\bprimari[oa]s?\b|\bprincipal(?:es)?\b", re.IGNORECASE)
_DOTTED_ABBREVIATION = re.compile(r"\b(?:[A-Za-zÀ-ÿ]{1,2}\.\s*){2,}")
SECONDARY_STOPWORDS = {
    "are",
    "as",
    "check",
    "cross",
    "crosschecks",
    "for",
    "human",
    "keep",
    "kept",
    "only",
    "de",
    "del",
    "is",
    "las",
    "los",
    "of",
    "requested",
    "remain",
    "secondary",
    "secundaria",
    "secundario",
    "source",
    "sources",
    "supplementary",
    "son",
    "the",
    "them",
    "these",
    "with",
}
JURISDICTION_SCOPE_WORDS = {
    "and",
    "de",
    "del",
    "federal",
    "government",
    "local",
    "national",
    "of",
    "public",
    "regional",
    "state",
    "the",
    "y",
}
REJECTED_THRESHOLD = re.compile(
    r"\b(?:unsupported|unfounded|invented|unjustified|incorrect|wrong|rejected?|"
    r"discard(?:ed)?|drop|remove|do not use|not supported|not grounded|"
    r"sin sustento|no sustentad[oa]|inventad[oa]|rechazad[oa])\b",
    re.IGNORECASE,
)
GROUNDING_NOTE = re.compile(
    r"\s*[,;—–(\[]?\s*(?:basis\s*[:=]|basis_quote\s*[:=]|quote\s*:)", re.IGNORECASE
)


class PrdDraftUnavailable(Exception):
    """The phase loop stopped before it could produce an approvable PRD."""

    def __init__(
        self,
        message: str,
        *,
        purpose: str | None = None,
        attempts: int | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.purpose = purpose
        self.attempts = attempts
        self.reason = reason


def _contains_number(text: str, expected: float, *, percent_metric: bool = False) -> bool:
    for match in NUMBER_TOKEN.finditer(text):
        token = match.group().strip().replace(",", ".")
        percent = token.endswith("%")
        if percent and not percent_metric and expected >= 1:
            continue
        raw = float(token.rstrip("% "))
        values = (
            (raw, raw / 100) if percent and percent_metric else (raw / 100 if percent else raw,)
        )
        if any(abs(expected - value) < 0.00001 for value in values):
            return True
    return False


def _number_clauses(text: str) -> tuple[str, str]:
    """Keep supported and rejected numeric clauses separate for human revisions."""
    clauses = re.split(r";\s*|(?<=[.!?])\s+|,\s+|\n+", text)
    supported, rejected = [], []
    for clause in clauses:
        if REJECTED_THRESHOLD.search(clause):
            rejected.append(clause)
        else:
            supported.append(clause)
    return "\n".join(supported), "\n".join(rejected)


def _tokens(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]+", plain))


def _revision_clauses(reason: str) -> list[str]:
    """Split prose without treating dotted abbreviations as sentence boundaries."""
    protected = _DOTTED_ABBREVIATION.sub(lambda match: match.group().replace(".", "\ue000"), reason)
    return [
        part.replace("\ue000", ".").strip()
        for part in re.split(r";\s*|(?<=[.!?])\s+|\n+", protected)
        if part.strip()
    ]


def _secondary_clauses(revisions: list[dict]) -> list[str]:
    clauses: list[str] = []
    for revision in revisions:
        previous = ""
        for fragment in _revision_clauses(revision["reason"]):
            if SECONDARY_REQUEST.search(fragment):
                # A later denial can ask to preserve already reviewed secondary
                # publishers. Its other objects (DoD, tiers, etc.) are not new
                # source subjects; earlier revisions still enforce the sources.
                if re.search(
                    r"\b(?:keep|preserve|maintain|mantener|conservar)\b", fragment, re.IGNORECASE
                ) and re.search(
                    r"\b(?:as they are|unchanged|tal como est[aá]n|sin cambios)\b",
                    fragment,
                    re.IGNORECASE,
                ):
                    previous = fragment
                    continue
                clause = fragment
                if not _secondary_subjects(clause) and previous:
                    clause = f"{previous} {clause}"
                if _secondary_subjects(clause):
                    clauses.append(clause)
            previous = fragment
    return clauses


def _subject_tokens(text: str) -> set[str]:
    return {
        token[:-1] if token.endswith("s") and len(token) > 4 else token
        for token in _tokens(text)
        if len(token) >= 3 and token not in SECONDARY_STOPWORDS
    }


def _secondary_subjects(clause: str) -> list[tuple[str, set[str]]]:
    marker = SECONDARY_REQUEST.search(clause)
    assert marker is not None
    named_part = clause[: marker.start()] or clause[marker.end() :]
    parts = re.split(r"\s*(?:,|\band\b|\by\b)\s*", named_part, flags=re.IGNORECASE)
    return [(part, subject) for part in parts if (subject := _subject_tokens(part))]


def _jurisdiction_aliases(jurisdiction: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", jurisdiction.casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    words = [
        token for token in re.findall(r"[a-z0-9]+", plain) if token not in JURISDICTION_SCOPE_WORDS
    ]
    aliases = set(words)
    if len(words) > 1:
        aliases.update("".join(word[0] for word in words[:end]) for end in range(2, len(words) + 1))
    return aliases


def _kind_token_matches(subject_token: str, kind_token: str) -> bool:
    """Allow close cross-language cognates only in an explicitly named publisher kind."""
    return subject_token == kind_token or (
        min(len(subject_token), len(kind_token)) >= 4
        and SequenceMatcher(None, subject_token, kind_token).ratio() >= 0.84
    )


def _named_secondary_matches(
    subject_text: str, subject: set[str], publishers: list[dict]
) -> list[dict]:
    jurisdiction_aliases = {
        id(item): _jurisdiction_aliases(item.get("jurisdiction", "")) for item in publishers
    }
    publisher_tokens = {
        id(item): _tokens(
            f"{item['kind']} {item['rationale']} {' '.join(item['domains'])} "
            f"{item.get('jurisdiction', '')}"
        )
        for item in publishers
    }
    all_jurisdiction_aliases = (
        set().union(*jurisdiction_aliases.values()) if jurisdiction_aliases else set()
    )
    referenced_jurisdictions = _tokens(subject_text) & all_jurisdiction_aliases
    named_codes = {token.casefold() for token in re.findall(r"\b[A-Z]{2,}\b", subject_text)}
    dotted_codes = {
        token.casefold()
        for abbreviation in _DOTTED_ABBREVIATION.finditer(subject_text)
        for token in re.findall(r"[A-Za-zÀ-ÿ]+", abbreviation.group())
    }
    known_publisher_tokens = set().union(*publisher_tokens.values()) if publisher_tokens else set()
    unexplained_codes = (
        named_codes - dotted_codes - all_jurisdiction_aliases - known_publisher_tokens
    )
    if unexplained_codes:
        return []
    jurisdiction_scoped = bool(referenced_jurisdictions)
    if jurisdiction_scoped:
        publishers = [
            item for item in publishers if referenced_jurisdictions & jurisdiction_aliases[id(item)]
        ]
    direct_domains = [
        item
        for item in publishers
        if any(domain.casefold() in subject_text.casefold() for domain in item["domains"])
    ]
    if direct_domains:
        longest = max(
            len(domain)
            for item in direct_domains
            for domain in item["domains"]
            if domain.casefold() in subject_text.casefold()
        )
        exact = [
            item
            for item in direct_domains
            if any(
                len(domain) == longest and domain.casefold() in subject_text.casefold()
                for domain in item["domains"]
            )
        ]
        return exact if len(exact) == 1 else []
    normalized_subject = " ".join(subject_text.casefold().split())
    direct_kinds = [
        item
        for item in publishers
        if re.search(
            rf"(?<!\w){re.escape(' '.join(item['kind'].casefold().split()))}(?!\w)",
            normalized_subject,
        )
    ]
    if direct_kinds:
        longest = max(len(item["kind"]) for item in direct_kinds)
        exact = [item for item in direct_kinds if len(item["kind"]) == longest]
        return exact if len(exact) == 1 else []
    owners_by_token: list[list[dict]] = []
    for token in subject:
        owners = [
            item
            for item in publishers
            if any(
                _kind_token_matches(token, kind_token)
                for kind_token in _subject_tokens(item["kind"])
            )
        ]
        if not owners:
            return []
        owners_by_token.append(owners)
    if jurisdiction_scoped:
        return [item for item in publishers if any(item in owners for owners in owners_by_token)]
    distinctive = [owners[0] for owners in owners_by_token if len(owners) == 1]
    if not distinctive or any(
        not any(item in owners for item in distinctive) for owners in owners_by_token
    ):
        return []
    return [item for item in publishers if item in distinctive]


def _claims_primary(rationale: str) -> bool:
    return bool(
        re.search(
            r"^\s*(?:(?:a|an|the)\s+)?primary\b|"
            r"\b(?:is|serves as|acts as|classified as|designated as|(?<!not )as)\s+"
            r"(?:(?:a|an|the)\s+)?primary\b",
            rationale,
            re.IGNORECASE,
        )
    )


def _ground_criteria(
    document: dict, brief: str, revisions: list[dict], budget_usd: float | None
) -> None:
    human_text = "\n".join(item["reason"] for item in revisions)
    human_supported, human_rejected = _number_clauses(human_text)
    budget = f"${budget_usd:.2f}" if budget_usd is not None else "an unspecified USD budget"
    for item in document["definition_of_done"]:
        percent_metric = bool(PERCENT_METRIC.search(item["metric"].strip()))
        if item["basis"] == "proposed":
            for basis, source in (("human", human_supported), ("brief", brief)):
                clauses = re.split(r";\s*|(?<=[.!?])\s+|\n+", source)
                matching = [
                    clause.strip()
                    for clause in clauses
                    if _contains_number(clause, item["target"], percent_metric=percent_metric)
                    or (
                        "min_ratio" in item
                        and _contains_number(clause, item["min_ratio"], percent_metric=True)
                    )
                ]
                if not matching:
                    continue
                if not _contains_number(source, item["target"], percent_metric=percent_metric):
                    continue
                if "min_ratio" in item and not _contains_number(
                    source, item["min_ratio"], percent_metric=True
                ):
                    continue
                if basis == "human" and (
                    _contains_number(human_rejected, item["target"], percent_metric=percent_metric)
                    or (
                        "min_ratio" in item
                        and _contains_number(human_rejected, item["min_ratio"], percent_metric=True)
                    )
                ):
                    continue
                item["basis"] = basis
                item["basis_quote"] = matching[0]
                break
        if item["basis"] == "proposed":
            item["feasibility"] = (
                f"{item['feasibility'].rstrip('.')} (run budget: {budget}; elapsed run time unverified)."
            )
            continue
        quote_source = brief if item["basis"] == "brief" else human_text
        number_source = brief if item["basis"] == "brief" else human_supported + "\n" + brief
        quote = " ".join(item.get("basis_quote", "").split())
        grounded = bool(quote) and quote.casefold() in " ".join(quote_source.split()).casefold()
        quote_supported, _ = _number_clauses(quote)
        grounded &= _contains_number(
            quote_supported, item["target"], percent_metric=percent_metric
        ) or (
            "min_ratio" in item
            and _contains_number(quote_supported, item["min_ratio"], percent_metric=True)
        )
        grounded &= _contains_number(
            number_source,
            item["target"],
            percent_metric=percent_metric,
        )
        if "min_ratio" in item:
            grounded &= _contains_number(number_source, item["min_ratio"], percent_metric=True)
        if item["basis"] == "human":
            grounded &= not _contains_number(
                human_rejected, item["target"], percent_metric=percent_metric
            )
            if "min_ratio" in item:
                grounded &= not _contains_number(
                    human_rejected, item["min_ratio"], percent_metric=True
                )
        if not grounded:
            item["basis"] = "proposed"
            item.pop("basis_quote", None)
            item["rationale"] = (
                "The stated numeric threshold lacks a matching brief or human quote."
            )
            item["feasibility"] = (
                f"At {budget}, feasibility and elapsed run time need validation after source discovery."
            )


def _apply_human_authority_revisions(document: dict, revisions: list[dict]) -> None:
    publishers = document["authority_policy"]["trusted_publishers"]
    jurisdiction = document["authority_policy"]["jurisdiction"]
    case_aliases = _jurisdiction_aliases(jurisdiction)
    case_tokens = _tokens(jurisdiction) - JURISDICTION_SCOPE_WORDS
    jurisdiction_stems = {
        token[:4] for token in _tokens(jurisdiction) - JURISDICTION_SCOPE_WORDS if len(token) >= 4
    }
    for revision in revisions:
        for clause in _revision_clauses(revision["reason"]):
            marker = PRIMARY_REQUEST.search(clause)
            if marker is None or SECONDARY_REQUEST.search(clause):
                continue
            named = clause[: marker.start()] or clause[marker.end() :]
            subjects = _subject_tokens(named)
            matches = _named_secondary_matches(named, subjects, publishers)
            if not matches and jurisdiction_stems & {token[:4] for token in _tokens(named)}:
                matches = [
                    item
                    for item in publishers
                    if jurisdiction_stems
                    & {token[:4] for token in _tokens(item.get("jurisdiction", ""))}
                ]
            for item in matches:
                item["tier"] = "primary"
    for clause in _secondary_clauses(revisions):
        for subject_text, subject in _secondary_subjects(clause):
            named_codes = {code.casefold() for code in re.findall(r"\b[A-Z]{2,}\b", subject_text)}
            foreign_scope = bool(named_codes) and not bool(named_codes & case_aliases)
            candidates = [item for item in publishers if item.get("tier") != "secondary"]
            for item in _named_secondary_matches(subject_text, subject, candidates):
                item_tokens = _tokens(item.get("jurisdiction", "")) - JURISDICTION_SCOPE_WORDS
                if foreign_scope and item.get("tier") == "primary" and item_tokens & case_tokens:
                    continue
                item["tier"] = "secondary"


def _authority_policy_check(document: dict, revisions: list[dict]) -> CheckResult:
    policy = document["authority_policy"]
    case_tokens = _tokens(policy["jurisdiction"]) - JURISDICTION_SCOPE_WORDS
    objections = []
    seen_kinds: set[str] = set()
    seen_domains: set[str] = set()
    local_primary = False
    publishers = policy["trusted_publishers"]
    for publisher in publishers:
        kind = " ".join(publisher["kind"].casefold().split())
        if kind in seen_kinds:
            objections.append(f"Duplicate trusted publisher: {publisher['kind']}")
        seen_kinds.add(kind)
        domains = publisher["domains"]
        if "tier" not in publisher:
            objections.append(f"Trusted publisher {publisher['kind']} needs an explicit tier")
        if not domains:
            objections.append(f"Trusted publisher {publisher['kind']} has no domain")
        for domain in domains:
            normalized = domain.casefold().rstrip(".")
            labels = normalized.split(".")
            if len(labels) == 2 and len(labels[0]) <= 4 and len(labels[1]) == 2:
                objections.append(
                    f"Trusted domain {domain} is a broad namespace; name a specific publisher domain"
                )
            if normalized in seen_domains:
                objections.append(f"Duplicate trusted domain: {domain}")
            seen_domains.add(normalized)
        if publisher.get("tier") == "primary":
            publisher_tokens = _tokens(publisher.get("jurisdiction", "")) - JURISDICTION_SCOPE_WORDS
            if case_tokens and publisher_tokens & case_tokens:
                local_primary = True
        elif "tier" in publisher and _claims_primary(publisher["rationale"]):
            objections.append(
                f"Publisher {publisher['kind']} is {publisher['tier']} but rationale claims primary"
            )
    if not local_primary:
        objections.append("Authority policy needs a primary publisher in the case jurisdiction")
    secondary = [item for item in publishers if item.get("tier") == "secondary" and item["domains"]]
    for clause in _secondary_clauses(revisions):
        for subject_text, subject in _secondary_subjects(clause):
            if not _named_secondary_matches(subject_text, subject, secondary):
                named_subject = " ".join(subject_text.split())[:160]
                objections.append(
                    f"Human SECONDARY subject '{named_subject}' needs a SECONDARY-tier publisher "
                    "whose kind names that subject and whose domains include a specific domain"
                )
    return CheckResult(not objections, tuple(objections))


def _objection_subject_changed(before: dict, after: dict, objection: str) -> bool:
    """Require a diff at the field and publisher named by a prior objection."""
    subject = objection.casefold()
    before_policy = before["authority_policy"]
    after_policy = after["authority_policy"]
    if any(term in subject for term in ("authority", "publisher", "tier", "domain")):
        named = [
            item
            for item in before_policy["trusted_publishers"]
            if item["kind"].casefold() in subject
            or any(domain.casefold() in subject for domain in item["domains"])
            or any(
                token in subject
                for token in _tokens(item["rationale"])
                if len(token) >= 5
                and token not in SECONDARY_STOPWORDS
                and token
                not in {
                    "primary",
                    "official",
                    "trusted",
                    "authority",
                    "publisher",
                    "rationale",
                    "domain",
                }
            )
        ]
        if named:
            for old in named:
                current = next(
                    (
                        item
                        for item in after_policy["trusted_publishers"]
                        if item["kind"].casefold() == old["kind"].casefold()
                    ),
                    None,
                )
                if current is None:
                    continue
                if "tier" in subject and "primary" in subject and old.get("tier") == "secondary":
                    if current.get("tier") == "primary":
                        continue
                    if (
                        current.get("tier") in {"secondary", "review"}
                        and current["rationale"] != old["rationale"]
                        and not _claims_primary(current["rationale"])
                    ):
                        continue
                    return False
                fields = (
                    ("tier",)
                    if "tier" in subject
                    else ("domains", "jurisdiction", "rationale", "tier")
                )
                if all(old.get(field) == current.get(field) for field in fields):
                    return False
            return True
        return False
    if any(term in subject for term in ("target", "criterion", "definition_of_done", "dod")):
        old_items = before["definition_of_done"]
        new_items = after["definition_of_done"]
        named = [
            item
            for item in old_items
            if item["id"].casefold() in subject or item["metric"].casefold() in subject
        ]
        if not named:
            named = [item for item in old_items if str(item["target"]) in subject]
        if named:
            by_id = {item["id"]: item for item in new_items}
            return all(by_id.get(item["id"]) != item for item in named)
        return old_items != new_items
    for section in ("requirements", "constraints", "non_goals", "personas", "jobs_to_be_done"):
        if section in subject:
            return before[section] != after[section]
    return False


def _remove_grounding_notes(document: dict) -> None:
    for section in ("personas", "jobs_to_be_done", "requirements"):
        for item in document[section]:
            description = item["description"]
            match = GROUNDING_NOTE.search(description)
            if match:
                clean = description[: match.start()].strip(" .,;—–([")
                item["description"] = clean or "Description requires human review"


def _narrow_domain_patch_target(document: dict, reason: str) -> tuple[int, str] | None:
    """Find the one existing publisher domain explicitly named by a domain-only denial."""
    if not re.search(
        r"\b(?:one|single|only)\s+(?:defect|issue|change|fix)\b|\bonly\s+(?:fix|change|replace)\b",
        reason,
        re.IGNORECASE,
    ):
        return None
    if not re.search(r"\b(?:domain|dominio|host)\b", reason, re.IGNORECASE):
        return None
    named = [
        (index, domain)
        for index, publisher in enumerate(document["authority_policy"]["trusted_publishers"])
        for domain in publisher["domains"]
        if re.search(rf"(?<![\w.-]){re.escape(domain)}(?![\w.-])", reason, re.IGNORECASE)
    ]
    return named[0] if len(named) == 1 else None


def draft_prd(
    case_dir: Path,
    decision: DecisionClient,
    *,
    budget_usd: float | None = None,
    run_id: str = "draft-prd",
    emit: Callable[[dict], None] | None = None,
    mock_preview: bool = False,
) -> dict:
    output = case_dir / "01-scope/prd.json"
    brief_path = case_dir / "brief.md"
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError("case brief is empty")
    if mock_preview and decision.backend != "recorded":
        raise ValueError("mock PRD previews require a recorded decision client")
    artifact_names = ["prd.json", "prd.md", "prd.input.sha256"]
    revisions = checkpoint_revisions(
        output.parent, "prd", artifact_names, archive_denial=False, case_dir=case_dir
    )
    digest_parts = [brief, revisions, "prd-steering-v4"]
    if mock_preview:
        digest_parts.append("mock-preview")
    digest = hashlib.sha256(json.dumps(digest_parts, ensure_ascii=False).encode()).hexdigest()
    # Migrate a cached draft made by the previous fingerprint formula while the
    # runner still supplies its original budget. This changes only the cache key;
    # the reviewed PRD bytes and any digest-bound approval stay untouched.
    legacy_digest = None
    if not mock_preview:
        legacy_digest = hashlib.sha256(
            json.dumps(
                [brief, revisions, budget_usd, "prd-steering-v4"], ensure_ascii=False
            ).encode()
        ).hexdigest()
    fingerprint_path = output.with_suffix(".input.sha256")
    prior_for_patch: dict | None = None
    domain_patch_target: tuple[int, str] | None = None
    if output.exists():
        document = load_json(output)
        cached_digest = (
            fingerprint_path.read_text(encoding="utf-8").strip()
            if fingerprint_path.exists()
            else None
        )
        cache_matches = cached_digest == digest or (
            legacy_digest is not None and cached_digest == legacy_digest
        )
        approval_path = output.parent / "APPROVED"
        approved = False
        if not mock_preview and decision.backend == "vultr" and approval_path.exists():
            approval = load_verified_approval(approval_path, case_dir, ["01-scope/prd.json"], "prd")
            approved = approval.get("decision", "approve") != "deny"
            if not approved and document.get("generated_by", {}).get("backend") == "vultr":
                try:
                    validate_document("global-prd", document)
                except ValidationError:
                    pass  # a reviewed legacy draft may still need the full bounded redraft
                else:
                    if _authority_policy_check(document, revisions[:-1]).passed:
                        domain_patch_target = _narrow_domain_patch_target(
                            document, revisions[-1]["reason"]
                        )
                        if domain_patch_target is not None:
                            prior_for_patch = document
        if document.get("generated_by", {}).get("backend") == decision.backend and (
            cache_matches or approved
        ):
            try:
                validate_document("global-prd", document)
            except ValidationError as exc:
                if approved:
                    raise PrdDraftUnavailable(
                        f"approved PRD fails current schema validation: {exc.message}"
                    ) from exc
            else:
                authority = _authority_policy_check(document, revisions)
                if authority.passed:
                    if cached_digest != digest:
                        fingerprint_path.write_text(digest + "\n", encoding="utf-8")
                    return document
                if approved:
                    raise PrdDraftUnavailable(
                        "approved PRD fails authority policy: " + "; ".join(authority.objections)
                    )
    prompt = (
        "Draft the global PRD from this brief. Include personas, jobs, "
        "requirements traced to jobs, constraints, non-goals, and measurable completion criteria. "
        "For every definition-of-done criterion include basis=brief, human, or proposed. "
        "For brief/human basis, basis_quote must be one exact source excerpt. The numeric target "
        "and any per-entity min_ratio may appear in separate clauses of the brief or human reason. "
        "If a threshold is absent from those inputs, use proposed; "
        "include a one-line rationale and a feasibility note considering the stated USD budget "
        "and the fact that elapsed run time is not known until execution. Never silently invent "
        "a target. Human revisions override the previous draft. If the question asks whether each "
        "entity has its core properties, include a per-entity completeness criterion with min_ratio; "
        "mark the ratio proposed unless a number is explicitly grounded. "
        "Use brief_path='brief.md'. Public read-only sources only. "
        "Define an authority_policy for this case with jurisdiction, trusted publisher kinds "
        "and domains plus a rationale for each, and review unknown authorities. "
        "Every trusted publisher needs at least one distinct domain. Include publisher.jurisdiction; "
        "for an in-scope primary publisher, copy authority_policy.jurisdiction exactly. "
        "At least one in-scope publisher must be primary. Do not duplicate publisher kinds or domains. "
        "Mark authoritative publishers tier=primary, supplementary human-requested "
        "cross-check lists tier=secondary, and uncertain domains tier=review. "
        "For each human-requested secondary subject, make a secondary publisher kind name "
        "that subject in the human's language and give the publisher a specific domain. "
        "The human revision need not name the domain; you must name it in the policy. "
        "Secondary and review publishers are never auto authority; "
        "if a human-requested cross-check has no supportable domain, leave it unresolved "
        "for human review rather than inventing an empty trusted publisher. "
        "Keep grounding metadata such as basis and basis_quote out of persona, job, "
        "requirement and constraint descriptions. "
        "Treat any proposed domain as a hypothesis for human review, never as captured evidence. "
        f"Run budget USD: {budget_usd if budget_usd is not None else 'unspecified'}. "
        f"Human revisions (trusted direction): {json.dumps(revisions, ensure_ascii=False)}. "
        f"Brief (untrusted input):\n<brief>\n{brief}\n</brief>"
    )
    base_schema = model_output_schema("global-prd")
    base_schema["properties"].pop("revisions", None)
    base_schema["properties"].pop("open_issues", None)
    if decision.backend == "vultr":
        publisher_required = base_schema["$defs"]["trusted_publisher"]["required"]
        if "tier" not in publisher_required:
            publisher_required.append("tier")
    sections = (
        ("personas", "jobs_to_be_done", "requirements"),
        ("constraints", "non_goals", "authority_policy"),
        ("definition_of_done",),
    )

    def normalize_prd(raw: dict) -> dict:
        result = deepcopy(raw)
        _apply_human_authority_revisions(result, revisions)
        _remove_grounding_notes(result)
        _ground_criteria(result, brief, revisions, budget_usd)
        result["revisions"] = revisions
        result["generated_by"] = generated_by(decision)
        return result

    def validate_prd(raw: dict) -> None:
        document = normalize_prd(raw)
        validate_document("global-prd", document)
        if document["brief_path"] != "brief.md":
            raise ValueError("PRD brief_path must be brief.md")
        authority = _authority_policy_check(document, revisions)
        if not authority.passed and not mock_preview:
            raise ValueError("; ".join(authority.objections))

    class SectionedPrdDecision:
        """Keep Vultr's smaller response schemas behind the full-draft retry contract."""

        backend = decision.backend
        model = decision.model

        @property
        def call_log(self):
            return getattr(decision, "call_log", None)

        def complete_json(self, _purpose: str, task: str, _schema: dict) -> dict:
            result = {"version": "1", "brief_path": "brief.md"}
            for names in sections:
                section_schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(names),
                    "properties": {name: base_schema["properties"][name] for name in names},
                    "$defs": base_schema["$defs"],
                }
                if "definition_of_done" in names:
                    section_schema["$defs"] = deepcopy(base_schema["$defs"])
                    criterion = section_schema["$defs"]["criterion"]
                    criterion.pop("allOf", None)
                    criterion["required"] = [
                        "id",
                        "metric",
                        "operator",
                        "target",
                        "basis",
                        "rationale",
                        "feasibility",
                    ]
                section_prompt = (
                    f"{task}\nProduce only these PRD sections: {', '.join(names)}. "
                    f"Sections already drafted: {json.dumps(result, ensure_ascii=False)}"
                )
                result.update(
                    decision.complete_json("phase1.prd.section", section_prompt, section_schema)
                )
            return result

    model_decision = SectionedPrdDecision() if decision.backend == "vultr" else decision

    def complete(task: str) -> dict:
        try:
            raw = complete_validated(
                model_decision,
                "phase1.prd",
                task,
                base_schema,
                validate_prd,
                max_attempts=3,
            )
        except ModelValidationExhausted as exc:
            raise PrdDraftUnavailable(
                str(exc), purpose=exc.purpose, attempts=exc.attempts, reason=exc.reason
            ) from exc
        return normalize_prd(raw)

    def domain_patch(extra_objections: tuple[str, ...] = ()) -> dict:
        assert prior_for_patch is not None and domain_patch_target is not None
        index, old_domain = domain_patch_target
        publisher = prior_for_patch["authority_policy"]["trusted_publishers"][index]
        patch_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["domains"],
            "properties": {
                "domains": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": deepcopy(
                        base_schema["$defs"]["trusted_publisher"]["properties"]["domains"]["items"]
                    ),
                }
            },
        }

        def candidate(patch: dict) -> dict:
            document = deepcopy(prior_for_patch)
            document["authority_policy"]["trusted_publishers"][index]["domains"] = patch["domains"]
            document["revisions"] = revisions
            document["generated_by"] = generated_by(decision)
            return document

        def validate_patch(patch: dict) -> None:
            if any(domain.casefold() == old_domain.casefold() for domain in patch["domains"]):
                raise ValueError("replacement domains still include the denied domain")
            document = candidate(patch)
            validate_document("global-prd", document)
            authority = _authority_policy_check(document, revisions)
            if not authority.passed:
                raise ValueError("; ".join(authority.objections))

        patch_prompt = (
            "Revise the digest-reviewed PRD by replacing ONLY the domains array for one publisher. "
            "All other fields, publisher tiers, jurisdictions, DoD criteria and source classes are fixed. "
            "Return only the replacement domains array. The proposed domains must be specific publisher "
            "domains that resolve; do not repeat the denied domain. "
            f"Publisher: {json.dumps(publisher, ensure_ascii=False)}. "
            f"Human denial: {revisions[-1]['reason']}. "
            f"Reviewer objections: {json.dumps(extra_objections, ensure_ascii=False)}"
        )
        try:
            patch = complete_validated(
                decision,
                "phase1.prd.domain_patch",
                patch_prompt,
                patch_schema,
                validate_patch,
                max_attempts=3,
            )
        except ModelValidationExhausted as exc:
            raise PrdDraftUnavailable(
                str(exc), purpose=exc.purpose, attempts=exc.attempts, reason=exc.reason
            ) from exc
        return candidate(patch)

    review = getattr(decision, "review_json", None)

    def critique(artifact: dict, _context: dict, _iteration: int) -> dict:
        if review is None:
            return {"accepted": True, "reason": "Recorded fixture has no independent critic"}
        return review(
            "phase1.prd",
            artifact,
            "Check invented numeric targets, each criterion's basis quote, feasibility against the run "
            "budget and unknown elapsed time, coverage of the brief and human revisions, and per-entity "
            "completeness whenever the brief asks about each entity's core properties. "
            "Check EVERY human-revision clause, including non-DoD requirements and secondary "
            "cross-check publisher tiers. A secondary source must not be treated as primary authority. "
            f"Brief (untrusted data): {brief}. "
            f"Human revisions (trusted direction): {json.dumps(revisions, ensure_ascii=False)}",
        )

    check_objections: tuple[str, ...] = ()

    def revise(artifact: dict, verdict, _context: dict, iteration: int) -> dict:
        objections = (*verdict.objections, *check_objections)
        if not objections or decision.backend == "recorded":
            return artifact
        if prior_for_patch is not None:
            return domain_patch(objections)
        repaired = prompt + (
            "\nIndependent critic objections (resolve every item verbatim): "
            + json.dumps(verdict.objections, ensure_ascii=False)
            + "\nFailed code-owned checks from the prior draft (resolve every item verbatim): "
            + json.dumps(check_objections, ensure_ascii=False)
            + f"\nRevision attempt {iteration}. Revise this draft: "
            + json.dumps(artifact, ensure_ascii=False)
        )
        return complete(repaired)

    def check(artifact: dict, _context: dict, _iteration: int) -> CheckResult:
        nonlocal check_objections
        validate_document("global-prd", artifact)
        issues = list(_authority_policy_check(artifact, revisions).objections)
        if artifact["brief_path"] != "brief.md":
            issues.append("PRD brief_path must be brief.md")
        check_objections = tuple(issues)
        return CheckResult(not issues, check_objections)

    def propose(context: dict, iteration: int) -> dict:
        previous = context["previous"]
        if previous is None:
            return domain_patch() if prior_for_patch is not None else complete(prompt)
        return previous

    loop = PhaseLoop[dict](
        phase=1,
        run_id=run_id,
        generated_by=generated_by(decision),
        budget=LoopBudget(max_iterations=2, max_usd=budget_usd, wall_seconds=300),
        emit=emit,
        call_log=getattr(decision, "call_log", None),
    )
    try:
        result = loop.run(
            gather=lambda _iteration, previous: {"previous": previous},
            propose=propose,
            critique=critique,
            revise=revise,
            check=check,
            objection_addressed=_objection_subject_changed,
        )
    except ModelValidationExhausted as exc:
        raise PrdDraftUnavailable(
            str(exc), purpose=exc.purpose, attempts=exc.attempts, reason=exc.reason
        ) from exc
    if result.artifact is None:
        if not (output.parent / "APPROVED").exists():
            write_prd_budget_pending(output.parent, generated_by(decision))
        raise PrdDraftUnavailable("PRD loop budget exhausted before a draft was produced")
    document = result.artifact
    if not mock_preview:
        try:
            validate_document("global-prd", document)
            final_errors = list(_authority_policy_check(document, revisions).objections)
            if document["brief_path"] != "brief.md":
                final_errors.append("PRD brief_path must be brief.md")
        except ValidationError as exc:
            final_errors = [exc.message]
        if final_errors:
            document = complete(
                prompt
                + "\nThe final PRD failed code-owned checks: "
                + json.dumps(final_errors, ensure_ascii=False)
                + "\nCorrect the exact fields and return a complete revised PRD: "
                + json.dumps(document, ensure_ascii=False)
            )
            validate_document("global-prd", document)
            if (
                document["brief_path"] != "brief.md"
                or not _authority_policy_check(document, revisions).passed
            ):
                raise PrdDraftUnavailable("revised PRD still fails code-owned checks")
    document.pop("open_issues", None)
    if result.stop_reason != "checks_passed":
        document["open_issues"] = list(result.objections) or [
            f"PRD review stopped on {result.stop_reason} before all checks passed."
        ]
    validate_document("global-prd", document)
    if document["brief_path"] != "brief.md":
        raise ValueError("PRD brief_path must be brief.md")
    summary = ["# Global PRD", "", f"Brief: `{document['brief_path']}`", "", "## Personas", ""]
    summary.extend(f"- **{item['id']}** {item['description']}" for item in document["personas"])
    summary.extend(["", "## Jobs to be done", ""])
    summary.extend(
        f"- **{item['id']}** {item['description']}" for item in document["jobs_to_be_done"]
    )
    summary.extend(["", "## Requirements", ""])
    summary.extend(f"- **{item['id']}** {item['description']}" for item in document["requirements"])
    summary.extend(["", "## Definition of done", ""])
    summary.extend(
        f"- {item['metric']} {item['operator']} {item['target']} [basis: {item['basis']}]"
        + (f" (per-entity ratio: {item['min_ratio']})" if "min_ratio" in item else "")
        + (f" — {item['rationale']}; {item['feasibility']}" if item["basis"] == "proposed" else "")
        for item in document["definition_of_done"]
    )
    summary.extend(["", "## Authority policy", ""])
    summary.extend(
        f"- {item['kind']} [{item.get('tier', 'primary')}]: {item['rationale']}"
        for item in document["authority_policy"]["trusted_publishers"]
    )
    if revisions:
        summary.extend(["", "## Human revisions", ""])
        summary.extend(f"- {item['n']}. {item['reason']}" for item in revisions)
    if document.get("open_issues"):
        summary.extend(["", "## Open issues for human review", ""])
        summary.extend(f"- {item}" for item in document["open_issues"])
    output.parent.mkdir(parents=True, exist_ok=True)
    marker = output.parent / "APPROVED"
    denied = marker.exists() and load_json(marker).get("decision") == "deny"
    with tempfile.TemporaryDirectory(prefix=".prd-candidate-", dir=output.parent) as temporary:
        staging = Path(temporary)
        write_json(staging / "prd.json", document)
        write_markdown(staging / "prd.md", "\n".join(summary) + "\n", document["generated_by"])
        (staging / "prd.input.sha256").write_text(digest + "\n", encoding="utf-8")
        backup = staging / "previous"
        backup.mkdir()
        for name in artifact_names:
            old = output.parent / name
            if old.exists():
                shutil.copy2(old, backup / name)
        archive = output.parent / "revisions" / str(len(revisions))
        stale_marker = marker.with_name(f"APPROVED.stale.{digest[:12]}")
        try:
            if denied:
                checkpoint_revisions(output.parent, "prd", artifact_names, case_dir=case_dir)
            elif marker.exists():
                os.replace(marker, stale_marker)
            for name in artifact_names:
                os.replace(staging / name, output.parent / name)
        except Exception:
            for name in artifact_names:
                old = backup / name
                target = output.parent / name
                if old.exists():
                    os.replace(old, target)
                else:
                    target.unlink(missing_ok=True)
            if denied:
                for name in ["APPROVAL_PENDING.md", "APPROVED"]:
                    source = archive / name
                    if source.exists():
                        os.replace(source, output.parent / name)
                for name in artifact_names:
                    (archive / name).unlink(missing_ok=True)
                if archive.exists():
                    archive.rmdir()
            elif stale_marker.exists() and not marker.exists():
                os.replace(stale_marker, marker)
            raise
    return document
