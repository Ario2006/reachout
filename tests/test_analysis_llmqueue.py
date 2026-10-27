import asyncio
from pathlib import Path

import pytest

from backend.core import analysis as an, llm, llmqueue as lq
from backend.core.extractor import Person
from backend.core.models import EmailConfidence

FX = Path(__file__).parent / "fixtures"
SITE = {"acme.ai"}


def A(html, url="https://acme.ai/team", **kw):
    return an.analyze_page(url, html, SITE, domain="acme.ai", **kw)


# ---- when is the model needed? ------------------------------------------------------------------

def test_team_cards_resolve_everyone_so_the_model_is_not_needed():
    a = A((FX / "team_cards.html").read_text())
    assert a.needs_llm is False and a.llm_text == ""
    r = an.finalize_page(a)
    by = {d.email: d for d in r.drafts}
    assert (by["priya@acme.ai"].name, by["priya@acme.ai"].role, by["priya@acme.ai"].email_confidence) == ("Priya Sharma", "Co-founder & CEO", EmailConfidence.published_personal)
    assert by["ana.li@acme.ai"].name == "Ana Li"
    assert {p.name for p, _ in r.people} == {"Priya Sharma", "Rahul Verma", "Ana Li"}  # Rahul has no email but is known for guesses
    assert r.llm_calls == 0


def test_jsonld_people_and_org_contact_address_need_no_model():
    html = (FX / "jsonld_team.html").read_text()
    a = A(html)
    assert not a.needs_llm
    by = {d.email: d for d in an.finalize_page(a).drafts}
    assert by["priya@acme.ai"].email_confidence == EmailConfidence.published_personal
    assert by["contact@acme.ai"].email_confidence == EmailConfidence.published_generic and by["contact@acme.ai"].company == "Acme AI"
    assert "structured data" in by["contact@acme.ai"].context_snippet


def test_generic_addresses_on_a_contact_page_need_no_model():
    a = A('<html><body><p>Write to <a href="mailto:hello@acme.ai">hello@acme.ai</a> or careers@acme.ai</p></body></html>', "https://acme.ai/contact")
    assert not a.needs_llm and {d.email for d in an.finalize_page(a).drafts} == {"hello@acme.ai", "careers@acme.ai"}


def test_a_personal_looking_address_nobody_names_needs_the_model():
    a = A('<html><body><p>Reach <a href="mailto:priya@acme.ai">priya</a></p></body></html>', "https://acme.ai/contact")
    assert a.needs_llm and a.unresolved == ["priya@acme.ai"] and a.llm_text
    r = an.finalize_page(a, [Person(name="Priya Sharma", role="CEO")])
    assert (r.drafts[0].name, r.drafts[0].email_confidence) == ("Priya Sharma", EmailConfidence.published_personal)
    assert "language model" in dict(r.drafts[0].trace)["label"]


def test_a_team_page_without_structure_needs_the_model_for_people():
    html = "<html><body><h1>Team</h1><p>" + "Our founder Priya Sharma and CTO Rahul Verma lead the company. " * 5 + "</p></body></html>"
    a = A(html)
    assert a.needs_llm and not a.unresolved


@pytest.mark.parametrize("url", ["https://acme.ai/jobs/ml-intern", "https://acme.ai/portfolio/alpha", "https://acme.ai/blog/team-update", "https://acme.ai/top-startups/"])
def test_job_portfolio_and_list_pages_never_need_the_model(url):
    a = A('<html><body>Founder Jane Doe. CEO Bob Ray. Partner Al Ko. <a href="mailto:jane@acme.ai">j</a></body></html>', url)
    assert not a.needs_llm


def test_an_empty_page_is_harmless():
    a = A("")
    assert a.hits == [] and not a.needs_llm and an.finalize_page(a).drafts == []


def test_the_name_beside_the_address_still_works_without_a_model():
    a = A('<div><h3>Meet Priya Sharma</h3><p><a href="mailto:priya.sharma@acme.ai">Email</a></p></div>', "https://acme.ai/about")
    assert not a.needs_llm
    d = an.finalize_page(a).drafts[0]
    assert d.name == "Priya Sharma" and "beside the address" in dict(d.trace)["label"]


# ---- provenance -------------------------------------------------------------------------------

def test_drafts_carry_provenance_and_a_reason_for_their_label():
    a = A('<p><a href="mailto:hello@acme.ai">x</a> and john.smith@acme.ai</p>', "https://acme.ai/contact", tier="browser", fetched_at=1234.5)
    by = {d.email: d for d in an.finalize_page(a).drafts}
    d = by["hello@acme.ai"]
    assert (d.page_tier, d.page_type, d.fetched_at, d.source_url) == ("browser", "contact", 1234.5, "https://acme.ai/contact")
    kinds = [k for k, _ in d.trace]
    assert kinds == ["found", "label"] and "served from browser" in d.trace[0][1] and "role-style" in d.trace[1][1]
    assert "no name on the page matches" in dict(by["john.smith@acme.ai"].trace)["label"]


