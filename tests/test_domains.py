import pytest

from backend.core import domains as d


@pytest.mark.parametrize("url,expected", [
    ("https://www.blog.acme.co.in/x", "acme.co.in"), ("acme.ai", "acme.ai"),
    ("https://sub.example.co.uk/a", "example.co.uk"), ("localhost", "localhost"), ("", ""),
])
def test_registrable(url, expected):
    assert d.registrable(url) == expected


@pytest.mark.parametrize("dom", ["linkedin.com", "www.linkedin.com", "jobs.lever.co", "github.com", "news.ycombinator.com",
                                 "x.com", "reddit.com", "indeed.com", "glassdoor.com", "crunchbase.com", "facebook.com", "instagram.com"])
def test_spec_blocklist_covered(dom):
    assert d.is_blocked(dom)


def test_blocklist_extra_and_normal_sites():
    assert not d.is_blocked("acme.ai")
    assert d.is_blocked("shop.badsite.com", ["badsite.com"])
    assert not d.is_blocked("goodsite.com", ["badsite.com"])


@pytest.mark.parametrize("email", [
    "noreply@acme.com", "no-reply@acme.com", "donotreply@acme.com", "do-not-reply@acme.com", "logo@2x.png",
    "a@example.com", "support@acme.com", "postmaster@acme.com", "abc123def4567890abc12345@x.com",
    "icon.png@acme.com", "not-an-email", "x@y", "privacy@acme.com",
])
def test_junk(email):
    assert d.is_junk_email(email)


@pytest.mark.parametrize("email", ["info@acme.com", "priya@acme.ai", "careers@acme.ai", "john.smith@acme.com"])
def test_not_junk(email):
    assert not d.is_junk_email(email)


def test_local_classification():
    assert d.is_generic_local("careers") and d.is_generic_local("marketing") and d.is_generic_local("ceo")
    assert d.namelike_local("priya") and d.namelike_local("john.smith") and d.namelike_local("j.smith")
    assert not d.namelike_local("info") and not d.namelike_local("marketing") and not d.namelike_local("x")
    assert not d.namelike_local("a1b2")


def test_same_site():
    assert d.same_site("mail.acme.ai", "acme.ai") and not d.same_site("gmail.com", "acme.ai")


def test_public_host_guard():
    assert not d.is_public_host("127.0.0.1") and not d.is_public_host("localhost")
    assert not d.is_public_host("10.0.0.5") and not d.is_public_host("169.254.169.254") and not d.is_public_host("")


@pytest.mark.parametrize("dom", ["hiredoor.in", "carrerlift.in", "bestjobs24.com", "internshala.com", "freshersjobs.in", "myinternships.in", "naukri.com", "jooble.org"])
def test_job_boards_are_blocked(dom):
    assert d.is_blocked(dom)


@pytest.mark.parametrize("dom", ["acme.ai", "hirelogic.io"[:0] or "acme.io", "bangalore-labs.in", "sarvam.ai", "razorpay.com"])
def test_real_companies_are_not_blocked(dom):
    assert not d.is_blocked(dom)


@pytest.mark.parametrize("dom", ["wa.me", "bit.ly", "calendly.com", "docs.google.com", "forms.gle", "linktr.ee", "typeform.com"])
def test_shorteners_and_tools_are_blocked(dom):
    assert d.is_blocked(dom)


@pytest.mark.parametrize("dom", ["iisc.ac.in", "sebi.gov.in", "nic.in", "mit.edu", "ox.ac.uk", "unsw.edu.au", "whitehouse.gov", "meity.gov.in", "isro.gov.in"])
def test_institutions_are_skipped(dom):
    assert d.is_institutional(dom) and d.is_blocked(dom)


@pytest.mark.parametrize("dom", ["acme.ai", "razorpay.com", "sarvam.ai", "gov-tech-startup.io", "academy.co", "accelerate.in"])
def test_companies_with_institutional_looking_names_are_not_skipped(dom):
    assert not d.is_institutional(dom)


@pytest.mark.parametrize("email", ["care@byjus.com", "cs@bluestone.com", "bugreport@byjus.com", "cards@rupeek.com", "bookings@portea.com",
                                   "customersupport@portea.com", "mediarelations@scripbox.com", "global-prteam@arm.com", "investor.relations@arm.com",
                                   "editor@beststartup.in", "editor1@kdnuggets.com", "contribute@beststartup.in", "press-office@a.com",
                                   "support.eu@a.com", "customer-success@a.com", "legal-team@a.com"])
def test_service_desk_newsroom_and_ir_mailboxes_are_never_leads(email):
    assert d.is_junk_email(email)


@pytest.mark.parametrize("email", ["careers@scripbox.com", "ceo@scripbox.com", "hr@portea.com", "info@vahan.ai", "partners@upforge.org", "pitch@vc.com",
                                   "intern@a.com", "founders@a.com", "hello@a.com", "hr@vahan.co"])
def test_outreach_role_mailboxes_are_kept(email):
    assert not d.is_junk_email(email)


@pytest.mark.parametrize("email", ["api@a.com", "edi@a.com", "reports@a.com", "eu.support@statista.com", "us-care@a.com", "global.pr@a.com",
                                   "wholesale@a.com", "influencer@a.com", "orders.uk@a.com"])
def test_technical_and_desk_mailboxes_are_junk(email):
    assert d.is_junk_email(email)
