"""Scoring v2 engine.

Flight risk
  1. Each factor is ranked against the person's peers (see catalog.Factor.peer) and turned
     into a signal in [-1, 1] (or [0, 1] for one-sided factors), signed so that a positive
     signal means more risk.
  2. Factors are averaged within their family; families are combined with the weights in
     weights.json. Missing factors and families are skipped and the rest re-weighted, so
     missing data never counts as bad data.
  3. The combined signal (x `spread`) plus recent-event bumps ("shocks") is a log-odds
     score. Its intercept is set so the average predicted 12-month chance of leaving across
     current employees equals the base rate (the org's observed rate when given, else the
     configured default).
  4. The probability maps to the familiar 0-100 index through fixed anchors
     (35% -> 70 "high", 15% -> 40 "medium"), so risk bands keep a real meaning.
  5. Reason codes are the largest family contributions and shocks, each explained by the
     factor that drove it.

Value and the panel scores are weighted averages of peer-ranked factors (0..1, higher is
better), with the same skip-and-reweight rule and reason codes measured from the middle.
"""
from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..heuristic import ScoreResult
from .catalog import F, MIN_GROUP, PANEL, VALUE_LABELS, Factor

WEIGHTS_PATH = Path(__file__).with_name("weights.json")
TOP_REASONS = 3


def load_weights(path: Path = WEIGHTS_PATH) -> dict:
    return json.loads(path.read_text())


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _consented(f: Factor, row: dict) -> bool:
    if f.consent == "signals":
        return int(row.get("consented_signals") or 0) == 1
    if f.consent == "monitoring":
        return int(row.get("monitoring_consent") or 0) == 1
    return True


@dataclass
class Ranked:
    pct: float          # 0..1 position among the comparison group (or on the absolute scale)
    raw: float
    group: str          # plain-language comparison group, for explanations
    peer_based: bool    # False when we fell back to fixed anchors


class _Normalizer:
    """Ranks each factor value against comparison groups built from the reference rows."""

    def __init__(self, rows: list[dict], raw: dict[str, list[float | None]]) -> None:
        self.rows = rows
        self.raw = raw
        ref = [i for i, r in enumerate(rows) if r.get("reference", True)]
        self.dists: dict[tuple, list[float]] = {}
        for key, values in raw.items():
            for i in ref:
                v = values[i]
                if v is None:
                    continue
                r = rows[i]
                for gkey in (("role", r.get("level"), r.get("division")),
                             ("level", r.get("level")), ("all",)):
                    self.dists.setdefault((key, *gkey), []).append(v)
        for d in self.dists.values():
            d.sort()

    def rank(self, key: str, i: int) -> Ranked | None:
        v = self.raw[key][i]
        if v is None:
            return None
        f = F[key]
        r = self.rows[i]
        level, division = r.get("level"), r.get("division")
        if f.peer != "absolute":
            groups = ([(("role", level, division), f"L{level} {division} peers"),
                       (("level", level), f"L{level} peers")] if f.peer == "role" else [])
            groups.append((("all",), "colleagues"))
            for gkey, label in groups:
                dist = self.dists.get((key, *gkey))
                if dist and len(dist) >= MIN_GROUP:
                    lo = bisect.bisect_left(dist, v)
                    hi = bisect.bisect_right(dist, v)
                    return Ranked((lo + 0.5 * (hi - lo)) / len(dist), v, label, True)
        a, b = f.anchors
        scaled = _clamp01((v - a) / (b - a)) if b != a else 0.5
        return Ranked(scaled, v, "colleagues", f.peer == "absolute")


