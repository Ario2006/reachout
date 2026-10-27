from pathlib import Path

import pytest

from backend.core import extractor as x
from backend.core.extractor import Page
from backend.core.models import AppSettings, EmailConfidence

HTML = (Path(__file__).parent / "fixtures" / "team.html").read_text()
PEOPLE = [x.Person(name="Priya Sharma", role="Co-founder & CEO"), x.Person(name="Rahul Verma", role="CTO"),
          x.Person(name="Ana Li", role="Head of Design"), x.Person(name="Made Up Person", role="Wizard")]


def fake_people(text, domain):
    return PEOPLE


def test_deobfuscate_forms():
    assert x.deobfuscate("name [at] acme [dot] com") == "name@acme.com"
    assert x.deobfuscate("name(at)acme(dot)co(dot)in") == "name@acme.co.in"
    assert x.deobfuscate("name {at} acme.com") == "name@acme.com"
    assert x.deobfuscate("write to jane at example dot org today") == "write to jane@example.org today"
    assert x.deobfuscate("meet me at noon") == "meet me at noon"  # ordinary prose is untouched


def test_cloudflare_decode_roundtrip():
    enc = "5a" + "".join(f"{ord(c) ^ 0x5a:02x}" for c in "founders@acme.ai")
    assert x.decode_cfemail(enc) == "founders@acme.ai"
    assert x.decode_cfemail("zz") is None and x.decode_cfemail("") is None


def test_extract_emails_all_sources():
    got = {h.email: h.via for h in x.extract_emails(HTML)}
    assert got["priya@acme.ai"] == "mailto"          # query string stripped from mailto:
    assert got["rahul@acme.ai"] == "text"            # [at]/[dot] obfuscation
    assert got["founders@acme.ai"] == "cfemail"      # Cloudflare email protection
    assert got["careers@acme.ai"] == "text"


def test_mailto_forms():
    html = '<a href="mailto:a@acme.com,b@acme.com">x</a><a href="MAILTO:C@Acme.com?subject=x&body=y">y</a><a href="mailto:%20d@acme.com">z</a>'
    assert sorted(h.email for h in x.extract_emails(html)) == ["a@acme.com", "b@acme.com", "c@acme.com", "d@acme.com"]


def test_page_extraction_labels_and_filters():
    r = x.extract_page(Page(url="https://acme.ai/team", html=HTML), {"acme.ai"}, AppSettings(), people_fn=fake_people, domain="acme.ai")
    by = {d.email: d for d in r.drafts}
    # junk, image-like, system and other-domain addresses never become leads
    for bad in ("noreply@acme.ai", "logo@2x.png", "press@acme.ai", "info@acme-other.io"):
        assert bad not in by
    p = by["priya@acme.ai"]
    assert (p.name, p.role, p.email_confidence) == ("Priya Sharma", "Co-founder & CEO", EmailConfidence.published_personal)
    assert by["rahul@acme.ai"].role == "CTO"
    assert by["careers@acme.ai"].email_confidence == EmailConfidence.published_generic
    assert by["founders@acme.ai"].email_confidence == EmailConfidence.published_generic
    assert p.company == "Acme AI" and p.source_url == "https://acme.ai/team" and "Priya" in p.context_snippet
    assert len(p.context_snippet) <= x.CONTEXT_CHARS
    assert r.llm_calls == 1


def test_invented_names_are_dropped():
    r = x.extract_page(Page(url="https://acme.ai/team", html=HTML), {"acme.ai"}, AppSettings(), people_fn=fake_people, domain="acme.ai")
    assert "Made Up Person" not in [p.name for p, _ in r.people]
    assert [p.name for p, _ in r.people] == ["Priya Sharma", "Rahul Verma", "Ana Li"]


def test_name_validation_rules():
    low = "meet priya sharma and dr. j. smith. contact us. team lead"
    assert x._valid_name("Priya Sharma", low) == "Priya Sharma"
    assert x._valid_name("Contact Us", low) is None
    assert x._valid_name("Priya", low) is None
    assert x._valid_name("priya sharma", low) is None  # must look like a capitalised name
    assert x._valid_name("Priya Sharma Ltd Pvt Extra Words", low) is None


def test_llm_not_called_without_email_or_team_structure():
    called = []
    html = "<html><body><h1>Welcome</h1><p>We sell shoes. Visit our store.</p></body></html>"
    r = x.extract_page(Page(url="https://shoes.com/", html=html), {"shoes.com"}, AppSettings(),
                       people_fn=lambda t, d: called.append(1) or [], domain="shoes.com")
    assert r.llm_calls == 0 and not called and r.drafts == []


