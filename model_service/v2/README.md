# Scoring v2 — model card

The default scorer behind `POST /score` (`SCORING_BACKEND=v2`). Version: `talent88-v2.0`
(every score row stores it). v1 (`heuristic`) and the trained ML backend (`ml`) remain
selectable; a request may also ask for `"scorer": "v1"` or `"v2"` explicitly.

## What it produces

| Output | Meaning |
| ------ | ------- |
| `leave_probability` | Chance of leaving in the next 12 months (0–1), calibrated so current employees average the org's observed 12-month leave rate (or 12% when there are fewer than 20 recent leavers). |
| `flight_risk` | The familiar 0–100 index, a fixed mapping of the probability: 5% → 20, 15% → 40 (medium), 35% → 70 (high), 60% → 90. |
| `risk_percentile` | Rank among current employees (0–100, higher is riskier). |
| `confidence` | How complete the inputs were (0–100): family coverage, discounted when a comparison group was too small and fixed scales were used. |
| `value_score` | Percentile among current employees on talent value ("more valuable than X% of colleagues"). Key people = top 20% (≥ 80). |
| `reason_codes` | Top three drivers, each with a weight and a plain-language `detail`. |
| `metrics` | The eight-score panel, all on the same peer-ranked method. |
| `capital` | Cost to lose (real salary converted to USD, else a modeled band), **`expected_loss`** (dollars at risk = probability × cost to lose), suggested retention investment, ROI, compensation efficiency. All flagged as estimates. |

## How flight risk is computed

1. **Compare with peers.** Each factor is ranked against people at the same level in the
   same division; groups under 8 people widen to the same level, then to everyone. A few
   shape-like factors (the 18–30 month tenure window, counts of internal applications or
   departed teammates) use fixed scales so zero stays neutral.
2. **Families.** Factors average within seven families, weighted in `weights.json`:

   | Family | Weight | Factors |
   | ------ | -----: | ------- |
   | Engagement & sentiment\* | 20% | engagement trend and level, eNPS, job satisfaction, work-life balance, survey sentiment, recognition, manager-effectiveness score |
   | Career & job search | 20% | months since promotion, promotion pace, years in role, internal applications |
   | Pay & reward | 18% | gap to band midpoint, gap to market, pay rank in role, months since raise, last raise size |
   | Manager & team | 14% | manager changes, team turnover, teammates who left recently, manager's rating |
   | Market pull | 10% | outside demand for their skills, industry hiring, rare skill mix, regional pay movement, performance standing (strong performers get offers) |
   | Ties & timing | 10% | tenure window, unvested equity (keeps people), prior-employer tenure, job-hopping history |
   | Workload & wellbeing\* | 8% | unused time off, overtime, commute, after-hours load |

   \* consent-gated where the data is behavioural.
3. **Missing data is skipped, not penalised.** Absent factors and families drop out and the
   rest are re-weighted; `confidence` reports the gap.
4. **Recent events** (`shocks` in `weights.json`): a new manager (6 months), a stock vest
   that just paid out (3 months), an internal application (3 months) add risk that fades.
5. **Calibration-in-the-large** sets the intercept so the average probability over current
   employees equals the base rate. People who have left are scored but never shape peer
   groups or the calibration.

## Safeguards

- **Scoring wall.** No protected trait (gender, birth-year band, marital status) or identity
  proxy (school prestige, named employer) is an input; `test_scoring_wall_holds_for_v2`
  checks the input list and the service contract.
- **Consent.** Engagement and survey fields are sent and used only when
  `consented_signals = 1`; collaboration metadata only when `monitoring_consent = 1`.
  Without consent the engagement panel score is absent, not estimated.
- **Survey confidentiality.** Engagement explanations are generic ("Engagement signals are
  weaker or falling (from data this person agreed to share)") and never show an answer.
- **Decision support only.** Scores feed L1–L3 agents; nothing acts automatically.

## Validation and fairness

`python -m data.backtest` scores the latest snapshot with v1 and v2 and compares against
`label_attrition`: AUC, average precision, top-10% capture, Brier score, and calibration by
decile. It then runs two aggregate checks through `data/bias_audit.py` (admin-only,
audited, cohorts under 5 suppressed):

- **High-risk flag rate by protected cohort**, with the four-fifths adverse-impact ratio
  and a Bonferroni-adjusted Fisher exact test (`review` only when the gap is both large and
  unlikely to be chance).
- **Proxy screen**: factors whose cohort means differ by ≥ 0.3 SD (and significantly) are
  listed for review — for example commute distance or time off acting as a stand-in.

On the synthetic demo data (latest run): v2 AUC 0.915 vs v1 0.882; the riskiest 10% hold
47% of leavers (v1 30%); Brier 0.077 vs 0.135.

**Limits.** The demo generator creates leavers from these same drivers, so this shows the
pipeline works, not that it predicts real people. Before relying on it, retrain/re-weight
on a client's leaver history with a time-based split, review the fairness report with HR
and counsel (NYC Local Law 144 and the EU AI Act's employment rules may apply), and record
any weight change as a new `version`.
