import type { ProtocolVersion, RedFlag } from '@metafora/contracts';

/**
 * The deterministic red-flag gate.
 *
 * This runs on the committed turn, *before any generation*. That ordering is
 * the entire point of putting the backend in the media path: the transcript
 * passes through our code before it reaches the model, so the scan is an
 * inline gate rather than a parallel observer, and it cannot be bypassed by
 * the model declining to tell us something.
 *
 * There is no model in this file and there must never be one. A gate that
 * asks an LLM whether it is safe to call an LLM is not a gate.
 */

export interface ScanHit {
  flag: RedFlag;
  /** The matched span, kept for the audit trail. */
  matched: string;
}

export interface ScanResult {
  hits: ScanHit[];
  /** True when generation must not run at all. */
  blocked: boolean;
  /** Set when `blocked` — spoken to the patient instead of a model reply. */
  say?: string;
  action?: RedFlag['action'];
}

/** Ranked so the most serious outcome decides the turn. */
const SEVERITY: Record<RedFlag['action'], number> = {
  end_call: 3,
  urgent_escalate: 2,
  soft_review: 1,
  note_only: 0,
};

/**
 * Normalise before matching so trivial obfuscation and ordinary speech
 * artefacts do not walk past the gate: case, punctuation, and repeated
 * whitespace all collapse. Padding with spaces lets patterns match at the
 * string edges using the same word-boundary rule as the middle.
 */
function normalise(text: string): string {
  return ` ${text.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim()} `;
}

export function scan(transcript: string, protocol: ProtocolVersion): ScanResult {
  const haystack = normalise(transcript);
  const hits: ScanHit[] = [];

  for (const flag of protocol.redFlags) {
    for (const pattern of flag.patterns) {
      const needle = normalise(pattern);
      if (needle.trim() && haystack.includes(needle)) {
        hits.push({ flag, matched: pattern });
        break; // one hit per flag is enough; the flag is what matters
      }
    }
  }

  if (hits.length === 0) return { hits: [], blocked: false };

  const worst = hits.reduce((a, b) => (SEVERITY[b.flag.action] > SEVERITY[a.flag.action] ? b : a));
  const blocked = worst.flag.action === 'end_call';

  return {
    hits,
    blocked,
    action: worst.flag.action,
    ...(blocked ? { say: worst.flag.say } : {}),
  };
}