def test_llm_not_called_for_generic_only_non_team_page():
    called = []
    html = '<html><body><a href="mailto:info@shoes.com">Contact us</a></body></html>'
    r = x.extract_page(Page(url="https://shoes.com/hello", html=html), {"shoes.com"}, AppSettings(),
                       people_fn=lambda t, d: called.append(1) or [], domain="shoes.com")
    assert not called and [d.email for d in r.drafts] == ["info@shoes.com"]


def test_llm_failure_degrades_gracefully_and_names_beside_emails_still_work():
    from backend.core import llm

    def boom(t, d):
        raise llm.LlmError("down")

    r = x.extract_page(Page(url="https://acme.ai/team", html=HTML), {"acme.ai"}, AppSettings(), people_fn=boom, domain="acme.ai")
    by = {d.email: d for d in r.drafts}
    assert {"priya@acme.ai", "careers@acme.ai"} <= set(by)
    # With the model down, "priya@" beside "Priya Sharma" is not proof enough: no name, generic label.
    assert by["priya@acme.ai"].name == "" and by["priya@acme.ai"].email_confidence == EmailConfidence.published_generic
    assert "Ana Li" not in [p.name for p, _ in r.people]  # no email, no model: not found


def test_personal_requires_a_name_on_the_page_that_matches_the_address():
    html = ('<p>Questions? Write to <a href="mailto:john.smith@acme.ai">john.smith@acme.ai</a> or payments@acme.ai '
            'or <a href="mailto:fluidmechanics.me@acme.ai">fluidmechanics.me@acme.ai</a></p>')
    r = x.extract_page(Page(url="https://acme.ai/x", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    by = {d.email: d for d in r.drafts}
    assert "payments@acme.ai" not in by  # payment mailboxes are never outreach targets
    for e in ("john.smith@acme.ai", "fluidmechanics.me@acme.ai"):
        assert by[e].email_confidence == EmailConfidence.published_generic and by[e].name == ""
        assert "no matching name" in by[e].notes


def test_name_beside_the_address_makes_it_personal_without_the_model():
    html = '<div><h3>Meet Priya Sharma</h3><p>Founder. <a href="mailto:priya.sharma@acme.ai">Email</a></p><p>Ravi Rao <a href="mailto:rrao@acme.ai">x</a></p></div>'
    r = x.extract_page(Page(url="https://acme.ai/x", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    by = {d.email: d for d in r.drafts}
    assert (by["priya.sharma@acme.ai"].name, by["priya.sharma@acme.ai"].email_confidence) == ("Priya Sharma", EmailConfidence.published_personal)
    assert by["rrao@acme.ai"].name == "Ravi Rao"


def test_a_stray_name_near_a_non_matching_address_is_not_used():
    html = '<p>Contact Us. Aryan Kumar built this site. <a href="mailto:zed.quinn@acme.ai">mail</a></p>'
    r = x.extract_page(Page(url="https://acme.ai/x", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    assert r.drafts[0].email_confidence == EmailConfidence.published_generic and r.drafts[0].name == ""


def test_company_comes_from_the_given_value_not_each_page_title():
    r = x.extract_page(Page(url="https://acme.ai/contact", html="<title>Contact us</title>" + HTML), {"acme.ai"}, AppSettings(),
                       people_fn=None, domain="acme.ai", company="Acme AI")
    assert {d.company for d in r.drafts} == {"Acme AI"}


@pytest.mark.parametrize("html,expected", [
    ("<title>About Us</title>", "Acme"), ("<title>Contact us | Acme</title>", "Acme"), ("<title>Careers at Cockroach Labs</title>", "Cockroach Labs"),
    ("<title>Home</title>", "Acme"), ("<title>Acme Robotics - Build faster</title>", "Acme Robotics"),
    ('<meta property="og:site_name" content="Acme Inc"><title>About us</title>', "Acme Inc"), ("", "Acme"),
])
def test_company_name_ignores_page_titles(html, expected):
    assert x.company_name(html, "acme.ai") == expected


def test_match_person_patterns():
    people = [x.Person(name="Priya Sharma"), x.Person(name="José Álvarez")]
    for local in ("priya", "sharma", "priya.sharma", "psharma", "p.sharma", "priya.s", "priyas", "sharma.priya", "priya_sharma", "priya-sharma", "priya+sales"):
        assert x.match_person(local, people) == people[0], local
    assert x.match_person("jose.alvarez", people) == people[1]
    assert x.match_person("somebody", people) is None


def test_guesses_only_for_target_roles_without_published_email_and_marked_guessed():
    r = x.extract_page(Page(url="https://acme.ai/team", html=HTML), {"acme.ai"}, AppSettings(), people_fn=fake_people, domain="acme.ai")
    g = x.make_guesses(r.people, r.drafts, "acme.ai", "Acme AI", lambda p: "https://acme.ai/team", ["design"])
    assert [(d.email, d.name) for d in g] == [("ana@acme.ai", "Ana Li")]  # pattern inferred from priya@ -> first@
    assert g[0].email_confidence == EmailConfidence.guessed and "not verified" in g[0].notes
    assert x.make_guesses(r.people, r.drafts, "acme.ai", "Acme AI", lambda p: "https://acme.ai/team", ["plumber"]) == []


def test_pattern_inference():
    mk = lambda email, name: x.LeadDraft(email=email, name=name, email_confidence=EmailConfidence.published_personal)
    assert x.infer_pattern([mk("jane.doe@a.com", "Jane Doe")]) == "first.last"
    assert x.infer_pattern([mk("jdoe@a.com", "Jane Doe")]) == "flast"
    assert x.infer_pattern([mk("jane@a.com", "Jane Doe")]) == "first"
    assert x.infer_pattern([]) is None


def test_guess_limit_per_site():
    people = [(x.Person(name=f"Person{i} Surname", role="CTO"), "") for i in range(10)]
    people = [(x.Person(name=n, role="CTO"), "") for n in ["Aaa Bbb", "Ccc Ddd", "Eee Fff", "Ggg Hhh", "Iii Jjj"]]
    assert len(x.make_guesses(people, [], "a.com", "A", lambda p: "https://a.com/team", ["cto"], limit=3)) == 3


def test_visible_text_strips_scripts_and_styles():
    t = x.visible_text("<body><script>var a='x@y.com'</script><style>p{}</style><p>Hello&nbsp;world</p></body>")
    assert "x@y.com" not in t and "Hello" in t and "world" in t


def test_company_name_sources():
    assert x.company_name('<meta property="og:site_name" content="Acme AI">', "acme.ai") == "Acme AI"
    assert x.company_name("<title>Acme Labs | Home</title>", "acme.ai") == "Acme Labs"
    assert x.company_name("<p>x</p>", "my-brand.co") == "My Brand"


@pytest.mark.parametrize("name", ["Frontend Developer Intern", "Moksha Retails", "Selah Digital", "Rocket Health", "Software Engineering Intern",
                                  "Machine Learning Engineer", "Bangalore Based Role", "Acme Technologies Pvt Ltd", "Remote Data Analyst"])
def test_job_titles_and_company_names_are_not_people(name):
    assert x._valid_name(name, name.lower()) is None


@pytest.mark.parametrize("name", ["Priya Sharma", "Rahul Verma", "Anne-Marie O'Neil", "José Álvarez", "Dev Patel", "Li Wei Chen"])
def test_real_names_pass(name):
    assert x._valid_name(name, name.lower()) == name


def test_job_pages_never_call_the_model_even_with_roles_and_emails():
    called = []
    html = ("<html><body><h1>Software Engineer Intern</h1><p>Our CEO and Founder and CTO and Partner say apply.</p>"
            '<a href="mailto:priya@acme.ai">priya@acme.ai</a></body></html>')
    for url in ("https://acme.ai/jobs/software-intern", "https://acme.ai/careers/openings", "https://acme.ai/internships/ai"):
        r = x.extract_page(Page(url=url, html=html), {"acme.ai"}, AppSettings(), people_fn=lambda t, d: called.append(1) or [], domain="acme.ai")
        assert [d.email for d in r.drafts] == ["priya@acme.ai"]  # the email is still kept
    assert not called


def test_team_detection_ignores_generic_job_words():
    jobs_text = "engineer manager recruiter talent analyst associate director engineer manager"
    assert not x.team_like("https://acme.ai/", jobs_text)
    assert x.team_like("https://acme.ai/team", "")
    assert x.team_like("https://acme.ai/", "Founder Jane. CEO Bob. Partner Al.")
    assert not x.team_like("https://acme.ai/careers/team-lead", "Founder CEO CTO Partner")


def test_people_prompt_is_capped_below_the_global_limit():
    seen = []
    html = "<html><body>" + "<p>Priya Sharma, Founder.</p>" * 2000 + "</body></html>"
    x.extract_page(Page(url="https://acme.ai/team", html=html), {"acme.ai"}, AppSettings(), people_fn=lambda t, d: seen.append(len(t)) or [], domain="acme.ai")
    assert seen and seen[0] <= x.PEOPLE_PROMPT_CHARS < 12_000


def test_mailto_context_reaches_a_name_in_a_sibling_heading():
    hits = x.extract_emails('<div><h3>Meet Priya Sharma</h3><p>Founder. <a href="mailto:priya.sharma@acme.ai">Email</a></p></div>')
    assert "Priya Sharma" in hits[0].context


def test_long_container_context_is_windowed_around_the_link():
    filler = "lorem ipsum " * 80
    html = f'<div>{filler}<b>Jane Doe</b> <a href="mailto:jane@acme.ai">jane@acme.ai</a>{filler}</div>'
    ctx = x.extract_emails(html)[0].context
    assert len(ctx) <= x.CONTEXT_CHARS and "Jane Doe" in ctx


@pytest.mark.parametrize("role,ok", [
    ("Co-founder & CEO", True), ("Founder", True), ("Head of Engineering", True), ("Managing Partner", True), ("Group CEO and Co-founder", True),
    ("Founder & Chairman of the Board", True), ("Chief Technology Officer", True), ("CTO", True), ("Vice President, Product", True),
    ("Co-Founder & CEO of Cognition", False), ("Managing Director, Mayfield Fund", False), ("Co-Founder, Infosys, Chairman, Indiaspora", False),
    ("Director – Project Controls", False), ("Wizard", False), ("", False), ("Founder at OpenAI", False),
])
def test_role_must_be_a_plain_title_to_justify_a_guess(role, ok):
    assert x.role_is_title_only(role) is ok


@pytest.mark.parametrize("url,ok", [
    ("https://acme.ai/team", True), ("https://acme.ai/about", True), ("https://acme.ai/about-us/", True), ("https://acme.ai/our-team", True),
    ("https://acme.ai/leadership", True), ("https://acme.ai/people/", True), ("https://vc.com/investors", True),
    ("https://vc.com/portfolio", False), ("https://acme.ai/top-ai-startups-india/", False), ("https://acme.ai/blog/team-update", False),
    ("https://acme.ai/podcasts/guests", False), ("https://acme.ai/careers/team", False), ("https://acme.ai/", False),
    ("https://acme.ai/about/portfolio", False), ("https://acme.ai/startups/alpha", False),
])
def test_guesses_only_from_the_sites_own_team_pages(url, ok):
    assert x.guessable_source(url) is ok


def test_guess_skips_people_who_belong_to_other_organisations():
    cto = lambda name, role: (x.Person(name=name, role=role), "")  # noqa: E731
    people = [cto("Ann Lee", "CTO"), cto("Scott Wu", "Co-Founder & CEO of Cognition"), cto("Bo Chan", "CTO"), cto("Cy Dee", "CTO")]
    src = {"Ann Lee": "https://acme.ai/team", "Scott Wu": "https://acme.ai/team", "Bo Chan": "https://acme.ai/portfolio/alpha", "Cy Dee": "https://acme.ai/about"}
    g = x.make_guesses(people, [], "acme.ai", "Acme", lambda p: src[p.name], ["CTO", "founder"])
    assert [d.name for d in g] == ["Ann Lee", "Cy Dee"]


def test_portfolio_and_list_pages_do_not_call_the_model_for_people():
    called = []
    html = "<html><body><h1>Our portfolio founders</h1>" + "<p>Founder Jane Doe. CEO Bob Ray. Partner Al Ko.</p>" * 3 + "</body></html>"
    for url in ("https://vc.com/portfolio", "https://x.com/top-50-startups/", "https://x.com/news/funding"):
        x.extract_page(Page(url=url, html=html), {"vc.com", "x.com"}, AppSettings(), people_fn=lambda t, d: called.append(1) or [], domain="x.com")
    assert not called


def _d(email, conf=EmailConfidence.published_generic, role="", name=""):
    return x.LeadDraft(email=email, email_confidence=conf, role=role, name=name)


def test_select_best_prefers_personal_then_mode_relevant_role_addresses():
    drafts = [_d("sales@a.com"), _d("careers@a.com"), _d("info@a.com"), _d("g@a.com", EmailConfidence.guessed, "CTO", "G X"),
              _d("jane@a.com", EmailConfidence.published_personal, "CTO", "Jane D"), _d("press-office@a.com")]
    got = [d.email for d in x.select_best(drafts, "internship", 3, ["CTO"])]
    assert got == ["jane@a.com", "careers@a.com", "info@a.com"]
    assert [d.email for d in x.select_best(drafts, "service", 2)] == ["jane@a.com", "info@a.com"]
    inv = [_d("contact@v.com"), _d("pitch@v.com"), _d("sales@v.com")]
    assert [d.email for d in x.select_best(inv, "investor", 1)] == ["pitch@v.com"]


def test_select_best_trims_a_corporate_contact_page_to_a_handful():
    offices = [_d(f"info{c}@ibm.com") for c in "abcdefghijklmnopqrstuvwxyz"] + [_d("careers@ibm.com"), _d("jobs@ibm.com")]
    got = [d.email for d in x.select_best(offices, "internship", 5)]
    assert len(got) == 5 and got[:2] == ["careers@ibm.com", "jobs@ibm.com"]


def test_select_best_penalises_machine_looking_addresses_and_is_stable():
    drafts = [_d("ibm_customer_support_bulgaria@ibm.com"), _d("6557@il.ibm.com"), _d("hello@ibm.com")]
    assert x.select_best(drafts, "service", 1)[0].email == "hello@ibm.com"
    same = [_d(f"x{i}@a.com") for i in range(4)]
    assert [d.email for d in x.select_best(same, "service", 3)] == ["x0@a.com", "x1@a.com", "x2@a.com"]  # ties keep page order
    assert x.select_best([], "service", 5) == [] and len(x.select_best(same, "service", 99)) == 4


def test_role_words_next_to_an_address_do_not_become_a_name():
    html = '<p>Customer Support <a href="mailto:customersupport@acme.ai">x</a></p><p>For Bookings <a href="mailto:forbookings@acme.ai">y</a></p>'
    r = x.extract_page(Page(url="https://acme.ai/contact", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    by = {d.email: d for d in r.drafts}
    assert "customersupport@acme.ai" not in by  # a service-desk mailbox
    assert by["forbookings@acme.ai"].name == "" and by["forbookings@acme.ai"].email_confidence == EmailConfidence.published_generic


def test_a_title_fragment_is_not_part_of_a_name():
    html = '<p>Tanuj Choudhry Co-founder <a href="mailto:tanuj.choudhry@acme.ai">mail</a></p>'
    r = x.extract_page(Page(url="https://acme.ai/about", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    assert r.drafts[0].name == "Tanuj Choudhry"


@pytest.mark.parametrize("name", ["Co Founder", "Customer Support", "For Bookings", "Chief Executive", "Mr Kumar", "Head of Design", "Vice President Sales", "Head of Engineering"])
def test_titles_and_departments_are_not_names(name):
    assert x._valid_name(name, name.lower()) is None


@pytest.mark.parametrize("heading,email", [
    ("EDI Questions", "edi@acme.ai"), ("API Questions", "api@acme.ai"), ("Wholesale Opportunities", "wholesale@acme.ai"),
    ("Influencer Inquiries", "influencer@acme.ai"), ("Careers Team", "careers@acme.ai"), ("Press Office", "press@acme.ai"),
])
def test_headings_beside_an_address_are_never_people(heading, email):
    html = f'<p>{heading} <a href="mailto:{email}">{email}</a></p>'
    r = x.extract_page(Page(url="https://acme.ai/contact", html=html), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    assert all(d.name == "" and d.email_confidence != EmailConfidence.published_personal for d in r.drafts)


def test_without_the_model_a_single_matching_word_is_not_enough_but_the_full_name_is():
    one = '<p>Priya Sharma <a href="mailto:priya@acme.ai">mail</a></p>'
    full = '<p>Priya Sharma <a href="mailto:priya.sharma@acme.ai">mail</a> and R. Rao <a href="mailto:rrao@acme.ai">x</a></p>'
    r1 = x.extract_page(Page(url="https://acme.ai/team", html=one), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    assert r1.drafts[0].email_confidence == EmailConfidence.published_generic  # priya@ alone: no model to vouch for the name
    r2 = x.extract_page(Page(url="https://acme.ai/team", html=full), {"acme.ai"}, AppSettings(), people_fn=None, domain="acme.ai")
    assert {d.email: d.name for d in r2.drafts}["priya.sharma@acme.ai"] == "Priya Sharma"


def test_with_the_model_a_first_name_address_still_matches_a_listed_person():
    fn = lambda text, domain: [x.Person(name="Priya Sharma", role="CEO")]  # noqa: E731
    html = '<h1>Team</h1><p>Priya Sharma, CEO <a href="mailto:priya@acme.ai">mail</a></p>'
    r = x.extract_page(Page(url="https://acme.ai/team", html=html), {"acme.ai"}, AppSettings(), people_fn=fn, domain="acme.ai")
    assert (r.drafts[0].name, r.drafts[0].role, r.drafts[0].email_confidence) == ("Priya Sharma", "CEO", EmailConfidence.published_personal)
