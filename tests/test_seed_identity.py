"""The demo roster, and the constraint that keeps it synthetic.

`frontend/dashboard/src/demo.ts` used to draw NHS numbers in the browser. They
are rows now, which is better in every way but one: a column that holds an NHS
number is a column that *could* hold a real one. So the schema refuses to.

Two properties, and the second is the one that matters. Every seeded number is
in 999 000 0000 -- 999 999 9999, the range NHS England reserves for test data,
and passes Modulus 11 — the digits are well-formed as well as reserved, so
nothing downstream has to special-case them. And the CHECK is real: it is
re-derived here rather than trusted, because a constraint is precisely the kind
of thing a later migration drops by accident.

Deselected by default. `make test-pg` supplies TEST_DATABASE_URL.
"""

import asyncpg
import pytest

pytestmark = pytest.mark.postgres

ROSTER = 10


def check_digit(first_nine: str) -> int | None:
    """Modulus 11 over the first nine digits, weights 10 down to 2.

    `None` where the arithmetic yields 10, which is not a digit — such a number
    is simply not issuable, which is why a seed has to be checked rather than
    counted out by hand.
    """
    total = sum(int(d) * w for d, w in zip(first_nine, range(10, 1, -1), strict=True))
    remainder = 11 - (total % 11)
    if remainder == 10:
        return None
    return 0 if remainder == 11 else remainder


def test_the_check_digit_helper_agrees_with_a_known_number():
    """Otherwise the assertions below pass against a broken oracle."""
    assert check_digit("999000001") == 8
    # 11 - 0 == 11, the case that wraps to zero rather than to eleven.
    assert check_digit("999000010") == 7


async def test_the_roster_is_seeded(db):
    rows = await db.fetch(
        "select id, first_name, origin, clinician_email, nhs_number, date_of_birth "
        "from clinical.patients where nhs_number is not null order by id"
    )

    assert len(rows) == ROSTER
    for row in rows:
        assert row["first_name"]
        assert row["date_of_birth"] is not None
        # Unowned, which is what puts them in front of whichever clinician signs
        # in — `reads.OWNED_BY`. Dispatch is still the only thing that sets an
        # owner.
        assert row["origin"] == "demo"
        assert row["clinician_email"] is None


async def test_every_seeded_number_is_reserved_and_well_formed(db):
    numbers = [
        r["nhs_number"]
        for r in await db.fetch(
            "select nhs_number from clinical.patients where nhs_number is not null"
        )
    ]

    assert len(set(numbers)) == ROSTER
    for number in numbers:
        assert len(number) == 10
        assert number.startswith("999"), f"{number} is outside the test range"
        assert check_digit(number[:9]) == int(number[9]), f"{number} fails Modulus 11"


async def test_a_real_nhs_number_cannot_be_stored(db):
    """The guarantee this stage is actually making. Not a convention, not a
    comment — a deployment of this repo *cannot* hold a real identifier, and a
    clinical one drops one line to change that."""
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="nhs_number_is_test"):
        await db.execute(
            "insert into clinical.patients (id, first_name, nhs_number) "
            "values ('pt_real', 'Ada', '4010232137')"
        )


async def test_a_patient_may_have_no_number_at_all(db):
    """The intake asks for a first name. A row this product created itself is
    the normal case, not a broken one."""
    await db.execute("insert into clinical.patients (id, first_name) values ('pt_bare', 'Ada')")

    held = await db.fetchval("select nhs_number from clinical.patients where id = 'pt_bare'")
    assert held is None


async def test_two_patients_cannot_share_a_number(db):
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "insert into clinical.patients (id, first_name, nhs_number) "
            "values ('pt_dup', 'Ada', (select nhs_number from clinical.patients "
            "where nhs_number is not null limit 1))"
        )


async def test_the_experience_seed_covers_a_fortnight(db):
    """The chart draws fourteen bars at its widest, so fourteen days have to be
    there — and every response has to belong to somebody, because the read
    scopes through the patient."""
    # Excluding the rows `tests/test_reads.py` inserts to exercise the read: it
    # runs first, against the same scratch database, and its pool-backed writes
    # are not rolled back the way this connection's are.
    rows = await db.fetch(
        "select responded_at::date as day, count(*) as n "
        "from metrics.experience_responses where id not like 'xr_test_%' "
        "group by 1 order by 1"
    )

    assert len(rows) == 14
    assert all(r["n"] > 0 for r in rows)
    orphans = await db.fetchval(
        "select count(*) from metrics.experience_responses x "
        "left join clinical.patients p on p.id = x.patient_id where p.id is null"
    )
    assert orphans == 0
