import type { FieldState } from '@metafora/contracts';

/**
 * Notes so far · the top right corner.
 *
 * A patient's real anxiety about answering questions out loud is "did that
 * count?" This card answers it continuously and asks for nothing. No rules
 * inside it, no controls, no colour except on the line being talked about now.
 *
 * Everything here is a field on the clinician's review composer. A patient
 * watching this list fill in is watching the clinical record being written,
 * which is why it is worth a quarter of the screen.
 */
export function Notes({ fields, clinician }: { fields: FieldState[]; clinician: string }) {
  return (
    <aside className="notes">
      <div className="notes__card">
        <span className="notes__t">Notes so far</span>
        <ul className="notes__l">
          {fields.map((field) => (
            <li
              key={field.key}
              className={
                field.status === 'live'
                  ? 'note note--live'
                  : field.status === 'open'
                    ? 'note note--open'
                    : 'note'
              }
            >
              <span className="note__l">{field.label}</span>
              <span className="note__v">
                {field.value ?? (field.status === 'live' ? 'Talking about it now…' : 'Not yet')}
              </span>
            </li>
          ))}
        </ul>
        <p className="notes__f">
          <b>{clinician} reads this list</b> before your appointment. Say “that’s not right” at any
          point and we will fix the line together.
        </p>
      </div>
    </aside>
  );
}
