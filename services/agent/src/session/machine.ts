import type { FieldState, ProtocolVersion, Question } from '@metafora/contracts';

/**
 * Compiles a published ProtocolVersion into the things the runtime actually
 * executes.
 *
 * The studio's handover table is the spec for this file:
 *   · the interview script becomes the conversation state machine and the
 *     field catalog;
 *   · the tool table becomes a permission matrix checked in-process before
 *     dispatch.
 *
 * Four of the six blocks never enter the prompt. Only the script does.
 */

export interface CompiledState {
  /** `sectionId.questionId` — the id the tool matrix is keyed on. */
  id: string;
  sectionId: string;
  sectionTitle: string;
  question: Question;
}

export interface ToolAuthorisation {
  authorised: boolean;
  reason?: string;
}

export class InterviewMachine {
  readonly states: CompiledState[];
  private index = 0;
  private readonly values = new Map<string, string>();
  /** Follow-up budget per question, so "if unclear" is bounded. */
  private readonly followUps = new Map<string, number>();

  constructor(readonly protocol: ProtocolVersion) {
    this.states = protocol.script.sections.flatMap((section) =>
      section.questions.map((question) => ({
        id: `${section.id}.${question.id}`,
        sectionId: section.id,
        sectionTitle: section.title,
        question,
      })),
    );
    if (this.states.length === 0) throw new Error('protocol has no questions');
  }

  get current(): CompiledState | null {
    return this.states[this.index] ?? null;
  }

  get complete(): boolean {
    return this.index >= this.states.length;
  }

  /** Advance past the current question. Returns the state we moved to. */
  advance(): CompiledState | null {
    this.index = Math.min(this.index + 1, this.states.length);
    return this.current;
  }

  recordFollowUp(stateId: string): number {
    const n = (this.followUps.get(stateId) ?? 0) + 1;
    this.followUps.set(stateId, n);
    return n;
  }

  /**
   * A tool call is legal only from a state the matrix names. This runs before
   * dispatch, in our process, on a call the model cannot route around.
   */
  authorise(toolName: string): ToolAuthorisation {
    const spec = this.protocol.tools.find((t) => t.name === toolName);
    if (!spec) return { authorised: false, reason: 'tool not in protocol' };

    const state = this.current;
    if (!state) return { authorised: false, reason: 'interview is complete' };
    if (!spec.allowedStates.includes(state.id)) {
      return { authorised: false, reason: `not allowed from ${state.id}` };
    }
    return { authorised: true };
  }

  /** Write a captured value, if the key is one this protocol declares. */
  capture(fieldKey: string, value: string): boolean {
    const known = this.states.some((s) => s.question.fieldKey === fieldKey);
    if (!known) return false;
    this.values.set(fieldKey, value);
    return true;
  }

  get captured(): Record<string, string | null> {
    return Object.fromEntries(
      this.states.map((s) => [s.question.fieldKey, this.values.get(s.question.fieldKey) ?? null]),
    );
  }

  /**
   * The "Notes so far" card, which is the same list as the clinician's review
   * composer. A patient watching it fill in is watching the record being
   * written, which is why it is worth a quarter of the screen.
   */
  fields(): FieldState[] {
    return this.states.map((s, i) => {
      const value = this.values.get(s.question.fieldKey) ?? null;
      const status: FieldState['status'] = value
        ? 'captured'
        : i === this.index
          ? 'live'
          : i < this.index
            ? 'open' // moved past without an answer
            : 'pending';
      return { key: s.question.fieldKey, label: s.question.label, value, status };
    });
  }

  /** The tool schema handed to the model, derived from the field catalog. */
  toolDefinitions() {
    return this.protocol.tools.map((spec) => ({
      name: spec.name,
      description: spec.description,
      parameters: {
        type: 'object',
        properties: {
          field: {
            type: 'string',
            enum: this.states.map((s) => s.question.fieldKey),
            description: 'The field key being recorded.',
          },
          value: {
            type: 'string',
            description: "What the patient said, in their own words where possible.",
          },
        },
        required: ['field', 'value'],
        additionalProperties: false,
      },
    }));
  }
}
