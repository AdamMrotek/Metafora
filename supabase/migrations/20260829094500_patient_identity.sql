-- Phase 5.0 · the identity a demo patient has.
--
-- `frontend/dashboard/src/demo.ts` drew NHS numbers and dates of birth in the
-- browser, because the record held a first name and nothing else. That was
-- honest while nothing could hold them; it stops being the best available
-- answer the moment a column can.
--
-- So the roster is here, and the numbers are ones NHS England reserves for
-- test data. The dashboard reads them the way it reads every other value --
-- one query, one shape -- and the invention lives in a file that says so.


-- ─── the columns ─────────────────────────────────────────────────────────────
--
-- Both nullable. A patient this product creates has neither: the intake asks
-- for a first name, and a real deployment would carry the rest across from PDS
-- at dispatch rather than ask a stranger for it.

alter table clinical.patients
    add column nhs_number    text,
    add column date_of_birth date;

-- 999 000 0000 -- 999 999 9999 is the range NHS England reserves for test
-- data; no number in it is issued to a person. The constraint is the
-- guarantee, not the comment: this deployment *cannot* store a real NHS
-- number, and a clinical one drops this line and nothing else.
alter table clinical.patients
    add constraint patients_nhs_number_is_test
    check (nhs_number is null or nhs_number ~ '^999[0-9]{7}$');

create unique index patients_by_nhs_number
    on clinical.patients (nhs_number)
    where nhs_number is not null;


-- ─── the roster ──────────────────────────────────────────────────────────────
--
-- Ten people, and the caseload is these ten. A visitor to the public demo used
-- to become a new row; from here they take a call *as* one of these, which is
-- why the list is short enough to read and long enough that two visitors are
-- rarely the same person. `services/core/queue.py` selects from it.
--
-- `clinician_email` is null on every one: unowned, so `reads.OWNED_BY` shows
-- them to whichever clinician signs in. Phase 5a's dispatch is still the only
-- thing that sets an owner.
--
-- Every check digit below is Modulus 11 over the first nine digits (weights 10
-- down to 2), so these are well-formed as well as reserved --
-- `tests/test_seed_identity.py` re-derives all ten rather than trusting this
-- comment.

insert into clinical.patients (id, first_name, origin, nhs_number, date_of_birth) values
    ('pt_demo_01', 'Alice', 'demo', '9990000018', date '1951-03-14'),
    ('pt_demo_02', 'Ben',   'demo', '9990000026', date '1948-11-02'),
    ('pt_demo_03', 'Chloe', 'demo', '9990000034', date '1993-07-22'),
    ('pt_demo_04', 'Dev',   'demo', '9990000042', date '1967-01-09'),
    ('pt_demo_05', 'Erin',  'demo', '9990000050', date '1975-09-30'),
    ('pt_demo_06', 'Femi',  'demo', '9990000069', date '1982-04-17'),
    ('pt_demo_07', 'Greta', 'demo', '9990000077', date '1958-12-05'),
    ('pt_demo_08', 'Hari',  'demo', '9990000085', date '1990-02-28'),
    ('pt_demo_09', 'Iris',  'demo', '9990000093', date '1949-06-11'),
    ('pt_demo_10', 'Jonas', 'demo', '9990000107', date '1998-10-23')
on conflict (id) do nothing;
