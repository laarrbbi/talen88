"""Per-org RAG context: customization without fine-tuning, with STRICT cross-org isolation.

These tests prove the retrieval/context layer:
  * Loads exactly one org's profile by id — never bleeds another org's terminology in.
  * A missing profile degrades to a minimal profile (no fallback to some other org).
  * A mislabelled profile file cannot impersonate another org (org_id is forced).
  * Live retrieval uses ONLY the caller's scoped tools client, so org A's data can never
    appear in org B's AI context (isolation inherited from the query seam, no shared cache).
  * End-to-end: the org's own context reaches the assembled prompt, still token-only (no PII).
"""
from __future__ import annotations

import json

from agents import orgcontext, reasoning


# ---- a tiny scoped-tools fake (audit-capable so AI/retrieval runs) -----------
class OrgTools:
    """Stands in for DataTools scoped to ONE org's caller — returns only its own data."""
    def __init__(self, headline, by_division):
        self._dash = {"headline": headline, "by_division": by_division, "quadrant": []}
        self.llm_logged = []

    def dashboard(self):
        return self._dash

    def log_llm(self, **kw):
        self.llm_logged.append(kw)


def _write_profile(dirpath, org_id, **fields):
    (dirpath / f"{org_id}.json").write_text(json.dumps(fields))


# ---- profile isolation -------------------------------------------------------
def test_profile_loads_only_requested_org(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    _write_profile(tmp_path, "acme", display_name="Acme Bank",
                   terminology={"flight risk": "ACME-SPECIFIC-TERM-AAA"})
    _write_profile(tmp_path, "globex", display_name="Globex",
                   terminology={"flight risk": "GLOBEX-SPECIFIC-TERM-BBB"})

    acme = orgcontext.load_org_profile("acme")
    globex = orgcontext.load_org_profile("globex")

    assert acme.org_id == "acme" and globex.org_id == "globex"
    # Each profile carries ONLY its own terminology.
    assert "ACME-SPECIFIC-TERM-AAA" in acme.render()
    assert "ACME-SPECIFIC-TERM-AAA" not in globex.render()
    assert "GLOBEX-SPECIFIC-TERM-BBB" in globex.render()
    assert "GLOBEX-SPECIFIC-TERM-BBB" not in acme.render()


def test_missing_profile_is_minimal_not_another_org(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    _write_profile(tmp_path, "acme", display_name="Acme Bank",
                   terminology={"x": "ACME-SECRET"})
    missing = orgcontext.load_org_profile("nope")
    assert missing.org_id == "nope"
    assert "ACME-SECRET" not in missing.render()  # no bleed from the one existing profile


def test_mislabelled_file_cannot_impersonate_another_org(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    # A file claims to be 'globex' inside, but it is named acme.json.
    (tmp_path / "acme.json").write_text(json.dumps({"org_id": "globex", "display_name": "Globex"}))
    loaded = orgcontext.load_org_profile("acme")
    assert loaded.org_id == "acme"  # forced to the requested id; cannot claim to be globex


def test_active_org_id_reads_env(monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_ID", "acme")
    assert orgcontext.active_org_id() == "acme"
    monkeypatch.delenv("TALENT88_ORG_ID", raising=False)
    assert orgcontext.active_org_id() == "default"


# ---- live-data isolation (the RAG retrieval path) ----------------------------
def test_live_retrieval_isolated_per_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    _write_profile(tmp_path, "acme", display_name="Acme Bank")
    _write_profile(tmp_path, "globex", display_name="Globex")

    acme_tools = OrgTools({"total_scored": 10, "high_risk_count": 3, "act_now_count": 1},
                          [{"division": "acme-trading", "high_risk_count": 3}])
    globex_tools = OrgTools({"total_scored": 99, "high_risk_count": 40, "act_now_count": 9},
                            [{"division": "globex-mining", "high_risk_count": 40}])

    acme_ctx = orgcontext.build_context(acme_tools, org_id="acme")
    globex_ctx = orgcontext.build_context(globex_tools, org_id="globex")

    # Each context reflects ONLY its own caller's retrieved data + its own profile.
    assert "acme-trading" in acme_ctx and "Acme Bank" in acme_ctx
    assert "globex-mining" not in acme_ctx and "Globex" not in acme_ctx
    assert "globex-mining" in globex_ctx and "globex-mining" not in acme_ctx
    assert "acme-trading" not in globex_ctx


def test_build_context_degrades_when_no_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    # No profile, no dashboard -> nothing useful -> None (prompt stays lean, no crash).
    class Bare:
        def log_llm(self, **kw):
            pass
    assert orgcontext.build_context(Bare(), org_id="unknown") is None


# ---- end-to-end: org context reaches the prompt, still token-only ------------
def test_org_context_injected_into_prompt_without_pii(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT88_ORG_PROFILES_DIR", str(tmp_path))
    _write_profile(tmp_path, "acme", display_name="Acme Bank",
                   terminology={"flight risk": "ACME-SPECIFIC-TERM-AAA"})
    monkeypatch.setenv("TALENT88_ORG_ID", "acme")

    sent = {}

    def _capture(messages):
        sent["messages"] = messages
        from llm import LLMResponse
        return LLMResponse(text="emp_1 is high risk.", model="gemma3:1b")

    monkeypatch.setattr(reasoning, "generate", _capture)
    tools = OrgTools({"total_scored": 2, "high_risk_count": 1, "act_now_count": 1},
                     [{"division": "acme-trading", "high_risk_count": 1}])
    out = reasoning.narrate(tools, agent="t", system="be terse",
                            data_json=json.dumps({"token": "emp_1", "flight_risk": 88}))

    assert out == "emp_1 is high risk."
    system_msg = sent["messages"][0]["content"]
    assert "ACME-SPECIFIC-TERM-AAA" in system_msg          # org's own terminology injected
    assert "acme-trading" in system_msg                     # live retrieved aggregate injected
    blob = "\n".join(m["content"] for m in sent["messages"])
    assert not reasoning.contains_pii(blob, names=["Riley Reyes"])  # still token-only
