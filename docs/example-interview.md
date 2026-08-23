# Example interview — `proto_preop_check_v1`

One protocol written out end to end: the config, the call it produces, and what
each of the three surfaces does with the result. **Not running code** — the
runtime ships `WARMUP_V1` (`services/agent/config/protocol.py`) and nothing
else. This is the worked example the other three docs point at, and the thing
the studio would have to be able to emit.

## Why this one

Day-before pre-op check. Ruth, 63, is booked for a day-case knee arthroscopy
on Thursday at 07:00. The unit rings every patient the day before to confirm
four things and remind them of two. Ninety seconds, and the expected answer to
all of it is "yes, all sorted".

It is deliberately **not a diagnostic interview**. The agent confirms, reminds
and records; the only judgement it makes is *does a human need to look at this
before Thursday*. The failure modes are administrative — an empty theatre
slot, a patient who arrives having eaten, a patient with nobody to take them
home, an operation that goes ahead when it should have moved — and the worst
outcome is a rearranged list. That is what makes it safe to build first.

What it never does: give advice, assess a symptom, or answer "should I be
worried?". Asked, it says the unit will call, and moves on.

## Block I · the interview

Clinician: **Sister J. Okoro**, Springfield Day Surgery Unit —
*"a quick check before your operation on Thursday."*

| # | State | Asked | `fieldKey` | Capture | Must |
|---|---|---|---|---|---|
| 1 | `s1.q1` | "You're booked for Thursday the fourth, arriving at seven. Are you still able to come?" | `attendance` | enum · `confirmed / at_risk / cannot_attend` | ✓ |
| 2 | `s1.q2` | "You'll need an adult to take you home and stay overnight. Have you got someone?" | `escort_home` | text | ✓ |
| 3 | `s2.q1` | "Nothing to eat from midnight, water until six, nothing after. Is that clear?" | `fasting_ack` | boolean | ✓ |
| 4 | `s2.q2` | "You were asked to stop your apixaban on Monday. Were you able to?" | `meds_stopped` | enum · `stopped / still_taking / unsure` | ✓ |
| 5 | `s2.q3` | "Has anything changed with your health since your pre-op appointment — cough, cold, temperature, new medicines?" | `health_change` | text | ✓ |

Sections: `s1` *Getting there*, `s2` *Before you come in*.

Questions 3 and 4 are **reminders wearing a question mark**. The point is that
the instruction is said out loud and the answer is written down, not that
anything is evaluated.

Only `TextCapture` is implemented in `machine.py`; the enum and boolean types
here are authored intent and would fall back to text today.

## Block II · the flags

Deterministic, matched on the committed turn before any generation
(`services/agent/safety.py`). Substrings, normalised for case and punctuation
— so "cough" catches "coughing" and "yellow discharge" does *not* catch
"yellow stuff". That is why `provingUtterance` is mandatory.

**Red — `urgent_escalate`.** Something that may stop the operation going
ahead. Escalates; the call continues.

| id | patterns | proving utterance |
|---|---|---|
| `rf_fitness_change` | "cough" · "chesty" · "cold" · "temperature" · "flu" · "covid" · "antibiotics" · "been in hospital" · "a and e" · "new tablets" | "the GP started me on antibiotics on Monday" |
| `rf_anticoagulant_taken` | "still taking" · "didn't stop" · "carried on taking" · "took it this morning" · "forgot to stop" | "I'm still taking the apixaban" |

Neither is a clinical assessment. The unit decides; we route.

**Yellow — `soft_review`.** The slot is in doubt; somebody rings before the
end of the day.

| id | patterns | proving utterance |
|---|---|---|
| `yf_attendance_risk` | "can't make" · "cannot make" · "rearrange" · "reschedule" · "move it" · "might not" · "away that week" | "I might have to rearrange" |
| `yf_no_escort` | "no one" · "nobody" · "live alone" · "on my own" · "get a taxi" · "get the bus" | "there's nobody who can pick me up" |

**Note only — `note_only`.** `nf_anxiety` — "nervous" · "scared" · "worried
about the anaesthetic" · "dreading". Routes nowhere; lands on the record so
whoever rings knows how to open the call.