class V2Scorer:
    """Peer-relative, multi-factor scorer behind the same `score(rows)` seam as v1."""

    def __init__(self, weights: dict | None = None) -> None:
        self.w = weights or load_weights()
        self.name = self.w["version"]

    # ---- public --------------------------------------------------------------------------
    def score(self, rows: list[dict], *, base_rate: float | None = None) -> list[ScoreResult]:
        if not rows:
            return []
        rows = [self._with_division_output(r) for r in self._division_output(rows)]
        raw = {key: [f.get(r) if _consented(f, r) else None for r in rows]
               for key, f in F.items()}
        norm = _Normalizer(rows, raw)

        fr_cfg = self.w["flight_risk"]
        partial = [self._flight_risk_terms(norm, rows, i) for i in range(len(rows))]
        intercept = self._intercept([p["logit"] for p in partial], rows, base_rate)
        probs = [_sigmoid(p["logit"] + intercept) for p in partial]
        ref_probs = sorted(p for p, r in zip(probs, rows) if r.get("reference", True))
        # Value is reported as a percentile among current employees ("more valuable than
        # X% of colleagues"); averaging many peer ranks would otherwise bunch everyone
        # near the middle. Small batches keep the raw 0..1 composite.
        values = [self._composite(norm, i, self._value_spec())[0] for i in range(len(rows))]
        ref_values = sorted(v for v, r in zip(values, rows)
                            if v is not None and r.get("reference", True))

        results = []
        for i, r in enumerate(rows):
            p = probs[i]
            index = self._index(p, fr_cfg["index_anchors"])
            reasons = self._reason_codes(partial[i]["contribs"])
            value = values[i]
            panel = self._panel(norm, i, index, reasons)
            percentile = None
            if len(ref_probs) >= MIN_GROUP:
                lo = bisect.bisect_left(ref_probs, p)
                hi = bisect.bisect_right(ref_probs, p)
                percentile = round(100 * (lo + 0.5 * (hi - lo)) / len(ref_probs))
            if value is None:
                value_score = 0
            elif len(ref_values) >= MIN_GROUP:
                lo = bisect.bisect_left(ref_values, value)
                hi = bisect.bisect_right(ref_values, value)
                value_score = round(100 * (lo + 0.5 * (hi - lo)) / len(ref_values))
            else:
                value_score = round(value * 100)
            results.append(ScoreResult(
                employee_token=r["employee_token"], flight_risk=index, risk_trend=0,
                value_score=value_score, reason_codes=reasons, metrics=panel,
                capital=self._capital(r, p, value_score),
                leave_probability=round(p, 4), risk_percentile=percentile,
                confidence=partial[i]["confidence"], scorer_version=self.name,
            ))
        return results

    # ---- flight risk -----------------------------------------------------------------------
    def _flight_risk_terms(self, norm: _Normalizer, rows: list[dict], i: int) -> dict:
        cfg = self.w["flight_risk"]
        fams = cfg["families"]
        fam_signal: dict[str, tuple[float, str, Ranked, bool]] = {}
        coverage_num = peer_num = covered = 0.0
        total_w = sum(spec["weight"] for spec in fams.values())
        for fam, spec in fams.items():
            num = den = 0.0
            drivers: list[tuple[float, str, Ranked]] = []
            fam_total = sum(spec["factors"].values())
            fam_peer = 0.0
            for key, w in spec["factors"].items():
                ranked = norm.rank(key, i)
                if ranked is None:
                    continue
                f = F[key]
                if f.one_sided:
                    s = ranked.pct
                else:
                    s = f.risk_dir * (2 * ranked.pct - 1)
                num += w * s
                den += w
                fam_peer += w if ranked.peer_based else 0.0
                drivers.append((w * s, key, ranked))
            if den == 0:
                continue
            signal = num / den
            coverage_num += spec["weight"] * (den / fam_total)
            covered += spec["weight"]
            peer_num += spec["weight"] * (fam_peer / den)
            # The factor that pushed hardest in the family's direction explains it.
            drivers.sort(key=lambda d: d[0] if signal >= 0 else -d[0], reverse=True)
            _, top_key, top_ranked = drivers[0]
            fam_signal[fam] = (signal, top_key, top_ranked, signal >= 0)

        contribs: list[dict] = []
        logit = 0.0
        if covered:
            for fam, (signal, key, ranked, up) in fam_signal.items():
                c = cfg["spread"] * (fams[fam]["weight"] / covered) * signal
                logit += c
                f = F[key]
                label = f.labels[0] if up else f.labels[1]
                if label:
                    contribs.append({"label": label, "direction": "increases" if c >= 0 else "decreases",
                                     "magnitude": abs(c),
                                     "detail": f.detail(ranked.raw, up, ranked.group)})
        for shock in self._shocks(rows[i]):
            logit += shock["magnitude"]
            contribs.append(shock)

        coverage = coverage_num / total_w if total_w else 0.0
        peer_share = peer_num / covered if covered else 0.0
        confidence = round(100 * coverage * (0.7 + 0.3 * peer_share))
        return {"logit": logit, "contribs": contribs, "confidence": confidence}

    def _shocks(self, row: dict) -> list[dict]:
        cfg = self.w["flight_risk"]["shocks"]
        out = []
        events = [
            ("recent_manager_change", row.get("months_since_manager_change"),
             "New manager {m} month(s) ago", "new_manager"),
            ("recent_vest", row.get("months_since_vest"),
             "A stock vest paid out {m} month(s) ago", "equity_just_vested"),
            ("recent_internal_application", row.get("months_since_internal_application"),
             "Applied for an internal role {m} month(s) ago", "recent_internal_application"),
        ]
        for name, months, text, label in events:
            spec = cfg.get(name)
            if spec is None or months is None:
                continue
            m = float(months)
            window = float(spec["window_months"])
            if 0 <= m <= window:
                bump = spec["logit"] * (1.0 - m / (window + 1.0))   # fades over the window
                out.append({"label": label, "direction": "increases", "magnitude": bump,
                            "detail": text.format(m=int(m))})
        return out

    def _intercept(self, logits: list[float], rows: list[dict], base_rate: float | None) -> float:
        """Set the intercept so current employees' average chance of leaving equals the
        base rate (calibration-in-the-large). Small batches use the plain prior."""
        cfg = self.w["flight_risk"]
        rate = base_rate if base_rate is not None else cfg["default_base_rate"]
        prior = math.log(rate / (1 - rate))
        ref = [z for z, r in zip(logits, rows) if r.get("reference", True)]
        if len(ref) < cfg["min_rows_for_calibration"]:
            return prior
        lo, hi = -20.0, 20.0
        for _ in range(60):
            mid = (lo + hi) / 2
            mean = sum(_sigmoid(z + mid) for z in ref) / len(ref)
            if mean > rate:
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2

    @staticmethod
    def _index(p: float, anchors: list[list[float]]) -> int:
        for (p0, s0), (p1, s1) in zip(anchors, anchors[1:]):
            if p <= p1:
                return int(round(s0 + (s1 - s0) * (p - p0) / (p1 - p0)))
        return int(anchors[-1][1])

    @staticmethod
    def _reason_codes(contribs: list[dict]) -> list[dict]:
        top = sorted(contribs, key=lambda c: c["magnitude"], reverse=True)[:TOP_REASONS]
        total = sum(c["magnitude"] for c in top) or 1.0
        return [{"label": c["label"], "direction": c["direction"],
                 "weight": round(c["magnitude"] / total, 3), "detail": c["detail"]} for c in top]

    # ---- value + panel ---------------------------------------------------------------------
    def _value_spec(self) -> list[tuple[str, float, int, str, str]]:
        spec = []
        for fam in self.w["value"].values():
            for key, w in fam["factors"].items():
                pos, neg = VALUE_LABELS.get(key, (key, key))
                spec.append((key, fam["weight"] * w, 1, pos, neg))
        return spec

    @staticmethod
    def _composite(norm: _Normalizer, i: int,
                   spec: list[tuple[str, float, int, str, str]]) -> tuple[float | None, list[dict]]:
        num = den = 0.0
        contribs = []
        for key, w, good, pos, neg in spec:
            ranked = norm.rank(key, i)
            if ranked is None:
                continue
            s = ranked.pct if good > 0 else 1 - ranked.pct
            num += w * s
            den += w
            signed = (s - 0.5) * w
            contribs.append((abs(signed), pos if signed >= 0 else neg,
                             "increases" if signed >= 0 else "decreases"))
        if den == 0:
            return None, []
        contribs.sort(reverse=True)
        top = contribs[:TOP_REASONS]
        total = sum(c[0] for c in top) or 1.0
        codes = [{"label": lbl, "direction": d, "weight": round(m / total, 3)} for m, lbl, d in top]
        return num / den, codes

    def _panel(self, norm: _Normalizer, i: int, index: int, retention_reasons: list[dict]) -> list[dict]:
        out = []
        for metric, spec in PANEL.items():
            score, codes = self._composite(norm, i, spec)
            # Engagement & productivity exists only with consented engagement data; the
            # performance input alone must not produce it (no covert monitoring).
            if metric == "engagement_productivity" and norm.rank("engagement_level", i) is None:
                score = None
            if score is None:
                note = ("no consented engagement signal" if metric == "engagement_productivity"
                        else "not enough data")
                out.append({"metric": metric, "score": None, "reason_codes": [], "note": note})
            else:
                out.append({"metric": metric, "score": round(score * 100), "reason_codes": codes})
        out.insert(4, {"metric": "retention_risk", "score": index, "reason_codes": retention_reasons})
        return out

    # ---- capital ---------------------------------------------------------------------------
    def _capital(self, row: dict, p: float, value_score: int) -> list[dict]:
        cfg = self.w["capital"]
        fx = cfg["fx_to_usd"].get(row.get("salary_currency") or "USD")
        if row.get("base_salary") is not None and fx is not None:
            salary = float(row["base_salary"]) * fx
        else:   # no salary on file (or an unknown currency): modeled band for the level
            salary = float(cfg["salary_band_usd"].get(str(int(row.get("level") or 2)), 95_000))
        comp_gap = float(row.get("comp_gap") or 0.0)
        cost_to_lose = salary * (0.5 + (value_score / 100.0) * 1.5)
        expected_loss = cost_to_lose * p
        invest = expected_loss * cfg["retention_invest_fraction"]
        mitigation = _clamp01(0.30 + comp_gap * 1.5)
        roi = round((expected_loss * mitigation - invest) / invest, 2) if invest >= 1.0 else 0.0
        efficiency = round(_clamp01((value_score / 100.0) * (0.6 + comp_gap)) * 100)
        return [
            {"metric": "value_score", "amount": float(value_score), "unit": "score_0_100",
             "is_estimate": False},
            {"metric": "cost_to_lose", "amount": round(cost_to_lose, 0), "unit": "USD",
             "is_estimate": True},
            {"metric": "expected_loss", "amount": round(expected_loss, 0), "unit": "USD",
             "is_estimate": True},
            {"metric": "suggested_retention_investment", "amount": round(invest, 0),
             "unit": "USD", "is_estimate": True},
            {"metric": "retention_roi", "amount": roi, "unit": "ratio", "is_estimate": True},
            {"metric": "compensation_efficiency", "amount": float(efficiency),
             "unit": "score_0_100", "is_estimate": True},
        ]

    # ---- division output (needs the whole batch) ----------------------------------------------
    def _division_output(self, rows: list[dict]) -> list[dict]:
        """Rank each output signal within its division (current employees as the reference)
        and average the ranks into one 0..1 `division_output` per person."""
        directions = self.w["division_output_signals"]
        dists: dict[tuple[str, str], list[float]] = {}
        for r in rows:
            if not r.get("reference", True):
                continue
            for sig, v in (r.get("division_signals") or {}).items():
                if sig in directions and v is not None:
                    dists.setdefault((r.get("division"), sig), []).append(float(v))
        for d in dists.values():
            d.sort()
        out = []
        for r in rows:
            ranks = []
            for sig, v in (r.get("division_signals") or {}).items():
                dist = dists.get((r.get("division"), sig))
                if sig not in directions or v is None or not dist or len(dist) < MIN_GROUP:
                    continue
                lo = bisect.bisect_left(dist, float(v))
                hi = bisect.bisect_right(dist, float(v))
                pct = (lo + 0.5 * (hi - lo)) / len(dist)
                ranks.append(pct if directions[sig] > 0 else 1 - pct)
            out.append({**r, "_division_output": sum(ranks) / len(ranks) if ranks else None})
        return out

    @staticmethod
    def _with_division_output(r: dict) -> dict:
        r = dict(r)
        r["division_output"] = r.pop("_division_output", None)
        return r
