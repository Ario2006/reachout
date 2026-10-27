"""Small factories for tests. Insert straight into SQLite and return ids; no seed files."""
from __future__ import annotations

import itertools

from sqlalchemy import Engine, text

_n = itertools.count(1)


def lead(engine: Engine, email: str | None = None, **kw) -> int:
    i = next(_n)
    row = dict(name=f"Person {i}", email=email or f"person{i}@co{i}.test", role="Founder", company=f"Co{i}", domain=None,
               source_url="https://example.test/team", context_snippet="Co builds developer tools for teams.",
               email_confidence="published_personal", mode="internship", status="new")
    row.update(kw)
    row["email"] = row["email"].lower()
    row["domain"] = row["domain"] or row["email"].split("@")[1]
    with engine.begin() as c:
        return c.execute(text(f"INSERT INTO leads ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row).lastrowid


def template(engine: Engine, **kw) -> int:
    row = dict(mode="internship", name="T", subject="Hello {{first_name}}", body="Hi {{first_name}},\n\n{{custom_opener}}\n\nThanks,\n{{my_name}}")
    row.update(kw)
    with engine.begin() as c:
        return c.execute(text(f"INSERT INTO templates ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row).lastrowid


def campaign(engine: Engine, **kw) -> int:
    row = dict(name="C", state="draft", daily_cap=25, window_start="00:00", window_end="23:59", mode="internship")
    row.update(kw)
    with engine.begin() as c:
        return c.execute(text(f"INSERT INTO campaigns ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row).lastrowid


def draft(engine: Engine, campaign_id: int, lead_id: int, status: str = "pending", **kw) -> int:
    row = dict(campaign_id=campaign_id, lead_id=lead_id, kind="initial", subject="Hello", body="Hi,\n\nBody.",
               status=status, approved=int(status == "approved"))
    row.update(kw)
    with engine.begin() as c:
        return c.execute(text(f"INSERT INTO drafts ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})"), row).lastrowid
