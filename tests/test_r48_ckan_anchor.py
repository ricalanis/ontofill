"""Recorded CKAN anchor queries and entity-granularity ranking regressions."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from ontofill.phases.p3_fanout.leads import CkanLeadProvider, LeadContext, LeadQuery

ONTOLOGY = {
    "primary_class": "supplier",
    "classes": [
        {
            "id": "supplier",
            "label": "Proveedor",
            "label_plural": "Proveedores",
            "title_property": "supplier_name",
            "identifier_property": "tax_id",
        },
        {"id": "contract", "label": "Contrato", "label_plural": "Contratos"},
    ],
    "properties": [
        {
            "id": "supplier_name",
            "label": "Nombre del proveedor",
            "domain": "supplier",
            "dod": True,
        },
        {"id": "tax_id", "label": "RFC", "domain": "supplier", "dod": True},
        {
            "id": "status",
            "label": "Estatus del registro",
            "domain": "supplier",
            "dod": True,
        },
        {"id": "award_amount", "label": "Monto adjudicado", "domain": "contract"},
    ],
    "relations": [
        {
            "id": "awarded_contract",
            "label": "contrato adjudicado a",
            "domain": "supplier",
            "range": "contract",
        }
    ],
}
POLICY = {
    "trusted_publishers": [{"kind": "open data catalog", "domains": ["trusted.example.test"]}]
}
PORTALS = ("https://discovered.example.test/catalog/",)


def _context() -> LeadContext:
    return LeadContext(
        brief="Buscamos un listado público de proveedores y sus registros.",
        ontology=ONTOLOGY,
        policy=POLICY,
        queries=(
            LeadQuery("status", "estatus proveedores RFC"),
            # The site-restricted duplicate is redundant for a host-scoped CKAN call.
            LeadQuery("status", "site:trusted.example.test estatus proveedores RFC"),
        ),
        iteration=1,
        open_data_portals=PORTALS,
    )


def test_ckan_queries_anchor_each_primary_gap_on_every_portal() -> None:
    requests: list[tuple[str, str]] = []

    def fetch_json(url: str, domain: str) -> dict:
        requests.append((domain, parse_qs(urlsplit(url).query)["q"][0]))
        return {"success": True, "result": {"results": []}}

    provider = CkanLeadProvider(fetch_json=fetch_json)
    assert provider.leads(_context()) == []

    assert [domain for domain, _ in requests] == [
        "trusted.example.test",
        "discovered.example.test",
        "trusted.example.test",
        "discovered.example.test",
        "trusted.example.test",
        "discovered.example.test",
    ]
    trusted_queries = [query for domain, query in requests if domain == "trusted.example.test"]
    discovered_queries = [
        query for domain, query in requests if domain == "discovered.example.test"
    ]
    assert trusted_queries == discovered_queries
    assert "listado completo" in trusted_queries[0].casefold()
    assert "proveedores" in trusted_queries[0].casefold()
    assert "estatus del registro" in trusted_queries[0].casefold()
    assert "rfc" in trusted_queries[0].casefold()
    assert "datos abiertos" in trusted_queries[1].casefold()
    assert "csv" in trusted_queries[1].casefold()
    assert "sistema de contrato adjudicado a" in trusted_queries[2].casefold()
    assert "api" in trusted_queries[2].casefold()


def test_ckan_call_cap_reaches_each_portal_before_later_query_variants() -> None:
    requests: list[tuple[str, str]] = []

    def fetch_json(url: str, domain: str) -> dict:
        requests.append((domain, parse_qs(urlsplit(url).query)["q"][0]))
        return {"success": True, "result": {"results": []}}

    provider = CkanLeadProvider(fetch_json=fetch_json, max_calls=2)
    provider.leads(_context())

    assert [domain for domain, _ in requests] == [
        "trusted.example.test",
        "discovered.example.test",
    ]
    assert requests[0][1] == requests[1][1]
    assert "listado completo" in requests[0][1].casefold()
    assert provider.calls == 2
    assert provider.attempts[-1]["outcome"] == "cap_reached"


def test_ckan_ranks_entity_rows_from_resource_format_rows_and_columns() -> None:
    payload = {
        "success": True,
        "result": {
            "results": [
                {
                    "name": "annual-summary",
                    "title": "Resumen estadístico por año",
                    "notes": "Totales agregados para un tablero anual.",
                    "resources": [
                        {
                            "format": "JSON",
                            "row_count": 4,
                            "columns": ["Año", "Total de contratos", "Monto total"],
                        }
                    ],
                },
                {
                    "name": "supplier-contract-records",
                    "title": "Registros de proveedores y contratos adjudicados",
                    "notes": "Una fila por relación publicada.",
                    "resources": [
                        {
                            "format": "CSV",
                            "row_count": 1280,
                            "schema": {
                                "fields": [
                                    {"name": "Nombre del proveedor"},
                                    {"name": "RFC"},
                                    {"name": "Estatus del registro"},
                                    {"name": "Contrato adjudicado a"},
                                ]
                            },
                        },
                        {"format": "XLSX", "num_rows": 1280},
                    ],
                },
            ]
        },
    }

    provider = CkanLeadProvider(
        fetch_json=lambda _url, _domain: payload,
        max_calls=8,
    )
    leads = provider.leads(_context())
    by_title = {lead.title: lead for lead in leads}
    entity = by_title["Registros de proveedores y contratos adjudicados"]
    aggregate = by_title["Resumen estadístico por año"]

    assert 0 <= entity.score <= 8
    assert entity.score > aggregate.score
    assert entity.as_dict()["lead_only"] is True
    assert not {"row_count", "columns", "authority", "evidence"} & entity.as_dict().keys()
    assert "Nombre del proveedor" not in entity.snippet
