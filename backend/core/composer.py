"""Templates, profile fields and rendering (milestone 3).

Templates are Jinja2 run in a sandbox, autoescape off (the output is a plain-text email). Unknown or empty
variables never abort a render: they are reported as `missing` so the draft can be flagged. The LLM-written opener
is wrapped in private marker characters while rendering so its exact position in the final body is known."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import jinja2
from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import Engine, text

from backend.core import db, footer

RESERVED = ("first_name", "company", "role", "my_name", "custom_opener")
OPEN, CLOSE = "", ""
SAMPLE_LEAD = {"name": "Alex Rivera", "company": "Acme", "role": "Founder", "mode": None}
STARTER_PROFILE = (
    ("my_summary", "About me (one or two sentences)"),
    ("my_pitch", "What I offer or am building"),
    ("my_traction", "Traction or proof"),
)

_env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=False, undefined=jinja2.Undefined)


class TemplateError(ValueError):
    """The template does not parse or cannot render."""


class TemplateInUse(Exception):
    """A campaign still uses the template."""


class ProfileError(ValueError):
    pass


STARTERS = [
    dict(mode="internship", name="Internship: short and direct", subject="Internship at {{company}}?",
         body=("Hi {{first_name}},\n\n{{custom_opener}}\n\nI'm {{my_name}}. {{my_summary}}\n\n"
               "I'd love to contribute as an intern on your team. Would you be open to a 15-minute chat, "
               "or could you point me to the right person to ask?\n\nThanks for reading,\n{{my_name}}"),
         followup_1_body=("Hi {{first_name}},\n\nJust a short follow-up on my note about an internship at {{company}}. "
                          "If now is not a good time, no problem at all.\n\nThanks,\n{{my_name}}"),
         followup_2_body=("Hi {{first_name}},\n\nLast note from me: I'm still keen on {{company}}. "
                          "If there is someone else I should write to, I'd be grateful for a pointer.\n\n{{my_name}}")),
    dict(mode="investor", name="Investor: traction first", subject="{{company}} and what we're building",
         body=("Hi {{first_name}},\n\n{{custom_opener}}\n\nI'm {{my_name}}. {{my_pitch}} {{my_traction}}\n\n"
               "Would you be open to a 20-minute call in the next couple of weeks?\n\nBest,\n{{my_name}}"),
         followup_1_body=("Hi {{first_name}},\n\nA quick follow-up on my note below. Happy to keep it to 20 minutes "
                          "whenever suits you.\n\nBest,\n{{my_name}}"),
         followup_2_body=("Hi {{first_name}},\n\nI'll leave it here so I don't clutter your inbox. "
                          "If the timing changes, I'd be glad to talk.\n\n{{my_name}}")),
    dict(mode="service", name="Service: one concrete offer", subject="An idea for {{company}}",
         body=("Hi {{first_name}},\n\n{{custom_opener}}\n\n{{my_pitch}}\n\n"
               "If this is relevant to {{company}}, could I send over a short example of what I would do for you?\n\n"
               "Best,\n{{my_name}}"),
         followup_1_body=("Hi {{first_name}},\n\nFollowing up on my note about {{company}}. "
                          "Glad to send the short example if it is useful.\n\nBest,\n{{my_name}}"),
         followup_2_body=("Hi {{first_name}},\n\nLast message from me on this. "
                          "If it is ever useful, just reply and I'll send it over.\n\n{{my_name}}")),
]


# ----------------------------------------------------------------------------- jinja helpers
def referenced(*sources: str | None) -> list[str]:
    """Variables the sources use, reserved ones first (in a fixed order), then the rest alphabetically."""
    names: set[str] = set()
    for s in sources:
        if s:
            try:
                names |= meta.find_undeclared_variables(_env.parse(s))
            except jinja2.TemplateSyntaxError as e:
                raise TemplateError(_syntax_message(e)) from e
    return [r for r in RESERVED if r in names] + sorted(names - set(RESERVED))


def _syntax_message(e: jinja2.TemplateSyntaxError) -> str:
    return f"Template error on line {e.lineno}: {e.message}"


def validate(t: dict) -> None:
    """Raises TemplateError if any part of the template does not parse."""
    referenced(t.get("subject"), t.get("body"), t.get("followup_1_body"), t.get("followup_2_body"))


def _clean_value(v: object) -> str:
    return str(v or "").replace(OPEN, "").replace(CLOSE, "").strip()


def lead_values(lead: dict, settings, profile: dict[str, str]) -> dict[str, str]:
    name = _clean_value(lead.get("name"))
    first = name.split()[0].strip(",") if name else ""
    values = {k: _clean_value(v) for k, v in profile.items()}
    values.update(first_name=first, company=_clean_value(lead.get("company")), role=_clean_value(lead.get("role")),
                  my_name=_clean_value(settings.display_name))
    return values


@dataclass
class Rendered:
    subject: str
    body: str
    opener_span: tuple[int, int] | None
    missing: list[str] = field(default_factory=list)


def render(template: dict, values: dict[str, str], opener: str) -> Rendered:
    """Render subject and body. `values` holds first_name, company, role, my_name and the profile keys."""
    used = referenced(template["subject"], template["body"])
    missing = [n for n in used if n not in ("custom_opener", "first_name") and not values.get(n)]
    ctx = dict(values)
    ctx["first_name"] = values.get("first_name") or "there"
    opener = _clean_value(opener)
    try:
        subject = _env.from_string(template["subject"]).render(**{**ctx, "custom_opener": opener})
        body = _env.from_string(template["body"]).render(**{**ctx, "custom_opener": OPEN + opener + CLOSE})
    except Exception as e:  # noqa: BLE001 - user templates can raise anything (1/0, bad filter arguments, ...)
        raise TemplateError(f"The template could not be rendered: {e}") from e
    subject = re.sub(r"\s+", " ", subject).strip()
    body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", body)).strip()
    span = None
    if OPEN in body and CLOSE in body:
        start = body.index(OPEN)
        span = (start, body.index(CLOSE) - 1)
    body = body.replace(OPEN, "").replace(CLOSE, "")
    return Rendered(subject, body, span, missing)


# ----------------------------------------------------------------------------- templates
def _tpl_out(r) -> dict:
    d = dict(r._mapping)
    d["is_starter"] = bool(d["is_starter"])
    d["merge_fields"] = referenced(d["subject"], d["body"], d["followup_1_body"], d["followup_2_body"])
    return d


def seed_starters(engine: Engine) -> None:
    """First call only: the three starter templates and blank profile fields. Never re-seeded after deletion."""
    with engine.begin() as c:
        first = c.execute(text("INSERT OR IGNORE INTO app_state (key, value) VALUES ('compose_seeded', '1')")).rowcount
        if not first:
            return
        for t in STARTERS:
            c.execute(text("INSERT INTO templates (mode, name, subject, body, followup_1_body, followup_2_body, is_starter) "
                           "VALUES (:mode, :name, :subject, :body, :followup_1_body, :followup_2_body, 1)"), t)
        for i, (k, label) in enumerate(STARTER_PROFILE):
            c.execute(text("INSERT OR IGNORE INTO profile_fields (key, label, value, position) VALUES (:k, :l, '', :p)"),
                      {"k": k, "l": label, "p": i})


def list_templates(engine: Engine) -> list[dict]:
    seed_starters(engine)
    with engine.connect() as c:
        return [_tpl_out(r) for r in c.execute(text("SELECT * FROM templates ORDER BY is_starter DESC, id"))]


def get_template(template_id: int, engine: Engine) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text("SELECT * FROM templates WHERE id = :i"), {"i": template_id}).first()
    return _tpl_out(r) if r else None


_COLS = ("mode", "name", "subject", "body", "followup_1_body", "followup_2_body")


def create_template(data: dict, engine: Engine) -> dict:
    validate(data)
    seed_starters(engine)
    row = {k: data.get(k) for k in _COLS}
    with engine.begin() as c:
        tid = c.execute(text(f"INSERT INTO templates ({', '.join(_COLS)}) VALUES ({', '.join(':' + k for k in _COLS)})"), row).lastrowid
    return get_template(tid, engine)  # type: ignore[return-value]


def update_template(template_id: int, data: dict, engine: Engine) -> dict | None:
    validate(data)
    row = {k: data.get(k) for k in _COLS} | {"i": template_id}
    with engine.begin() as c:
        n = c.execute(text(f"UPDATE templates SET {', '.join(f'{k} = :{k}' for k in _COLS)}, updated_at = datetime('now') "
                           "WHERE id = :i"), row).rowcount
    return get_template(template_id, engine) if n else None


def delete_template(template_id: int, engine: Engine) -> bool:
    with engine.begin() as c:
        if c.execute(text("SELECT COUNT(*) FROM campaigns WHERE template_id = :i"), {"i": template_id}).scalar_one():
            raise TemplateInUse("A campaign uses this template, so it cannot be deleted.")
        return c.execute(text("DELETE FROM templates WHERE id = :i"), {"i": template_id}).rowcount > 0


# ----------------------------------------------------------------------------- profile
def get_profile(engine: Engine) -> dict:
    seed_starters(engine)
    with engine.connect() as c:
        rows = c.execute(text("SELECT key, label, value FROM profile_fields ORDER BY position, key")).all()
    return {"fields": [dict(key=r[0], label=r[1], value=r[2]) for r in rows], "reserved": list(RESERVED)}


def put_profile(fields: list[dict], engine: Engine) -> dict:
    seen: set[str] = set()
    for f in fields:
        if f["key"] in RESERVED:
            raise ProfileError(f"“{f['key']}” is filled in automatically and cannot be a profile field.")
        if f["key"] in seen:
            raise ProfileError(f"The key “{f['key']}” appears twice.")
        seen.add(f["key"])
    seed_starters(engine)
    with engine.begin() as c:
        c.execute(text("DELETE FROM profile_fields"))
        for i, f in enumerate(fields):
            c.execute(text("INSERT INTO profile_fields (key, label, value, position) VALUES (:key, :label, :value, :p)"),
                      {**f, "p": i})
    return get_profile(engine)


def profile_values(engine: Engine) -> dict[str, str]:
    with engine.connect() as c:
        return {r[0]: r[1] for r in c.execute(text("SELECT key, value FROM profile_fields"))}


# ----------------------------------------------------------------------------- preview
def sample_opener(company: str | None) -> str:
    return f"(A short opener written by the local model about {company or 'the company'} appears here.)"


def preview(template: dict, lead: dict | None, engine: Engine) -> dict:
    settings = db.load_settings(engine)
    lead = lead or SAMPLE_LEAD
    values = lead_values(lead, settings, profile_values(engine))
    r = render(template, values, sample_opener(values["company"]))
    mode = template.get("mode") or lead.get("mode")
    final = footer.ensure_footer(r.body, settings, mode)
    return dict(subject=r.subject, body=r.body, final_text=final,
                opener_span=None if r.opener_span is None else dict(start=r.opener_span[0], end=r.opener_span[1]),
                missing_fields=r.missing, word_count=len(r.body.split()))
