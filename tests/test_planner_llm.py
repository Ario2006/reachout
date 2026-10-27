from pydantic import ValidationError
import re
import threading
import time

import pytest

from backend.core import llm, planner
from backend.core.models import AppSettings, Mode
from backend.core.planner import Plan

S = AppSettings()


def plan_json(**kw):
    base = {"mode": "internship", "target_roles": ["founder"], "industries": ["AI"], "regions": ["Bangalore"],
            "keywords": ["seed"], "search_queries": [f"ai startup bangalore {i}" for i in range(8)]}
    return Plan.model_validate({**base, **kw})


def test_plan_model_cleans_dedupes_and_caps():
    p = Plan(mode=Mode.service, search_queries=['  "a b"  ', "A B", "c", ""])
    assert p.search_queries == ["a b", "c"]
    with pytest.raises(ValidationError):
        Plan(mode=Mode.service, search_queries=[f"q{i}" for i in range(13)])
    with pytest.raises(ValidationError):
        Plan.model_validate({"mode": "bogus"})


def test_make_plan_uses_llm_and_respects_explicit_mode(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: plan_json(mode="investor"))
    r = planner.make_plan("anything", S)
    assert r.source == "llm" and r.plan.mode == Mode.investor
    r = planner.make_plan("anything", S, mode=Mode.service)
    assert r.plan.mode == Mode.service  # user's choice wins


def test_make_plan_filters_job_board_queries_and_adds_company_and_list_queries(monkeypatch):
    bad = ["software engineer jobs Bangalore", "AI internship vacancies Bangalore", "freshers walk-in Bangalore"]
    good = ["AI startup Bangalore careers", "fintech company Bangalore about us"]
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: plan_json(search_queries=bad + good, target_roles=[]))
    r = planner.make_plan("AI internships in Bangalore", S)
    qs = r.plan.search_queries
    assert 6 <= len(qs) <= 12 and r.plan.target_roles
    assert not any(q in qs for q in bad)
    assert all(q in qs for q in good)
    assert any(q.lower().startswith(("top ", "list of")) for q in qs)  # list pages that link to companies
    assert len({q.lower() for q in qs}) == len(qs)


def test_job_query_with_company_words_is_kept():
    assert planner._company_oriented("startup careers Bangalore")
    assert planner._company_oriented("AI startup jobs Bangalore")
    assert not planner._company_oriented("software engineer jobs Bangalore")
    assert planner._company_oriented("top AI startups in Bangalore")


@pytest.mark.parametrize("request_text,mode", [
    ("Software/AI internships at seed-stage startups in Bangalore", Mode.internship),
    ("Pre-seed investors who back AI developer tools in India/SEA", Mode.investor),
    ("Founders of D2C brands who need a website redesign", Mode.service),
])
def test_fallback_when_model_unavailable_gives_usable_plan(monkeypatch, request_text, mode):
    def down(*a, **k):
        raise llm.LlmError("connection refused")

    monkeypatch.setattr(llm, "generate_json", down)
    r = planner.make_plan(request_text, S)
    assert r.source == "fallback" and r.plan.mode == mode and "not running" in r.note and "refused" not in r.note
    assert 6 <= len(r.plan.search_queries) <= 12 and r.plan.target_roles and r.plan.industries


def test_fallback_extracts_regions_and_industry():
    p = planner.make_plan.__globals__["_fallback_plan"]("AI internships in Bangalore", Mode.internship)
    assert p.regions == ["Bangalore"] and "ai" in p.industries[0]


def test_empty_request_rejected():
    with pytest.raises(ValueError):
        planner.make_plan("   ", S)


# ---- llm.generate_json ---------------------------------------------------------------------

class FakeMsg:
    def __init__(self, content): self.message = type("M", (), {"content": content})()


class FakeClient:
    def __init__(self, replies, log):
        self.replies, self.log = list(replies), log

    def chat(self, **kw):
        self.log.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeMsg(r)


def patch_client(monkeypatch, replies):
    log: list = []
    monkeypatch.setattr(llm.ollama, "Client", lambda **kw: FakeClient(replies, log))
    return log


def test_generate_json_success_passes_schema_and_options(monkeypatch):
    log = patch_client(monkeypatch, ['{"name": "x"}'])

    class M(llm.BaseModel):
        name: str

    out = llm.generate_json(M, "sys", "user", model="m", num_ctx=2048, keep_alive="1m")
    assert out.name == "x"
    kw = log[0]
    assert kw["model"] == "m" and kw["format"] == M.model_json_schema() and kw["keep_alive"] == "1m"
    assert kw["options"]["num_ctx"] == 2048 and kw["messages"][0]["role"] == "system"


