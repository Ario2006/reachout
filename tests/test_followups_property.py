"""Property test for follow-ups: under random orders of replies, bounces, opt-outs, suppressions, time passing, sender
ticks and repeated follow-up ticks, nobody gets more than 1 + len(days) emails, nobody gets a follow-up after a blocking
event, and no kind is ever sent twice. Fakes only."""
import itertools

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st
from sqlalchemy import text

from backend.core import followups, services, tz
from tests import factories
from tests.sending_world import fresh, make_campaign, sender, suppress

pytestmark = pytest.mark.slow
PROP = hsettings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
_example = itertools.count(1)
N = 3

op = st.one_of(
    st.just(("fu",)), st.just(("fu",)), st.just(("send",)), st.just(("send",)),
    st.tuples(st.just("hours"), st.integers(1, 80)),
    st.tuples(st.sampled_from(["replied", "bounced", "opted_out", "auto_reply", "closed", "suppress"]), st.integers(0, N - 1)),
)


@pytest.fixture
def world(engine, memory_keyring):
    svc, _ = fresh(engine)
    yield engine, svc
    services.use(None)


@PROP
@given(ops=st.lists(op, min_size=1, max_size=40), days=st.sampled_from([[1, 2], [1], [2, 3], [1, 1]]), approval=st.booleans(), seed=st.integers(0, 10**6))
def test_property_followups_respect_limits_blocks_and_uniqueness(world, ops, days, approval, seed):
    engine, svc = world
    n = next(_example)
    svc.fake_clock.t = tz.local_day_start(1_800_000_000.0) + 12 * 3600 + n * 90 * 86400
    with engine.begin() as c:  # earlier examples must not keep sending in this one
        c.execute(text("UPDATE campaigns SET state='draft'"))
    tid = factories.template(engine, followup_1_body="Hi {{first_name}}, following up.", followup_2_body="Last note, {{first_name}}.")
    cid, leads = make_campaign(engine, N, domains=[f"d{n}x{i}.test" for i in range(N)], template_id=tid, followups_enabled=1,
                               followup_days_json=str(days), followups_require_approval=int(approval), delay_min_s=60, delay_max_s=180)
    emails = [f"p{i}@d{n}x{i}.test" for i in range(N)]
    snd = sender(engine, seed=seed)
    blocked_at: dict[int, int] = {}  # lead index -> emails already sent when the first blocking event hit

    def sent_to(i):
        return [e for e in svc.fake_mailer.sent if e.to_addr == emails[i]]

    def approve_all():
        with engine.begin() as c:  # the user approving whatever waits in the follow-up review queue
            c.execute(text("UPDATE drafts SET status='approved', approved=1, approved_at=datetime('now') "
                           "WHERE campaign_id=:c AND kind != 'initial' AND status='pending' "
                           "AND lead_id IN (SELECT id FROM leads WHERE status NOT IN ('replied','bounced','opted_out','auto_reply','closed'))"), {"c": cid})

    def step(o):
        if o[0] == "fu":
            followups.tick(engine)
        elif o[0] == "send":
            for _ in range(4):
                snd.tick()
                svc.fake_clock.advance(200)
            if approval:
                approve_all()
        elif o[0] == "hours":
            svc.fake_clock.advance(o[1] * 3600)
        else:
            i = o[1]
            blocked_at.setdefault(i, len(sent_to(i)))
            if o[0] == "suppress":
                suppress(engine, emails[i])
            else:
                with engine.begin() as c:
                    c.execute(text("UPDATE leads SET status=:s WHERE id=:i"), {"s": o[0], "i": leads[i]})
                if o[0] in ("bounced", "opted_out"):
                    suppress(engine, emails[i])

    for o in ops:
        step(o)
        assert all(len(sent_to(i)) <= 1 + len(days) for i in range(N))
        for i, k in blocked_at.items():
            assert len(sent_to(i)) == k, f"lead {i} was emailed after a blocking event"
    for _ in range(12):  # let everything that is still allowed finish
        for o in (("hours", 30), ("fu",), ("send",), ("fu",), ("fu",), ("send",)):
            step(o)

    with engine.connect() as c:
        per_kind = c.execute(text("SELECT lead_id, kind, COUNT(*) FROM messages WHERE campaign_id=:c AND state IN ('sent','sending','needs_confirmation') GROUP BY lead_id, kind"), {"c": cid}).all()
        sent_rows = c.execute(text("SELECT lead_id, kind, message_id, in_reply_to, references_text FROM messages WHERE campaign_id=:c AND state='sent' ORDER BY claimed_at"), {"c": cid}).all()
    assert all(cnt == 1 for _, _, cnt in per_kind)
    for i in range(N):
        mine = [r for r in sent_rows if r[0] == leads[i]]
        assert len(sent_to(i)) == len(mine) <= 1 + len(days)  # nothing left the building that is not recorded
        assert len({e.message_id for e in sent_to(i)}) == len(mine)
        ids: list[str] = []
        for _, kind, mid, irt, refs in mine:
            if kind != "initial":
                assert irt == ids[-1] and refs == " ".join(ids), "follow-up not threaded on every earlier email"
            ids.append(mid)
        assert [r[1] for r in mine] == ["initial", "followup_1", "followup_2"][: len(mine)], "kinds out of order"
        if i not in blocked_at and not approval:
            assert len(mine) == 1 + len(days), f"lead {i} never got all its follow-ups ({[r[1] for r in mine]})"
