import { useEffect, useState } from 'react';
import type { ExperienceRange, ExperienceSummary } from '@metafora/contracts';
import { get } from '../api.ts';

/**
 * Patient experience — a real read over invented answers.
 *
 * Nothing asks a patient how the interview went; the opt-in survey is on no
 * roadmap phase. What exists is `metrics.experience_responses`, seeded by a
 * migration and read by `GET /experience` — scoped through the patient by the
 * same predicate as the record itself, because a sentiment is not a clinical
 * fact and that is exactly why it would have been easy to leave unscoped.
 *
 * So the query, the window and the shape are the real ones and only the answers
 * are made up, which is a smaller lie than the loop in the browser this
 * replaced. The caption says which fortnight it is drawing, because the seed
 * ages and a chart that says "today" while drawing last spring is the thing
 * this was fixing.
 *
 * The chart itself is the spec's, reimplemented rather than reinterpreted:
 * stacked counts, the total riding the cap so the values never depend on colour
 * alone, and the sweep on the positive mark. Counts by sentiment and never an
 * average score, because an average hides whether anyone answered at all.
 */

const W = 720;
const TOP = 24;
const BASE = 176;
const L = 6;
const R = 714;
const GAP = 2;
const MAXW = 18;

const KEYS = [
  { key: 'positive', name: 'Positive', fill: 'url(#pxSweep)', swatch: 'var(--sweep)' },
  { key: 'neutral', name: 'Neutral', fill: 'var(--px-neu)', swatch: 'var(--px-neu)' },
  { key: 'negative', name: 'Concerned', fill: 'url(#pxSweepWarn)', swatch: 'var(--sweep-warn)' },
] as const;

/** Square at the baseline, rounded at the data end. */
function cap(x: number, y: number, w: number, h: number, radius: number): string {
  const r = Math.min(radius, w / 2, h);
  return `M${x} ${y + h}V${y + r}a${r} ${r} 0 0 1 ${r} ${-r}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}V${y + h}Z`;
}