def test_the_legacy_extract_page_wrapper_still_calls_the_model_only_when_needed():
    from backend.core.crawler import Page
    from backend.core.extractor import extract_page
    from backend.core.models import AppSettings

    calls = []
    fn = lambda text, domain: calls.append(1) or [Person(name="Priya Sharma", role="CEO")]  # noqa: E731
    r = extract_page(Page(url="https://acme.ai/team", html=(FX / "team_cards.html").read_text()), SITE, AppSettings(), people_fn=fn, domain="acme.ai")
    assert calls == [] and r.llm_calls == 0
    r = extract_page(Page(url="https://acme.ai/contact", html='<p>Priya Sharma, CEO: <a href="mailto:priya@acme.ai">p</a></p>'), SITE, AppSettings(), people_fn=fn, domain="acme.ai")
    assert calls == [1] and r.llm_calls == 1 and r.drafts[0].name == "Priya Sharma"


# ---- the batching queue ---------------------------------------------------------------------------

def run(coro): return asyncio.run(coro)


class Fake:
    """A stand-in for the model: records each call, answers per page from a dict url->people."""

    def __init__(self, answers=None, delay=0.0, fail=0):
        self.calls: list[str] = []
        self.answers, self.delay, self.fail = answers or {}, delay, fail
        self.batcher = None

    async def __call__(self, body: str, n: int):
        self.calls.append(body)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            self.fail -= 1
            raise llm.LlmError("boom")
        pages = []
        for i in range(1, n + 1):
            site = body.split(f"### PAGE {i} (site: ")[1].split(")")[0]
            pages.append(lq.PagePeople(page=i, people=self.answers.get(site, [])))
        return lq.BatchPeople(pages=pages)


def P(name, role=""): return Person(name=name, role=role)


def test_snippets_arriving_together_share_one_call_and_results_map_back_to_their_page():
    fake = Fake({"a.com": [P("Ann Lee", "CEO")], "b.com": [P("Bob Ray", "CTO")], "c.com": []})

    async def go():
        b = lq.PeopleBatcher(call=fake)
        res = await asyncio.gather(b.submit("a.com", "u1", "Ann Lee is our CEO"), b.submit("b.com", "u2", "Bob Ray, CTO"), b.submit("c.com", "u3", "nobody"))
        await b.close()
        return res, b

    (a, b_, c), b = run(go())
    assert len(fake.calls) == 1 and b.snapshot()["pages_per_call"] == 3.0
    assert [p.name for p in a] == ["Ann Lee"] and [p.name for p in b_] == ["Bob Ray"] and c == []
    assert "### PAGE 1 (site: a.com)" in fake.calls[0] and "### PAGE 3 (site: c.com)" in fake.calls[0]


def test_names_must_appear_in_that_pages_own_text():
    fake = Fake({"a.com": [P("Ann Lee", "CEO"), P("Ghost Writer", "CTO")], "b.com": [P("Ann Lee", "CEO")]})

    async def go():
        b = lq.PeopleBatcher(call=fake)
        res = await asyncio.gather(b.submit("a.com", "u1", "Ann Lee is the CEO."), b.submit("b.com", "u2", "Totally different page about Bob."))
        await b.close()
        return res

    a, b = run(go())
    assert [p.name for p in a] == ["Ann Lee"] and b == []  # Ann is not on page 2, so page 2 may not claim her


def test_the_batch_budget_splits_calls_and_nothing_is_lost():
    names = ["Alice Adams", "Brian Baker", "Carla Chen", "Derek Diaz", "Elena Evans", "Felix Ford"]
    fake = Fake({f"s{i}.com": [P(n, "CEO")] for i, n in enumerate(names)})
    texts = {f"s{i}.com": f"{n} is CEO. " + "x " * 1000 for i, n in enumerate(names)}  # ~2,000 chars each

    async def go():
        b = lq.PeopleBatcher(call=fake, batch_chars=5000)
        res = await asyncio.gather(*[b.submit(d, "u", t) for d, t in texts.items()])
        await b.close()
        return res, b

    res, b = run(go())
    assert len(fake.calls) >= 3 and b.items == 6
    assert [p[0].name for p in res] == names


def test_only_one_model_call_runs_at_a_time_however_many_pages_are_queued():
    fake = Fake({}, delay=0.02)

    async def go():
        b = lq.PeopleBatcher(call=fake, batch_chars=300, max_wait_s=0.0)
        await asyncio.gather(*[b.submit(f"s{i}.com", "u", "y " * 200) for i in range(12)])
        await b.close()
        return b

    b = run(go())
    assert b.max_active == 1 and b.calls >= 4


