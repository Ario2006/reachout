"""Domain and email-address helpers shared by discovery, crawling, extraction and validation."""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit

from tld import get_fld

# Sites we never crawl: social networks, aggregators, job boards, news and reference sites,
# and code hosts (harvesting emails from GitHub is against its terms). Matched on the
# registrable domain, so subdomains are covered too.
BLOCKLIST: frozenset[str] = frozenset({
    # social
    "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "t.co", "reddit.com",
    "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "quora.com", "threads.net",
    "snapchat.com", "discord.com", "telegram.org", "t.me", "whatsapp.com", "medium.com",
    "substack.com", "tumblr.com", "wordpress.com", "blogspot.com", "wixsite.com",
    # link shorteners, chat/calendar/forms/payments and app stores: never a company's own site
    "wa.me", "bit.ly", "t.ly", "goo.gl", "tinyurl.com", "lnkd.in", "linktr.ee", "calendly.com", "forms.gle",
    "docs.google.com", "drive.google.com", "maps.google.com", "apps.apple.com", "itunes.apple.com", "paypal.com",
    "patreon.com", "buymeacoffee.com", "list-manage.com", "eventbrite.com", "meetup.com", "zoom.us",
    "gravatar.com", "wp.com", "wordpress.org", "creativecommons.org", "schema.org", "w3.org", "cloudflare.com",
    "typeform.com", "airtable.com", "notion.so", "notion.site", "canva.com", "figma.com", "behance.net",
    "dribbble.com", "vimeo.com", "spotify.com", "soundcloud.com", "flickr.com", "slack.com", "mailchi.mp",
    # code hosts (terms forbid harvesting emails for solicitation)
    "github.com", "githubusercontent.com", "github.io", "gitlab.com", "bitbucket.org",
    "stackoverflow.com", "stackexchange.com", "huggingface.co",
    # job boards and company directories
    "glassdoor.com", "glassdoor.co.in", "indeed.com", "naukri.com", "foundit.in", "monster.com",
    "ziprecruiter.com", "simplyhired.com", "wellfound.com", "angel.co", "internshala.com",
    "ambitionbox.com", "shine.com", "timesjobs.com", "hirist.tech", "cutshort.io", "instahyre.com",
    "lever.co", "greenhouse.io", "workable.com", "ashbyhq.com", "smartrecruiters.com",
    "crunchbase.com", "pitchbook.com", "tracxn.com", "zoominfo.com", "apollo.io", "rocketreach.co",
    "owler.com", "dnb.com", "clutch.co", "goodfirms.co", "g2.com", "capterra.com", "trustpilot.com",
    "jooble.org", "talent.com", "careerjet.co.in", "careerjet.com", "adzuna.in", "adzuna.com", "learn4good.com",
    "letsintern.com", "freshersworld.com", "apna.co", "workindia.in", "quikr.com", "olx.in", "jobsora.com",
    "jobrapido.com", "whatjobs.com", "hirect.in", "unstop.com", "dare2compete.com", "twenty19.com",
    "simplyhired.co.in", "myinternships.in", "internsala.com", "jobsforher.com", "iimjobs.com",
    "hirepro.in", "recruiter.com", "careerbuilder.com", "jobstreet.com", "seek.com.au", "reed.co.uk",
    "totaljobs.com", "stepstone.com", "jora.com", "jobbank.gc.ca", "6figr.com", "levels.fyi", "payscale.com",
    "designrush.com", "sortlist.com", "themanifest.com", "topdevelopers.co", "indiamart.com", "tradeindia.com",
    "yellowpages.com", "mouthshut.com", "startupindia.gov.in", "entrepreneur.com", "fundsforngos.org",
    "angellist.co", "seedtable.com", "failory.com", "startupstash.com", "alternativeto.net", "slashdot.org",
    "producthunt.com", "ycombinator.com", "crunchbase.co", "f6s.com", "betalist.com", "angellist.com",
    # news and reference
    "wikipedia.org", "wikimedia.org", "techcrunch.com", "forbes.com", "bloomberg.com", "reuters.com",
    "cnbc.com", "businessinsider.com", "economictimes.com", "indiatimes.com", "yourstory.com",
    "inc42.com", "entrackr.com", "medianama.com", "livemint.com", "moneycontrol.com",
    "thehindu.com", "hindustantimes.com", "ndtv.com", "business-standard.com", "theverge.com",
    "wired.com", "venturebeat.com", "techinasia.com", "e27.co", "dealstreetasia.com", "vccircle.com",
    "nytimes.com", "wsj.com", "ft.com", "bbc.com", "bbc.co.uk", "theguardian.com", "cnn.com",
    "hackernoon.com", "dev.to", "news.ycombinator.com", "slideshare.net", "scribd.com",
    # big platforms, search engines, app stores
    "google.com", "bing.com", "duckduckgo.com", "yahoo.com", "amazon.com", "amazon.in", "apple.com",
    "microsoft.com", "play.google.com", "flipkart.com", "justdial.com", "sulekha.com", "yelp.com",
    "tripadvisor.com", "zomato.com", "swiggy.com", "archive.org", "web.archive.org", "pdfcoffee.com",
})