export function Experience() {
  const [range, setRange] = useState<ExperienceRange>('week');
  const [hover, setHover] = useState<number | null>(null);
  const [panel, setPanel] = useState<ExperienceSummary | null>(null);

  useEffect(() => {
    let live = true;
    get<ExperienceSummary>(`/experience?range=${range}`)
      .then((value) => live && setPanel(value))
      // The panel sits inside the dashboard; a failed read here must not take
      // the review table down with it, so it degrades to an empty chart.
      .catch(() => live && setPanel({ days: [], scope: 'unavailable' }));
    return () => {
      live = false;
    };
  }, [range]);

  const days = panel?.days ?? [];
  const scope = panel ? panel.scope : 'reading…';

  const totals = days.map((d) => d.positive + d.neutral + d.negative);
  const max = Math.max(...totals, 1);
  const step = max <= 8 ? 2 : max <= 20 ? 5 : 10;
  const top = Math.ceil(max / step) * step;
  const band = (R - L) / days.length;
  const width = Math.min(MAXW, band * 0.52);
  const scale = (v: number) => (BASE - TOP) * (v / top);

  const answered = totals.reduce((a, b) => a + b, 0);
  const positive = days.reduce((a, d) => a + d.positive, 0);
  const concerned = days.reduce((a, d) => a + d.negative, 0);

  return (
    <div className="px">
      <div className="px__top">
        <div>
          <h4 className="px__t">Patient experience</h4>
          <p className="px__s">
            Asked only of patients who opted in. It measures how the interview went, never the
            patient. <b>Seeded</b> — the read is real, no survey writes it yet.
          </p>
        </div>
        <div className="seg" role="group" aria-label="Time range">
          {(['today', 'week', 'all'] as const).map((value) => (
            <button
              key={value}
              className={range === value ? 'seg__b seg__b--on' : 'seg__b'}
              type="button"
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {value === 'today' ? 'Today' : value === 'week' ? 'Last week' : 'Overall'}
            </button>
          ))}
        </div>
      </div>

      <div className="px__stats">
        <span className="pxs">
          <span className="pxs__l">Responses</span>
          <span className="pxs__n">{answered}</span>
          <span className="pxs__d">across {days.length} day{days.length === 1 ? '' : 's'}</span>
        </span>
        <span className="pxs">
          <span className="pxs__l">Positive</span>
          <span className="pxs__n">
            {answered ? Math.round((positive / answered) * 100) : 0}
            <u>%</u>
          </span>
          <span className="pxs__d">
            <b>{positive}</b> of {answered} who answered
          </span>
        </span>
        <span className="pxs">
          <span className="pxs__l">Concerned</span>
          <span className="pxs__n">{concerned}</span>
          <span className="pxs__d">the ones worth reading</span>
        </span>
      </div>

      <div>
        <div className="px__legend">
          <span className="lg lg--pos">
            <i />
            Positive · 4–5
          </span>
          <span className="lg lg--neu">
            <i />
            Neutral · 3
          </span>
          <span className="lg lg--neg">
            <i />
            Concerned · 1–2
          </span>
          <span>{scope}</span>
        </div>

        <div className="px__plot">
          <svg
            viewBox={`0 0 ${W} 216`}
            role="img"
            aria-label="Opt-in patient experience responses, split by sentiment"
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              <linearGradient id="pxSweep" x1="0" y1="1" x2="1" y2="0">
                <stop offset="0%" stopColor="var(--sw-1)" />
                <stop offset="45%" stopColor="var(--sw-2)" />
                <stop offset="100%" stopColor="var(--sw-3)" />
              </linearGradient>
              <linearGradient id="pxSweepWarn" x1="0" y1="1" x2="1" y2="0">
                <stop offset="0%" stopColor="var(--sw-w1)" />
                <stop offset="100%" stopColor="var(--sw-w2)" />
              </linearGradient>
            </defs>

            <line x1={L} y1={BASE + 0.5} x2={R} y2={BASE + 0.5} className="base" />

            {days.map((day, i) => {
              const total = totals[i] ?? 0;
              const cx = L + band * i + band / 2;
              const x = cx - width / 2;
              let y = BASE;
              const marks = KEYS.map((k, ki) => {
                const value = day[k.key];
                if (!value) return null;
                const h = scale(value);
                const isTop = KEYS.slice(ki + 1).every((kk) => !day[kk.key]);
                const hh = Math.max(h - (ki === 0 ? 0 : GAP), 2);
                const yy = y - hh;
                y -= h;
                return isTop ? (
                  <path key={k.key} d={cap(x, yy, width, hh, 3)} fill={k.fill} />
                ) : (
                  <rect key={k.key} x={x} y={yy} width={width} height={hh} fill={k.fill} />
                );
              });

              return (
                <g key={`${day.label}-${i}`}>
                  <rect
                    className="hit"
                    x={L + band * i}
                    y={TOP - 14}
                    width={band}
                    height={BASE - TOP + 28}
                    onMouseEnter={() => setHover(i)}
                  />
                  {marks}
                  {/* The count rides the cap: colour alone should never have to
                      carry the values. */}
                  <text
                    x={cx}
                    y={total ? y - 8 : BASE - 8}
                    textAnchor="middle"
                    className={total ? 'cap' : 'cap cap--zero'}
                  >
                    {total}
                  </text>
                  <text x={cx} y={BASE + 20} textAnchor="middle" className="gx">
                    {day.label}
                  </text>
                </g>
              );
            })}
          </svg>

          {hover !== null && days[hover] && (
            <div
              className="tip tip--on"
              aria-hidden="true"
              style={{
                left: `${Math.max(12, Math.min(88, ((band * hover + band / 2 + 6) / W) * 100))}%`,
              }}
            >
              <b>
                {days[hover].label} · {totals[hover]}{' '}
                {totals[hover] === 1 ? 'response' : 'responses'}
              </b>
              {KEYS.map((k) =>
                days[hover]?.[k.key] ? (
                  <span key={k.key}>
                    <i style={{ background: k.swatch }} />
                    {k.name} · {days[hover][k.key]}
                  </span>
                ) : null,
              )}
            </div>
          )}
        </div>
      </div>

      <details className="px__vals">
        <summary>Show the values</summary>
        <table>
          <thead>
            <tr>
              <th>{range === 'today' ? 'Time' : 'Day'}</th>
              <th>Positive</th>
              <th>Neutral</th>
              <th>Concerned</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            {days.map((day, i) => (
              <tr key={`${day.label}-${i}`}>
                <td>{day.label}</td>
                <td>{day.positive}</td>
                <td>{day.neutral}</td>
                <td>{day.negative}</td>
                <td>{totals[i]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