def test_the_queue_is_bounded_so_producers_wait_instead_of_piling_up_memory():
    fake = Fake({}, delay=0.05)

    async def go():
        b = lq.PeopleBatcher(call=fake, queue_size=2, batch_chars=100, max_wait_s=0.0)
        done = []

        async def producer(i):
            await b.submit(f"s{i}.com", "u", "z " * 60)
            done.append(i)

        tasks = [asyncio.create_task(producer(i)) for i in range(8)]
        await asyncio.sleep(0.01)
        depth = b._q.qsize()
        await asyncio.gather(*tasks)
        await b.close()
        return depth, done

    depth, done = run(go())
    assert depth <= 2 and sorted(done) == list(range(8))


def test_a_failed_call_fails_its_pages_and_two_in_a_row_marks_the_model_down():
    fake = Fake(fail=99)

    async def go():
        b = lq.PeopleBatcher(call=fake, max_wait_s=0.0, batch_chars=50)
        outs = []
        for i in range(4):
            try:
                outs.append(await b.submit(f"s{i}.com", "u", "t " * 40))
            except llm.LlmError as e:
                outs.append(str(e))
        await b.close()
        return outs, b

    outs, b = run(go())
    assert b.down and all(isinstance(o, str) for o in outs)
    assert len(fake.calls) == 2  # after two failures it stops calling the model
    assert "stopped responding" in outs[-1]


def test_a_success_resets_the_failure_streak():
    fake = Fake(fail=1)

    async def go():
        b = lq.PeopleBatcher(call=fake, max_wait_s=0.0, batch_chars=50)
        r1 = None
        try:
            await b.submit("a.com", "u", "t " * 40)
        except llm.LlmError:
            r1 = "failed"
        r2 = await b.submit("b.com", "u", "t " * 40)
        r3 = await b.submit("c.com", "u", "t " * 40)
        await b.close()
        return r1, r2, r3, b

    r1, r2, r3, b = run(go())
    assert r1 == "failed" and r2 == [] and r3 == [] and not b.down


def test_snippets_are_cut_around_job_titles_not_the_navigation():
    text = "Home About Contact " * 400 + "Our CEO Priya Sharma leads the team. " + "filler " * 800
    s = lq.snippet_for_llm(text, 1000)
    assert len(s) <= 1000 and "Priya Sharma" in s
    assert lq.snippet_for_llm("short text", 1000) == "short text"


def test_the_default_caller_uses_the_batch_prompt_and_the_budgeted_input(monkeypatch):
    seen = {}

    def fake_generate(schema, system, user, **kw):
        seen.update(schema=schema, system=system, user=user, kw=kw)
        return lq.BatchPeople(pages=[lq.PagePeople(page=1, people=[Person(name="Ann Lee", role="CEO")])])

    monkeypatch.setattr(llm, "generate_json", fake_generate)

    async def go():
        b = lq.PeopleBatcher()
        r = await b.submit("a.com", "u", "Ann Lee, CEO")
        await b.close()
        return r

    r = run(go())
    assert [p.name for p in r] == ["Ann Lee"] and seen["schema"] is lq.BatchPeople
    assert "### PAGE 1" in seen["user"] and seen["kw"]["max_chars"] > lq.BATCH_CHARS and seen["kw"]["num_predict"] <= 1400


def test_a_contact_or_about_page_is_not_a_team_page_unless_the_text_says_so():
    assert not an.team_block("https://a.com/contact", "Write to us. We are in Pune.")
    assert not an.team_block("https://a.com/about", "We build robots.")
    assert an.team_block("https://a.com/team", "")
    assert an.team_block("https://a.com/leadership", "")
    assert an.team_block("https://a.com/about", "Founder Jane. CEO Bob. Partner Al. Chief Ann.")


def test_an_address_declared_for_a_person_in_structured_data_becomes_a_lead_with_that_person():
    html = '<html><head><script type="application/ld+json">{"@type":"Person","name":"Priya Sharma","jobTitle":"CEO","email":"priya@acme.ai"}</script></head><body>Team</body></html>'
    d = an.finalize_page(A(html)).drafts[0]
    assert (d.email, d.name, d.role, d.email_confidence) == ("priya@acme.ai", "Priya Sharma", "CEO", EmailConfidence.published_personal)
    assert "structured data" in dict(d.trace)["label"]


def test_structured_addresses_on_other_domains_are_ignored():
    html = '<script type="application/ld+json">{"@type":"Person","name":"Priya Sharma","email":"priya@gmail.com"}</script><body>x</body>'
    assert an.finalize_page(A(html)).drafts == []
