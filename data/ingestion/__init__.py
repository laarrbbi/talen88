"""Ingestion pipeline (raw source files -> canonical tables).

A deterministic, AI-FREE transform path. Source exports (Workday EIB, engagement
survey platforms, generic CSV/Excel/JSON/XML) are turned into rows in the canonical
analytics tables through six explicit stages, one module each:

    land      readers.py    raw file -> list[dict] records (CSV / Excel / JSON / XML)
    map       adapters.py   source field names -> canonical field names
    normalize normalize.py  dates (incl. Excel serials), currency -> base, enums, grades
    resolve   resolve.py    record -> existing employee_token; ambiguous -> review queue
    validate  validate.py   schema / ranges / referential integrity / volume — FAIL LOUDLY
    write     pipeline.py   parameterized INSERT into the canonical target table

No machine learning, inference, or fuzzy/AI matching lives anywhere in this path:
mapping is a static dictionary, resolution is exact-key only, and anything ambiguous
is sent to a human review queue — never auto-merged. Engagement adapters ingest the
platform's PRECOMPUTED scores/drivers/Q12/themes verbatim; they never recompute them.

Live integrations (unified HRIS API, M365/Workspace graph metadata, licensed market
data) are documented contracts only — see stubs.py — and raise NotImplementedError.
"""
from __future__ import annotations

from .adapters import ADAPTERS, Adapter, get_adapter
from .pipeline import IngestionReport, run_ingestion
from .resolve import ResolutionResult, ReviewItem, resolve_records
from .validate import ValidationError

__all__ = [
    "ADAPTERS",
    "Adapter",
    "get_adapter",
    "IngestionReport",
    "run_ingestion",
    "ResolutionResult",
    "ReviewItem",
    "resolve_records",
    "ValidationError",
]
