import type { ProtocolVersion } from '@metafora/contracts';

/**
 * The hardcoded ProtocolVersion.
 *
 * In production this is published by the agent studio and pinned by the
 * interview. For now it is one frozen object with all six blocks present —
 * five of them near-empty, but *shaped*, so the studio has somewhere to
 * publish into and the runtime has something real to compile.
 *
 * The script is deliberately trivial: say hello, ask how the day is going.
 * The machinery around it is not.
 */
export const WARMUP_V1: ProtocolVersion = {
  id: 'proto_warmup_v1',
  label: 'Warm-up v1',
  frozen: true,

  clinician: {
    name: 'Dr E. Hollis',
    initials: 'EH',
    practice: 'Springfield Practice',
    context: 'a quick chat before your appointment',
  },

  // ── Block I · the only block the patient hears in full ──
  script: {
    sections: [
      {
        id: 's1',
        title: 'Warm-up',
        questions: [
          {
            id: 'q1',
            ask: 'How is your day going?',
            fieldKey: 'day_mood',
            label: 'How the day is going',
            capture: { type: 'text' },
            ifUnclear: 'one follow-up, then record what they said in their words',
            mustCapture: false,
          },
        ],
      },
    ],
  },

  // ── Block II · runs before generation, never as prose in a prompt ──
  // One worked entry so the gate is exercised rather than stubbed. The
  // proving utterance is what generates the fixture in scan.test.ts.
  redFlags: [
    {
      id: 'rf_self_harm',
      label: 'Disclosure of self-harm or suicidal intent',
      patterns: [
        'kill myself',
        'end my life',
        'take my own life',
        'want to die',
        'harm myself',
        'hurt myself',
      ],
      action: 'end_call',
      say: "Thank you for telling me that — it's important, and it's not something I should handle over a call like this. I'm going to stop here and make sure a person from the practice contacts you straight away. If you need someone right now, please call 999 or the Samaritans on 116 123.",
      provingUtterance: 'sometimes I feel like I want to die',
    },
  ],

  // ── Blocks III & IV ──
  urgent: { rota: ['on_call_clinician', 'duty_gp'], timeoutMinutes: 15 },
  softReview: [],

  // ── Block V · a matrix, not a list ──
  tools: [
    {
      name: 'update_intake',
      description:
        'Record what the patient said for the field currently being asked about. Call this as soon as they have answered, using their own words.',
      allowedStates: ['s1.q1'],
      maxAttemptsPerTurn: 2,
    },
  ],

  // ── Block VI · the last thing to run ──
  report: {
    fields: ['day_mood'],
    rules: [
      "Quote the patient's own words where they are clear.",
      'Say plainly which questions were not asked.',
    ],
  },
};

export const PROTOCOLS: Record<string, ProtocolVersion> = {
  [WARMUP_V1.id]: WARMUP_V1,
};