# Addresses that are real but useless or risky to cold-email: never kept.
EXCLUDED_LOCALPARTS: frozenset[str] = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply", "mailer-daemon",
    "postmaster", "webmaster", "abuse", "privacy", "dpo", "legal", "security", "billing", "invoice",
    "invoices", "accounts", "unsubscribe", "bounce", "bounces", "root", "hostmaster", "support",
    "help", "helpdesk", "customercare", "customer-care", "orders", "returns", "refunds", "press",
    "media", "notifications", "alerts", "newsletter", "feedback", "complaints", "grievance",
    "compliance", "copyright", "dmca", "payments", "payment", "pay", "refund", "careers-noreply",
    "care", "cs", "bugreport", "bugs", "bug", "cards", "booking", "bookings", "customersupport", "customerservice",
    "customerfeedback", "claims", "claim", "complaint", "editor", "editors", "editor1", "editorial", "newsroom",
    "contribute", "contributors", "submit", "writeforus", "guestpost", "advertise", "advertising", "ads", "adsales",
    "api", "edi", "reports", "report", "order", "shipping", "wholesale", "influencer",
    "influencers", "affiliate", "affiliates", "subscribe", "subscriptions", "membership", "members", "wordpress", "example", "email", "name", "user", "username",
    "yourname", "your-name", "you", "test", "demo", "sample",
})

# Role-style addresses that are the right kind of target for outreach.
GENERIC_LOCALPARTS: frozenset[str] = frozenset({
    "info", "contact", "hello", "hi", "hey", "team", "careers", "career", "jobs", "job", "hr",
    "hiring", "recruit", "recruiting", "recruitment", "talent", "people", "work", "join", "internships",
    "intern", "interns", "pitch", "pitches", "founders", "founder", "invest", "investors",
    "partnerships", "partners", "partner", "business", "enquiries", "enquiry", "inquiries", "inquiry",
    "office", "mail", "admin", "sales", "connect", "reachout", "say", "hola", "general", "studio",
    "apply", "applications", "startups", "deals", "dealflow", "submissions",
    # department and title mailboxes
    "marketing", "engineering", "design", "product", "finance", "ops", "operations", "growth",
    "community", "content", "social", "events", "tech", "dev", "developer", "developers", "research",
    "ir", "procurement", "vendor", "vendors",
    "news", "store", "shop", "service", "services", "client",
    "clients", "enterprise", "education", "edu", "admissions", "student", "students", "alumni",
    "campus", "placement", "placements", "ceo", "cto", "coo", "cfo", "cmo", "md", "director",
    "president", "management", "leadership", "founders-office", "outreach", "pr", "brand",
    "creative", "agency", "consulting", "projects", "project", "new", "newbusiness",
})

_TWO_PART_TLDS = re.compile(r"\.(co|com|org|net|ac|gov|edu)\.[a-z]{2}$")
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".woff", ".woff2")
_PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com", "yoursite.com",
    "email.com", "test.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com", "localhost",
})


def host_of(url: str) -> str:
    """Lowercase hostname of a URL ("" when it has none)."""
    try:
        return (urlsplit(url if "//" in url else "//" + url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def registrable(host_or_url: str) -> str:
    """example.co.in for www.blog.example.co.in. Falls back to the host itself."""
    host = host_of(host_or_url)
    if not host:
        return ""
    try:
        return get_fld(host, fix_protocol=True, fail_silently=False).lower()
    except Exception:  # noqa: BLE001 - tld raises several error types for odd hosts
        return host


_JOB_BOARD_WORDS = ("job", "jobs", "career", "careers", "intern", "internship", "internships", "hire", "hiring",
                    "recruit", "recruiter", "vacancy", "vacancies", "resume", "naukri", "freshers", "placement")


def looks_like_job_board(domain: str) -> bool:
    """A domain whose name says it is a job site (e.g. hiredoor, carrerlift, bestjobs24)."""
    label = registrable(domain).split(".")[0].replace("-", "")
    return any(w in label for w in _JOB_BOARD_WORDS) or label.startswith(("carrer", "hired", "jobi"))


_INSTITUTIONAL = re.compile(r"\.(gov|edu|mil|int)(\.[a-z]{2})?$|\.(ac|nic|res|edu|gov)\.[a-z]{2}$|^(nic|gov)\.in$")


def is_institutional(domain: str) -> bool:
    """Government, university and similar domains: not startups, investors or brands."""
    return bool(_INSTITUTIONAL.search(registrable(domain)))


def is_blocked(domain: str, extra: list[str] | None = None) -> bool:
    d = registrable(domain)
    if d in BLOCKLIST or looks_like_job_board(d) or is_institutional(d):
        return True
    for e in extra or []:
        e = e.lower()
        if d == e or d.endswith("." + e) or host_of(domain) == e or host_of(domain).endswith("." + e):
            return True
    return False


def is_public_host(host: str) -> bool:
    """False for localhost, private, loopback and link-local addresses (SSRF guard)."""
    if not host or host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def split_email(email: str) -> tuple[str, str]:
    local, _, domain = email.strip().lower().rpartition("@")
    return local, domain


def looks_like_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))