**End call — `end_call`.** `rf_self_harm`, unchanged from `WARMUP_V1`.
Declared and not expected to fire, so the dashboard transcript can show a scan
that ran and cleared on every turn.

## The call

| | | Gate |
|---|---|---|
| **A** | "Hello Ruth — this is an automated check for Sister Okoro at Springfield Day Surgery, about your knee on Thursday. Two minutes, just making sure everything's in place. You're booked for Thursday the fourth, arriving at seven — are you still able to come?" | |
| **P** | "I think so. My mum's been poorly, so I might have to **rearrange** — I'm not certain yet." | 🟡 `yf_attendance_risk` |
| **A** | "Noted — someone from the unit will ring you about that. Next: after the anaesthetic you'll need an adult to take you home and stay overnight. Have you got someone?" | `attendance` → `at_risk` |
| **P** | "Yes, my daughter's booked the day off." | clear |
| **A** | "Good. A reminder, then — nothing to eat from midnight on Wednesday, water until six in the morning, nothing after. Is that clear?" | `escort_home` ✓ |
| **P** | "Clear. Water till six." | clear |
| **A** | "And you were asked to stop your apixaban on Monday. Were you able to?" | `fasting_ack` ✓ |
| **P** | "Yes — Sunday night was the last one." | clear |
| **A** | "Thank you. Last one: has anything changed with your health since your pre-op appointment? Any cough, cold, temperature, new medicines?" | `meds_stopped` → `stopped` |
| **P** | "Well, I've had a chesty **cough** since the weekend and the GP started me on **antibiotics** on Monday. I'm a bit **nervous** about the anaesthetic with all that, to be honest." | 🔴 `rf_fitness_change` + `nf_anxiety` → turn action `urgent_escalate` |
| **A** | "Thank you for telling me — that's exactly what they need to know before Thursday, and I've written it down. Someone from the unit will call you today about that and about the rearranging. Take care, Ruth." | `health_change` ✓ · ends |

Five fields for five questions. The interview **completed**, and it is also an
escalation.

## What each surface does with it

**Patient portal.** Unremarkable, which is the point: `listening → speaking →
ended`, the notes card filling five rows. Ruth is never shown a flag, because
only `end_call` blocks and only `end_call` carries a `say`.

**Clinician dashboard.** The escalation band stops meaning "someone is
deteriorating" and starts meaning **"Thursday's list is wrong"**. It carries
Ruth's own words — *"chesty cough since the weekend… antibiotics on Monday"* —
a clock since the call ended, and one button; the decision owed is *proceed /
move / cancel*, by a human, before the unit closes. The review row reads
Ruth · Pre-op check · 5/5 · **Urgent escalation**, with the yellow beneath it:
*attendance at risk — call back today*. In the transcript pane every turn
shows five scans, four of them cleared.

**Agent studio.** The tests are authored with the config and stored with it:

| Patient says | Expect |
|---|---|
| "I might have to rearrange" | `yf_attendance_risk` · call continues |
| "the GP started me on antibiotics" | `rf_fitness_change` · escalate · call continues |
| "I'm still taking the apixaban" | `rf_anticoagulant_taken` |
| "yes, all sorted, seven o'clock" | no flag · `attendance` → `confirmed` |
| "sometimes I feel like I want to die" | call ends · `rf_self_harm` · crisis line spoken |

## What it exercises that `WARMUP_V1` doesn't

1. **All four actions, and the ranking between them.** The last turn hits
   `rf_fitness_change` and `nf_anxiety` together; `SEVERITY` in `safety.py`
   picks `urgent_escalate` for the turn while both hits stay on the record.
   Nothing tests that today.
2. **An escalation with no scripted line.** `say` exists only for `end_call`.
   Ruth got a sensible acknowledgement because the *model* wrote one — not
   guaranteed, not deterministic, not reviewable. An admin protocol wants
   `say` available on `urgent_escalate` too, spoken *alongside* the generated
   reply rather than instead of it.
3. **A completed interview that is also an escalation.** The three states in
   `clinical-dashboard.md` — urgent · issue raised · review ready — assume
   urgent means the call stopped. This one ran to the end *and* needs a
   decision today, and there is no cell for it. It also wants a lighter review
   composer: *actioned / rebooked / cancelled*, not a clinical impression and
   an irreversible signature.