def test_generate_json_retries_once_on_invalid_then_fails(monkeypatch):
    class M(llm.BaseModel):
        n: int

    log = patch_client(monkeypatch, ["not json", '{"n": 3}'])
    assert llm.generate_json(M, "s", "u", model="m").n == 3 and len(log) == 2
    log = patch_client(monkeypatch, ["bad", "worse"])
    with pytest.raises(llm.LlmError):
        llm.generate_json(M, "s", "u", model="m")
    assert len(log) == 2


def test_generate_json_connection_errors_do_not_retry(monkeypatch):
    log = patch_client(monkeypatch, [ConnectionError("refused"), '{"n":1}'])

    class M(llm.BaseModel):
        n: int

    with pytest.raises(llm.LlmError, match="refused"):
        llm.generate_json(M, "s", "u", model="m")
    assert len(log) == 1


def test_prompt_is_truncated_to_about_3000_tokens(monkeypatch):
    log = patch_client(monkeypatch, ['{"n":1}'])

    class M(llm.BaseModel):
        n: int

    llm.generate_json(M, "s", "x" * 100_000, model="m")
    assert len(log[0]["messages"][1]["content"]) == llm.MAX_PROMPT_CHARS == 12_000


def test_only_one_generation_at_a_time(monkeypatch):
    state = {"active": 0, "max": 0}

    class Slow:
        def chat(self, **kw):
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            time.sleep(0.05)
            state["active"] -= 1
            return FakeMsg('{"n":1}')

    monkeypatch.setattr(llm.ollama, "Client", lambda **kw: Slow())

    class M(llm.BaseModel):
        n: int

    ts = [threading.Thread(target=llm.generate_json, args=(M, "s", "u"), kwargs={"model": "m"}) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert state["max"] == 1


def test_user_role_is_not_a_contact_role_and_defaults_are_merged(monkeypatch):
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: plan_json(target_roles=["intern", "Software Intern", "CTO", "student"]))
    r = planner.make_plan("AI internships in Bangalore", S)
    roles = [x.lower() for x in r.plan.target_roles]
    assert "intern" not in roles and "student" not in roles and "software intern" not in roles
    assert roles[0] == "cto" and "founder" in roles and "recruiter" in roles


def test_service_mode_drops_queries_about_the_service_itself(monkeypatch):
    bad = ["D2C brands need website redesign", "website redesign D2C brands", "ecommerce web design agency", "freelance designer for D2C"]
    good = ["top D2C brands in India", "D2C skincare brand contact us"]
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: plan_json(mode="service", industries=["D2C brands", "website redesign"], search_queries=bad + good))
    r = planner.make_plan("Founders of D2C brands who need a website redesign", S)
    qs = r.plan.search_queries
    assert not any(q in qs for q in bad) and all(q in qs for q in good)
    assert not any(re.search(r"redesign|web design|agency|freelanc", q, re.I) for q in qs)
    assert r.plan.industries == ["D2C brands"]


def test_the_same_queries_are_fine_in_other_modes():
    assert planner._company_oriented("startup web design agency careers", Mode.internship)
    assert planner._company_oriented("website redesign D2C brands", Mode.investor)
    assert not planner._company_oriented("website redesign D2C brands", Mode.service)


@pytest.mark.parametrize("regions,request_text,expected", [
    (["United States"], "Founders of D2C brands who need a website redesign", []),
    (["India", "SEA"], "Pre-seed investors who back AI developer tools in India/SEA", ["India", "SEA"]),
    (["Bangalore", "Mumbai"], "AI internships in Bangalore", ["Bangalore"]),
    (["New York"], "fashion brands in york", ["New York"]),
    (["Southeast Asia"], "investors in south east asia", ["Southeast Asia"]),
    ([], "anything", []),
])
def test_regions_must_come_from_the_request(monkeypatch, regions, request_text, expected):
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: plan_json(regions=regions))
    assert planner.make_plan(request_text, S).plan.regions == expected


def test_fallback_note_never_leaks_the_raw_error(monkeypatch):
    def bad(*a, **k):
        raise llm.LlmError("Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible. https://ollama.com/download")
    monkeypatch.setattr(llm, "generate_json", bad)
    note = planner.make_plan("AI interns", S).note
    assert "https://" not in note and "Failed to connect" not in note and "not running" in note

    def junk(*a, **k):
        raise llm.LlmError("Invalid model output: 2 validation errors for Plan ...")
    monkeypatch.setattr(llm, "generate_json", junk)
    note = planner.make_plan("AI interns", S).note
    assert "validation errors" not in note and "usable plan" in note
