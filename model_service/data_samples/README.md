# `data_samples/` — development/scaffolding data ONLY

## `ibm_hr_attrition.csv`

The **IBM HR Analytics Employee Attrition & Performance** dataset: ~1,470 rows,
35 columns, with a binary `Attrition` (Yes/No) label. ~16% of rows are leavers
(`Yes`) — attrition is the minority class.

> **This is fictional, public data created by IBM data scientists.** It exists here
> for **one purpose only: to prove the ML pipeline end-to-end** (load → preprocess →
> feature-engineer → train → validate → explain → score behind the `/score` seam).
>
> **It is NOT a shippable model.** The numbers it produces are meaningless for any
> real workforce. Before any production use, the model **must be retrained on a
> design partner's real, labeled leaver history** with **time-based validation**
> (train on a past window, test on a strictly later window). This cross-sectional
> public set cannot support time-based validation, and its feature distribution is
> unrelated to any real customer. Treat every metric here as a pipeline smoke-test,
> not evidence of real-world performance.

### Provenance
Originally published by IBM as sample data for Watson Analytics; widely mirrored
(e.g. on Kaggle as "IBM HR Analytics Employee Attrition & Performance"). No real
employees, no PII — entirely synthetic.

### Columns of note
- `Attrition` — the target (Yes/No).
- Constant columns (`EmployeeCount`, `Over18`, `StandardHours`) carry no signal and
  are dropped in preprocessing.
- `EmployeeNumber` — a row identifier; dropped (never a feature).
- The rest are demographic, compensation, role, tenure, and satisfaction fields.
