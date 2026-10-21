"""Factor catalog for scoring v2.

Each `Factor` says where a raw value comes from (a field of the scoring-seam row), who a
person is compared against, which way the factor pushes flight risk, and how to explain
it in a sentence. Weights live in `weights.json`, not here, so they can be reviewed and
versioned on their own.

Comparison groups (`peer`):
  * "role"     — same level + division; widens to same level, then everyone, when a group
                 has fewer than MIN_GROUP people with a value.
  * "org"      — everyone in the batch.
  * "absolute" — fixed anchors (lo, hi); used for shape-like factors (the tenure cliff) and
                 for count factors where "zero" must stay neutral.

Consent (`consent`):
  * "signals"    — only for people with consented_signals = 1 (engagement / survey data).
  * "monitoring" — only for people with monitoring_consent = 1 (collaboration metadata).
  Without consent the factor is treated as missing, never as a bad value.

Engagement explanations are deliberately generic: a reason code must never reveal how a
person answered a survey.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

MIN_GROUP = 8


def _get(name: str) -> Callable[[dict], float | None]:
    def getter(row: dict) -> float | None:
        v = row.get(name)
        return None if v is None else float(v)
    return getter


def _tenure_cliff(row: dict) -> float | None:
    """0..1 pressure peaking at ~24 months (the 18-30 month window when many people leave)."""
    t = row.get("tenure_months")
    if t is None:
        return None
    return math.exp(-((float(t) - 24.0) ** 2) / (2 * 7.0 ** 2))


def _engagement(row: dict) -> float | None:
    v = row.get("engagement_score")
    if v is None:
        v = row.get("engagement_pulse")
    return None if v is None else float(v)


def _tenure_years(row: dict) -> float | None:
    t = row.get("tenure_months")
    return None if t is None else float(t) / 12.0


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# detail(raw, raises, group) -> sentence. `raises` is True when the factor pushes risk up.
Detail = Callable[[float, bool, str], str]

_ENGAGEMENT_DETAIL: Detail = lambda raw, up, g: (
    "Engagement signals are weaker or falling (from data this person agreed to share)"
    if up else "Engagement signals are strong (from data this person agreed to share)")


@dataclass(frozen=True)
class Factor:
    key: str
    get: Callable[[dict], float | None]
    peer: str = "role"                         # role | org | absolute
    anchors: tuple[float, float] = (0.0, 1.0)  # absolute scale / small-batch fallback
    risk_dir: int = 1                          # +1: a higher value means more flight risk
    one_sided: bool = False                    # only ever raises risk (zero stays neutral)
    consent: str | None = None                 # None | "signals" | "monitoring"
    labels: tuple[str, str] = ("", "")         # (raises-risk label, lowers-risk label)
    detail: Detail = field(default=lambda raw, up, g: "")


F: dict[str, Factor] = {f.key: f for f in [
    # ---- pay & reward -----------------------------------------------------------------
    Factor("comp_gap", _get("comp_gap"), anchors=(0.0, 0.30),
           labels=("comp_below_band", "competitive_pay"),
           detail=lambda r, up, g: (f"Paid {_pct(r)} below their pay-band midpoint" if up
                                    else "Paid close to their pay-band midpoint")),
    Factor("comp_gap_vs_market", _get("comp_gap_vs_market"), anchors=(0.0, 0.30),
           labels=("pay_below_market", "pay_at_market"),
           detail=lambda r, up, g: (f"Paid {_pct(r)} below the market rate for the role" if up
                                    else "Pay is in line with the market rate")),
    Factor("pay_percentile_in_role", _get("pay_percentile_in_role"), risk_dir=-1,
           labels=("low_pay_in_role", "well_paid_in_role"),
           detail=lambda r, up, g: f"Paid at the {_ordinal(round(r * 100))} percentile of their role"),
    Factor("months_since_raise", _get("months_since_raise"), anchors=(0, 24),
           labels=("no_recent_raise", "recent_raise"),
           detail=lambda r, up, g: (f"No pay raise for {r:.0f} months" if up
                                    else f"Had a raise {r:.0f} months ago")),
    Factor("last_raise_pct", _get("last_raise_pct"), risk_dir=-1, anchors=(0, 15),
           labels=("small_last_raise", "strong_last_raise"),
           detail=lambda r, up, g: (f"Last raise ({r:.1f}%) was smaller than most {g}" if up
                                    else f"Last raise ({r:.1f}%) was larger than most {g}")),
    # ---- career & job search ------------------------------------------------------------
    Factor("months_since_promotion", _get("months_since_promotion"), anchors=(0, 36),
           labels=("overdue_promotion", "recently_promoted"),
           detail=lambda r, up, g: (f"{r:.0f} months since last promotion, longer than most {g}"
                                    if up else f"Promoted {r:.0f} months ago, sooner than most {g}")),
    Factor("promotion_velocity", _get("promotion_velocity"), risk_dir=-1, anchors=(0, 2),
           labels=("slow_career_progress", "fast_career_progress"),
           detail=lambda r, up, g: (f"Promoted more slowly than most {g}" if up
                                    else f"Promoted faster than most {g}")),
    Factor("years_in_current_role", _get("years_in_current_role"), anchors=(0, 6),
           labels=("long_time_in_role", "new_in_role"),
           detail=lambda r, up, g: f"{r:.1f} years in the same role"),
    Factor("internal_applications_12mo", _get("internal_applications_12mo"), peer="absolute",
           anchors=(0, 2), one_sided=True,
           labels=("applied_internally", ""),
           detail=lambda r, up, g: f"Applied for {r:.0f} internal role(s) in the last 12 months"),
    # ---- engagement & sentiment (consent-gated; generic wording) -------------------------
    *[Factor(k, get, peer="org", anchors=anchors, risk_dir=-1, consent="signals",
             labels=("low_engagement_signals", "strong_engagement"), detail=_ENGAGEMENT_DETAIL)
      for k, get, anchors in [
          ("engagement_trend", _get("engagement_trend"), (-5, 5)),
          ("engagement_score", _engagement, (0, 100)),
          ("enps_score", _get("enps_score"), (-100, 100)),
          ("job_satisfaction", _get("job_satisfaction"), (1, 5)),
          ("work_life_balance", _get("work_life_balance"), (1, 5)),
          ("survey_sentiment", _get("survey_sentiment"), (-1, 1)),
          ("recognition_count", _get("recognition_count"), (0, 10)),
          ("manager_effectiveness_score", _get("manager_effectiveness_score"), (1, 5)),
      ]],
    # ---- manager & team ------------------------------------------------------------------
    Factor("manager_changes_12mo", _get("manager_changes_12mo"), peer="org", anchors=(0, 3),
           labels=("manager_instability", "stable_manager"),
           detail=lambda r, up, g: (f"{r:.0f} manager change(s) in the last 12 months" if up
                                    else "Stable reporting line")),
    Factor("team_turnover_rate", _get("team_turnover_rate"), peer="org", anchors=(0, 0.3),
           labels=("high_team_turnover", "stable_team"),
           detail=lambda r, up, g: (f"Team turnover is {_pct(r)}, higher than most teams" if up
                                    else f"Team turnover is low ({_pct(r)})")),
    Factor("peers_departed_recently", _get("peers_departed_recently"), peer="absolute",
           anchors=(0, 3), one_sided=True,
           labels=("teammates_leaving", ""),
           detail=lambda r, up, g: f"{r:.0f} teammate(s) left in the last 3 months"),
    Factor("manager_rating", _get("manager_rating"), peer="org", anchors=(1, 5), risk_dir=-1,
           labels=("weak_manager_rating", "strong_manager_rating"),
           detail=lambda r, up, g: (f"Their manager is rated {r:.1f}/5, below most managers" if up
                                    else f"Their manager is rated {r:.1f}/5, above most managers")),
    # ---- market pull ---------------------------------------------------------------------
    Factor("skill_market_demand", _get("skill_market_demand"), peer="org",
           labels=("skills_in_demand", "low_outside_demand"),
           detail=lambda r, up, g: ("Their skills are in high demand outside the firm" if up
                                    else "Lower outside demand for their skills")),
    Factor("industry_hiring_heat", _get("industry_hiring_heat"), peer="org",
           labels=("hot_hiring_market", "cool_hiring_market"),
           detail=lambda r, up, g: ("Competitors are hiring heavily for profiles like theirs" if up
                                    else "Outside hiring for their profile is quiet")),
    Factor("skill_uniqueness", _get("skill_uniqueness"), peer="org",
           labels=("rare_skill_mix", "common_skill_mix"),
           detail=lambda r, up, g: ("Rare skill mix that other employers want" if up
                                    else "Common skill mix")),
    Factor("regional_comp_movement", _get("regional_comp_movement"), peer="org", anchors=(0, 0.1),
           labels=("regional_pay_rising", "regional_pay_flat"),
           detail=lambda r, up, g: (f"Pay in their region is rising ({_pct(r)} a year)" if up
                                    else "Regional pay is steady")),
    Factor("performance_standing", _get("perf_rating"), anchors=(1, 5),
           labels=("strong_performer_outside_offers", ""),
           detail=lambda r, up, g: (f"Rated {r:.1f}/5, above most {g}: likely to get outside offers"
                                    if up else f"Rated {r:.1f}/5")),
    # ---- ties & timing ---------------------------------------------------------------------
    Factor("tenure_cliff", _tenure_cliff, peer="absolute", one_sided=True,
           labels=("tenure_cliff", ""),
           detail=lambda r, up, g: "In the 18-30 month window when many people leave"),
    Factor("unvested_equity_usd", _get("unvested_equity_usd"), risk_dir=-1, anchors=(0, 250_000),
           labels=("little_unvested_equity", "unvested_equity_retains"),
           detail=lambda r, up, g: (f"Unvested equity (${r:,.0f}) is low for their level" if up
                                    else f"Would give up ${r:,.0f} of unvested equity by leaving")),
    Factor("median_prior_tenure", _get("median_prior_tenure"), peer="org", anchors=(0, 6),
           risk_dir=-1, labels=("short_prior_stints", "long_prior_stints"),
           detail=lambda r, up, g: f"Stayed a median {r:.1f} years at previous employers"),
    Factor("job_hopping_rate", _get("job_hopping_rate"), peer="org",
           labels=("frequent_job_changes", "steady_career_history"),
           detail=lambda r, up, g: ("Changed jobs often earlier in career" if up
                                    else "Steady career history")),
    # ---- workload & wellbeing --------------------------------------------------------------
    Factor("pto_used", _get("pto_used"), peer="org", anchors=(5, 25), risk_dir=-1,
           labels=("little_time_off", "takes_time_off"),
           detail=lambda r, up, g: (f"Took only {r:.0f} days off" if up
                                    else f"Took {r:.0f} days off")),
    Factor("overtime", _get("overtime"), peer="org",
           labels=("heavy_overtime", "normal_hours"),
           detail=lambda r, up, g: ("Works more overtime than most" if up else "Normal working hours")),
    Factor("commute_km", _get("commute_km"), peer="org", anchors=(0, 60),
           labels=("long_commute", "short_commute"),
           detail=lambda r, up, g: f"Commute of about {r:.0f} km"),
    Factor("after_hours_activity", _get("after_hours_activity"), peer="org", consent="monitoring",
           labels=("heavy_after_hours_load", "normal_after_hours_load"),
           detail=lambda r, up, g: ("Heavy after-hours workload (from data this person agreed to share)"
                                    if up else "Normal after-hours workload")),
    # ---- inputs used by value and the panel only ---------------------------------------------
    Factor("perf_rating", _get("perf_rating"), anchors=(1, 5)),
    Factor("performance_trend", _get("performance_trend"), peer="org", anchors=(-1, 1)),
    Factor("goal_attainment", _get("goal_attainment"), anchors=(0.5, 1.2)),
    Factor("division_output", _get("division_output"), peer="absolute"),   # precomputed 0..1
    Factor("skill_avg_prof", _get("skill_avg_prof"), peer="org", anchors=(0, 5)),
    Factor("skill_count", _get("skill_count"), anchors=(0, 8)),
    Factor("skill_family_breadth", _get("skill_family_breadth"), peer="org", anchors=(0, 4)),
    Factor("certifications_count", _get("certifications_count"), peer="org", anchors=(0, 4)),
    Factor("level", _get("level"), peer="absolute", anchors=(1, 6)),
    Factor("span_of_control", _get("span_of_control"), peer="absolute", anchors=(0, 10)),
    Factor("bridge_score", _get("bridge_score"), peer="org", consent="monitoring"),
    Factor("betweenness", _get("betweenness"), peer="org", consent="monitoring"),
    Factor("tenure_years", _tenure_years, peer="absolute", anchors=(0, 10)),
    Factor("learning_hours_12mo", _get("learning_hours_12mo"), anchors=(0, 60)),
    Factor("training_times_last_year", _get("training_times_last_year"), peer="org", anchors=(0, 6)),
    Factor("skills_acquired_12mo", _get("skills_acquired_12mo"), peer="absolute", anchors=(0, 3)),
    Factor("internal_moves", _get("internal_moves"), peer="org", anchors=(0, 3)),
    Factor("performance_consistency", lambda r: (None if r.get("review_rating_stdev") is None
                                                 else -float(r["review_rating_stdev"])),
           peer="org", anchors=(-1, 0)),
    Factor("engagement_level", _engagement, peer="org", anchors=(0, 100), consent="signals"),
    Factor("engagement_direction", _get("engagement_trend"), peer="org", anchors=(-5, 5),
           consent="signals"),
    Factor("enps", _get("enps_score"), peer="org", anchors=(-100, 100), consent="signals"),
]}


# Panel metrics (0-100, higher = more of the trait). Each entry:
#   factor key, weight, good direction (+1: a higher value is better), label if strong,
#   label if weak.
PANEL: dict[str, list[tuple[str, float, int, str, str]]] = {
    "skills_depth": [
        ("skill_avg_prof", 0.35, 1, "deep_expertise", "shallow_expertise"),
        ("skill_count", 0.25, 1, "broad_skill_set", "narrow_skill_set"),
        ("skill_family_breadth", 0.25, 1, "cross_domain_adjacency", "single_domain"),
        ("certifications_count", 0.15, 1, "certified", "few_certifications")],
    "learning_velocity": [
        ("learning_hours_12mo", 0.5, 1, "high_learning_investment", "low_learning_activity"),
        ("training_times_last_year", 0.3, 1, "regular_training", "little_training"),
        ("skills_acquired_12mo", 0.2, 1, "new_skills_this_year", "no_new_skills")],
    "performance_impact": [
        ("perf_rating", 0.40, 1, "strong_performance", "below_expectations"),
        ("goal_attainment", 0.25, 1, "hits_goals", "misses_goals"),
        ("division_output", 0.25, 1, "strong_output", "low_output"),
        ("performance_trend", 0.10, 1, "improving_performance", "declining_performance")],
    "mobility_readiness": [
        ("skill_family_breadth", 0.30, 1, "cross_domain_adjacency", "narrow_skill_set"),
        ("perf_rating", 0.30, 1, "proven_performer", "performance_gap"),
        ("internal_moves", 0.20, 1, "history_of_mobility", "static_role_history"),
        ("years_in_current_role", 0.20, 1, "ready_for_next_step", "new_in_role")],
    "trust_reliability": [
        ("tenure_years", 0.30, 1, "established_tenure", "short_tenure"),
        ("manager_changes_12mo", 0.20, -1, "stable_reporting_line", "frequent_manager_changes"),
        ("performance_consistency", 0.30, 1, "consistent_performance", "inconsistent_performance"),
        ("goal_attainment", 0.20, 1, "delivers_on_goals", "misses_goals")],
    "leadership_influence": [
        ("level", 0.30, 1, "senior_scope", "junior_scope"),
        ("span_of_control", 0.30, 1, "broad_span_of_control", "individual_contributor"),
        ("bridge_score", 0.20, 1, "connects_teams", "narrow_network"),
        ("perf_rating", 0.20, 1, "strong_performance", "performance_gap")],
    "engagement_productivity": [
        ("engagement_level", 0.45, 1, "high_engagement", "disengagement_signal"),
        ("engagement_direction", 0.20, 1, "engagement_rising", "engagement_falling"),
        ("enps", 0.15, 1, "would_recommend_employer", "would_not_recommend"),
        ("perf_rating", 0.20, 1, "productive_output", "output_dip")],
}

VALUE_LABELS: dict[str, tuple[str, str]] = {
    "perf_rating": ("strong_performance", "below_expectations"),
    "performance_trend": ("improving_performance", "declining_performance"),
    "goal_attainment": ("hits_goals", "misses_goals"),
    "division_output": ("strong_output", "low_output"),
    "skill_uniqueness": ("rare_skills", "common_skills"),
    "skill_market_demand": ("in_demand_skills", "low_demand_skills"),
    "skill_avg_prof": ("deep_expertise", "shallow_expertise"),
    "level": ("senior_scope", "junior_scope"),
    "span_of_control": ("leads_a_team", "individual_contributor"),
    "bridge_score": ("connects_teams", "narrow_network"),
    "betweenness": ("central_in_network", "peripheral_in_network"),
    "tenure_years": ("institutional_knowledge", "short_tenure"),
}

# Every input the v2 scorer may read. The scoring-wall test checks no protected or
# identity-proxy field is among them.
INPUT_FIELDS: frozenset[str] = frozenset({
    "comp_gap", "comp_gap_vs_market", "pay_percentile_in_role", "months_since_raise",
    "last_raise_pct", "months_since_promotion", "promotion_velocity", "years_in_current_role",
    "internal_applications_12mo", "engagement_trend", "engagement_score", "engagement_pulse",
    "enps_score", "job_satisfaction", "work_life_balance", "survey_sentiment",
    "recognition_count", "manager_effectiveness_score", "manager_changes_12mo",
    "team_turnover_rate", "peers_departed_recently", "manager_rating", "skill_market_demand",
    "industry_hiring_heat", "skill_uniqueness", "regional_comp_movement", "perf_rating",
    "tenure_months", "unvested_equity_usd", "median_prior_tenure", "job_hopping_rate",
    "pto_used", "overtime", "commute_km", "after_hours_activity", "performance_trend",
    "goal_attainment", "division_signals", "skill_avg_prof", "skill_count",
    "skill_family_breadth", "certifications_count", "level", "span_of_control", "bridge_score",
    "betweenness", "learning_hours_12mo", "training_times_last_year", "skills_acquired_12mo",
    "internal_moves", "review_rating_stdev", "months_since_manager_change", "months_since_vest",
    "months_since_internal_application", "base_salary", "salary_currency", "division",
    "consented_signals",
    "monitoring_consent", "reference",
})
