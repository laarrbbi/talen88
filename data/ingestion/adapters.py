"""Stage 2 — MAP (adapter definitions). Source schemas -> canonical fields.

An `Adapter` is a static, declarative description of how one kind of export maps onto
one canonical table. No code paths branch on data; nothing is inferred. Two real
adapters ship:

  * `workday` — a Workday EIB-style worker export -> `employee_core`.
  * `engagement_survey` — Peakon / Viva Glint / Qualtrics EmployeeXM / Culture Amp /
    Perceptyx / Medallia exports -> `engagement`. These platforms PRECOMPUTE the
    engagement/eNPS scores, driver breakdowns, Gallup Q12 vectors and text themes; the
    adapter INGESTS those numbers verbatim and never recomputes them. `group_map`
    collects a family of source columns into one canonical JSON column (drivers, Q12,
    themes) without interpreting the values.

`build_survey_adapter(platform)` picks the right column dictionary for a platform; a
future first-party Talent88 survey would register another entry in `PLATFORM_MAPS` and
fill only the gaps the third-party export leaves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Adapter:
    name: str
    fmt: str                                   # csv | excel | json | xml
    target: str                                # canonical table written
    field_map: Mapping[str, str]               # source column -> canonical field
    key_fields: tuple[str, ...]                # canonical fields forming the resolution key
    required: tuple[str, ...] = ()             # canonical fields that must be present
    date_fields: tuple[str, ...] = ()
    level_fields: tuple[str, ...] = ()
    enum_fields: tuple[str, ...] = ()
    currency_field: str | None = None          # canonical field holding the currency code
    amount_fields: tuple[str, ...] = ()        # money fields folded onto the base currency
    group_map: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    constant_fields: Mapping[str, object] = field(default_factory=dict)
    sheet: str | None = None                   # Excel worksheet, if format is excel
    header_row: int = 0                         # Excel zero-based header row index


# --- Workday EIB-style worker export -> employee_core --------------------------------
WORKDAY_ADAPTER = Adapter(
    name="workday",
    fmt="csv",
    target="employee_core",
    field_map={
        "Email": "email",                       # transient: resolution key, not a column
        "Job Profile": "role",
        "Business Title": "title",
        "Management Level": "level",
        "Cost Center": "division",
        "Supervisory Organization": "team",
        "Location": "location",
        "Worker Type": "employment_type",
        "Travel": "business_travel_frequency",
        "Hire Date": "hire_date",
        "Position Status": "status",
    },
    key_fields=("email",),
    required=("role", "level", "division", "team", "location",
              "employment_type", "hire_date", "status"),
    date_fields=("hire_date",),
    level_fields=("level",),
    enum_fields=("division", "employment_type", "business_travel_frequency", "status"),
)


# Each platform maps its own column names onto canonical `engagement` fields. Scalar
# scores in `fields`; collected JSON families in `groups`. Values are ingested as-is.
PLATFORM_MAPS: dict[str, dict] = {
    "peakon": {
        "fields": {
            "Employee Email": "email",
            "Survey Date": "as_of_date",
            "Engagement": "engagement_score",
            "Engagement Trend": "engagement_trend",
            "eNPS": "enps_score",
            "Response Rate": "survey_response_rate",
            "Text Sentiment": "survey_text_sentiment",
        },
        "groups": {
            "engagement_driver_scores": {
                "Accomplishment": "Driver: Accomplishment",
                "Autonomy": "Driver: Autonomy",
                "Growth": "Driver: Growth",
                "Recognition": "Driver: Recognition",
            },
            "survey_open_text_themes": {
                "themes": "Open Text Themes",
            },
        },
    },
    "glint": {
        "fields": {
            "Email": "email",
            "Date": "as_of_date",
            "eSat": "engagement_score",
            "eNPS": "enps_score",
            "Response Rate": "survey_response_rate",
        },
        "groups": {
            "engagement_driver_scores": {
                "Belonging": "Belonging", "Growth": "Growth", "Manager": "Manager",
            },
        },
    },
    "qualtrics": {
        "fields": {
            "Email Address": "email",
            "EndDate": "as_of_date",
            "EngagementIndex": "engagement_score",
            "eNPS": "enps_score",
        },
        "groups": {
            "q12_scores": {f"Q{i:02d}": f"Q12_{i}" for i in range(1, 13)},
        },
    },
    "cultureamp": {
        "fields": {
            "Work Email": "email",
            "Survey Close Date": "as_of_date",
            "Engagement": "engagement_score",
            "eNPS": "enps_score",
        },
        "groups": {
            "engagement_driver_scores": {
                "Leadership": "Factor: Leadership",
                "Enablement": "Factor: Enablement",
            },
        },
    },
    "perceptyx": {
        "fields": {
            "Email": "email",
            "Administration Date": "as_of_date",
            "Engagement Score": "engagement_score",
            "eNPS": "enps_score",
        },
        "groups": {},
    },
    "medallia": {
        "fields": {
            "e_email": "email",
            "response_date": "as_of_date",
            "eaccount_engagement": "engagement_score",
            "enps": "enps_score",
        },
        "groups": {},
    },
}


def build_survey_adapter(platform: str) -> Adapter:
    """Build the engagement adapter for a named survey platform (ingests precomputed
    scores; never recomputes)."""
    key = platform.strip().lower()
    spec = PLATFORM_MAPS.get(key)
    if spec is None:
        raise KeyError(f"unknown survey platform {platform!r}; known: {sorted(PLATFORM_MAPS)}")
    return Adapter(
        name=f"engagement_survey:{key}",
        fmt="csv",
        target="engagement",
        field_map=dict(spec["fields"]),
        key_fields=("email",),
        required=("as_of_date",),
        date_fields=("as_of_date", "survey_date"),
        group_map=spec.get("groups", {}),
    )


# Adapters addressable by name from the CLI / pipeline. Survey adapters are
# parameterized by platform via the `engagement_survey:<platform>` syntax.
ADAPTERS: dict[str, Adapter] = {"workday": WORKDAY_ADAPTER}


def get_adapter(name: str) -> Adapter:
    """Resolve an adapter by name. `engagement_survey:<platform>` builds a survey adapter."""
    if name in ADAPTERS:
        return ADAPTERS[name]
    if name.startswith("engagement_survey:"):
        return build_survey_adapter(name.split(":", 1)[1])
    raise KeyError(
        f"unknown adapter {name!r}; known: {sorted(ADAPTERS)} + engagement_survey:<platform>")
