"""Documented integration seams — contracts only, NO live calls.

These are the production data sources the pipeline is designed to accept, written as
explicit `NotImplementedError` stubs so the contract (inputs, consent/license gate,
canonical target) is captured in code without any network call, credential, or scrape.
Each, when implemented, would hand its records to the SAME normalize -> resolve ->
validate -> write path the file adapters already use.
"""
from __future__ import annotations

from typing import Any

Record = dict[str, Any]


def merge_hris(connection_token: str) -> list[Record]:  # pragma: no cover - stub seam
    """Unified HRIS API (e.g. Merge.dev) -> normalized worker records for `employee_core`.

    CONTRACT: pull workers from the linked HRIS, emit one record per worker using the
    Workday-style field names the `workday` adapter expects. Authentication is by a
    short-lived linked-account token; no credentials are stored here.
    """
    raise NotImplementedError(
        "merge_hris is a documented seam — wire a unified HRIS connector, then feed its "
        "records through the workday adapter.")


def graph_metadata(tenant_id: str, *, consented_tokens: set[str]) -> list[Record]:  # pragma: no cover
    """M365 Graph / Google Workspace collaboration METADATA -> `collaboration_metadata`.

    CONTRACT: request interaction metadata (who-with-whom counts, meeting load, network
    centrality) — never message content (§9 RED). Consent-gated: only `consented_tokens`
    (engagement_prefs.monitoring_consent=1) may be queried; everyone else is excluded at
    the source, not merely nulled downstream.
    """
    raise NotImplementedError(
        "graph_metadata is a documented, consent-gated seam — no live Graph/Workspace "
        "call is made; metadata only, never content.")


def external_market(license_key: str, regions: set[str]) -> list[Record]:  # pragma: no cover
    """Licensed labor-market data (McLagan / Radford / Mercer) -> `external_market`.

    CONTRACT: pull benchmark/demand series for licensed `regions` only — never scraped
    profiles (§9 RED). Restricted to regions covered by the active license.
    """
    raise NotImplementedError(
        "external_market is a documented, license-gated seam — licensed feed only, never "
        "scraped.")