# Anything that starts like this is a service desk, newsroom or investor-relations mailbox.
_EXCLUDED_PREFIXES = ("media", "press", "mediarelations", "investor", "ir.", "ir-", "grievance", "customer", "support", "helpdesk",
                      "help-", "help.", "global-pr", "pr-", "pr.", "prteam", "legal", "privacy", "complaint")


# A dotted/dashed local part with one of these words ("eu.support", "global-pr") is a desk, not a person.
_DESK_TOKENS = frozenset({"support", "help", "helpdesk", "care", "billing", "customer", "orders", "returns", "press", "media",
                          "legal", "privacy", "grievance", "pr", "ir", "noreply", "donotreply", "bounce", "abuse"})


def is_junk_email(email: str) -> bool:
    """Image-like, placeholder and automated addresses that must never become leads."""
    email = email.strip().lower()
    if not looks_like_email(email) or email.endswith(_IMAGE_SUFFIXES):
        return True
    local, domain = split_email(email)
    if local.endswith(_IMAGE_SUFFIXES):
        return True
    if domain in _PLACEHOLDER_DOMAINS or domain.endswith(_IMAGE_SUFFIXES):
        return True
    if re.search(r"@\d+x\.", email) or re.search(r"[0-9a-f]{24,}", local):  # retina image names, hashes
        return True
    base = local.split("+")[0]
    if base.startswith(_EXCLUDED_PREFIXES) or any(tok in _DESK_TOKENS for tok in re.split(r"[._\-]", base)):
        return True
    return base in EXCLUDED_LOCALPARTS or local in EXCLUDED_LOCALPARTS or local.startswith(
        ("noreply", "no-reply", "donotreply", "do-not-reply")
    )


def is_generic_local(local: str) -> bool:
    return local.split("+")[0] in GENERIC_LOCALPARTS


_NAMELIKE = re.compile(r"^[a-z]+([._\-][a-z]+){0,2}$")


def namelike_local(local: str) -> bool:
    """john, john.smith, j.smith, john_smith: plausibly a person, not a role address."""
    parts = re.split(r"[._\-]", local)
    return (bool(_NAMELIKE.match(local)) and len(local) >= 3 and any(len(p) >= 2 for p in parts)
            and not is_generic_local(local) and local not in EXCLUDED_LOCALPARTS)


def same_site(email_domain: str, site_domain: str) -> bool:
    """The address belongs to the site we crawled (same registrable domain)."""
    return registrable(email_domain) == registrable(site_domain)


# Throwaway-mailbox providers: a lead here is never a real company contact.
DISPOSABLE = frozenset({
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "sharklasers.com", "10minutemail.com",
    "10minutemail.net", "tempmail.com", "temp-mail.org", "temp-mail.io", "yopmail.com", "yopmail.net", "trashmail.com",
    "throwawaymail.com", "getnada.com", "maildrop.cc", "dispostable.com", "fakeinbox.com", "mailnesia.com", "mintemail.com",
    "mytemp.email", "tempinbox.com", "emailondeck.com", "spamgourmet.com", "mohmal.com", "tempr.email", "discard.email",
    "burnermail.io", "inboxkitten.com", "mailcatch.com", "moakt.com", "mail.tm", "tmpmail.org", "tempail.com", "emailfake.com",
    "grr.la", "spam4.me", "mailforspam.com", "trbvm.com", "armyspy.com", "cuvox.de", "dayrep.com", "einrot.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com", "superrito.com", "teleworm.us",
})


def is_disposable(domain: str) -> bool:
    d = domain.strip().lower()
    return d in DISPOSABLE or registrable(d) in DISPOSABLE
