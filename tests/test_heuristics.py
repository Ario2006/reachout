from pathlib import Path

import pytest

from backend.core import heuristics as h

FX = Path(__file__).parent / "fixtures"
read = lambda n: (FX / n).read_text()  # noqa: E731


def by_name(f): return {p.name: p for p in f.people}


def test_team_cards_yield_name_role_and_email_and_skip_non_people():
    f = h.structured_findings(read("team_cards.html"))
    got = by_name(f)
    assert set(got) == {"Priya Sharma", "Rahul Verma", "Ana Li"}  # the "Careers / Join us" card and the nav list are not people
    assert (got["Priya Sharma"].role, got["Priya Sharma"].email, got["Priya Sharma"].source) == ("Co-founder & CEO", "priya@acme.ai", "card")
    assert got["Rahul Verma"].email == "" and got["Ana Li"].email == "ana.li@acme.ai"


def test_jsonld_graph_organization_people_and_contact_emails():
    f = h.structured_findings(read("jsonld_team.html"))
    got = by_name(f)
    assert set(got) == {"Priya Sharma", "Rahul Verma", "Ana Li"}  # the article author and the broken name are ignored
    assert got["Priya Sharma"].role == "Founder" and got["Priya Sharma"].email == "priya@acme.ai"
    assert got["Rahul Verma"].role == "CTO" and got["Ana Li"].role == "Head of Design, Partner"
    assert f.org_name == "Acme AI"
    assert f.org_emails == ["contact@acme.ai", "sales@acme.ai"]  # press@ is a media desk, never an outreach target


def test_microdata_people():
    got = by_name(h.structured_findings(read("microdata_team.html")))
    assert got["José Álvarez"].role == "Managing Partner" and got["José Álvarez"].email == "jose@vc.fund" and got["José Álvarez"].source == "microdata"
    assert got["Mei Lin"].role == "Principal"


def test_heading_title_pairs_are_the_fallback_and_ignore_section_headings():
    got = by_name(h.structured_findings(read("heading_pairs.html")))
    assert set(got) == {"Dev Patel", "Sara Cohen"} and got["Sara Cohen"].role == "Chief Operating Officer"


def test_structured_data_wins_over_cards_when_present():
    html = read("jsonld_team.html").replace("<p>Team page</p>", "<div><p>x</p></div>")
    assert {p.source for p in h.structured_findings(html).people} == {"jsonld"}


def test_a_page_with_no_people_yields_nothing():
    assert h.structured_findings("<html><body><h1>Welcome</h1><p>We sell shoes.</p></body></html>").people == []
    assert h.structured_findings("").people == []


@pytest.mark.parametrize("fx", ["malformed.html", "hostile_jsonld.html", "spa_shell.html"])
def test_broken_hostile_or_empty_markup_never_raises(fx):
    f = h.structured_findings(read(fx))
    assert isinstance(f.people, list)


def test_deeply_nested_json_does_not_crash_or_leak_junk_names():
    f = h.structured_findings(read("hostile_jsonld.html"))
    assert "Aa" not in by_name(f)  # two letters is not a name


def test_huge_pages_stop_scanning():
    html = "<html><body>" + "<div><span>x</span></div>" * 20000 + "</body></html>"
    assert h.structured_findings(html).people == []  # finishes quickly (node budget) and finds nothing


@pytest.mark.parametrize("s,ok", [("CEO", True), ("Co-founder & CTO", True), ("Head of Marketing", True), ("Senior Software Engineer", True),
                                  ("Join us", False), ("Learn more", False), ("x" * 100, False), ("", False), ("Our values", False)])
def test_title_recognition(s, ok):
    assert h.looks_like_title(s) is ok
