-- Phase 5.0 · the patient-experience panel gets a source.
--
-- The first table in `metrics`, which the initial schema claimed and left
-- empty. It holds a sentiment and a day and a patient id -- nothing medical
-- goes here, and nothing here says anything about a person's health.
--
-- The panel was the one screen with nothing behind it at all: no survey asks a
-- patient how the interview went, and none is on any roadmap phase. That is
-- still true. What changes is where the invention lives -- a seeded table the
-- dashboard queries, rather than a loop in the browser -- so the read path,
-- the scoping and the shape are all real and only the answers are made up.


create table metrics.experience_responses (
    id           text primary key,
    patient_id   text not null references clinical.patients (id),
    sentiment    text not null check (sentiment in ('positive', 'neutral', 'negative')),
    responded_at timestamptz not null
);

create index experience_by_day on metrics.experience_responses (responded_at desc);
create index experience_by_patient on metrics.experience_responses (patient_id);


-- ─── the seed ────────────────────────────────────────────────────────────────
--
-- Fourteen days ending the day this migration is applied, in the volumes the
-- browser used to draw: four to twelve positive a day, one to four neutral,
-- nought to two concerned. The three series are three independent slices of
-- one md5 of the date, so they move independently -- a single counter mod 9
-- draws a straight line, which is a worse picture than no picture.
--
-- These dates go stale, and `reads.experience` is where that is handled: the
-- window anchors on the newest response rather than on `now()`, so a demo
-- redeployed a year from now still draws a full chart and says which fortnight
-- it is drawing.

insert into metrics.experience_responses (id, patient_id, sentiment, responded_at)
select 'xr_' || to_char(p.day, 'YYYYMMDD') || '_' || k.sentiment || '_' || g,
       'pt_demo_' || lpad((((extract(doy from p.day)::int + g) % 10) + 1)::text, 2, '0'),
       k.sentiment,
       p.day + interval '8 hours' + (g * interval '43 minutes')
  from (
      select day,
             4 + abs(mod(a, 9)) as positive,
             1 + abs(mod(b, 4)) as neutral,
             abs(mod(c, 3))     as negative
        from (
            select day,
                   ('x' || substr(md5(to_char(day, 'YYYY-MM-DD')),  1, 8))::bit(32)::int as a,
                   ('x' || substr(md5(to_char(day, 'YYYY-MM-DD')),  9, 8))::bit(32)::int as b,
                   ('x' || substr(md5(to_char(day, 'YYYY-MM-DD')), 17, 8))::bit(32)::int as c
              from generate_series(
                       (current_date - 13)::timestamptz,
                       current_date::timestamptz,
                       interval '1 day'
                   ) as day
        ) as mixed
  ) as p
  cross join lateral (
      values ('positive', p.positive), ('neutral', p.neutral), ('negative', p.negative)
  ) as k (sentiment, n)
  cross join lateral generate_series(1, k.n) as g
on conflict (id) do nothing;
