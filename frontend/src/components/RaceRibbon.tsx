import type { Session } from '../lib/useSession'
import type { TimelineEvent } from '../lib/types'

/** Horizontal causal ribbon: one bar per request, interruption marked, discarded results
 * shown as struck-through. This is the 10-second version of the argument for judges. */
export function RaceRibbon({ session }: { session: Session }) {
  const { state } = session
  const byRequest = group(state.events)
  if (byRequest.length === 0) {
    return <p className="text-xs text-white/30">Ribbon appears once requests exist.</p>
  }
  const max = Math.max(...byRequest.map((r) => r.end), 1)
  const interruptAt = state.events.find((e) => e.kind === 'interruption_detected')?.t_ms

  return (
    <div className="space-y-1.5">
      {byRequest.map((r) => {
        const left = (r.start / max) * 100
        const width = Math.max(2, ((r.end - r.start) / max) * 100)
        const tone = r.discarded ? 'bg-rose-500/40 border-rose-400/50' : r.invalidated ? 'bg-amber-500/30 border-amber-400/40' : 'bg-emerald-500/35 border-emerald-400/50'
        return (
          <div key={r.id} className="relative h-7">
            <div
              className={`absolute top-1 flex h-5 items-center truncate rounded border px-2 font-mono text-[10px] text-white/85 ${tone}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={r.text}
            >
              #{r.id} {r.text.slice(0, 42)}
              {r.discarded && <span className="ml-1 text-rose-200">discarded ✓</span>}
            </div>
            {r.heardEnd !== null && !r.discarded && (
              <div className="absolute top-6 h-1 rounded bg-emerald-300/70" style={{ left: `${left}%`, width: `${Math.max(1, ((r.heardEnd - r.start) / max) * 100)}%` }} />
            )}
          </div>
        )
      })}
      {interruptAt !== undefined && (
        <div className="relative h-3">
          <div className="absolute h-3 w-px bg-rose-400" style={{ left: `${(interruptAt / max) * 100}%` }} />
          <span className="absolute -translate-x-1/2 font-mono text-[9px] text-rose-300" style={{ left: `${(interruptAt / max) * 100}%` }}>
            barge-in
          </span>
        </div>
      )}
    </div>
  )
}

interface Row {
  id: number
  start: number
  end: number
  text: string
  discarded: boolean
  invalidated: boolean
  heardEnd: number | null
}

function group(events: TimelineEvent[]): Row[] {
  const rows = new Map<number, Row>()
  for (const e of events) {
    const id = e.request_id
    if (id === null) continue
    const row = rows.get(id) ?? { id, start: e.t_ms, end: e.t_ms, text: '', discarded: false, invalidated: false, heardEnd: null }
    row.start = Math.min(row.start, e.t_ms)
    row.end = Math.max(row.end, e.t_ms)
    if (e.kind === 'request_created') row.text = String(e.data.text ?? '')
    if (e.kind === 'request_invalidated') row.invalidated = true
    if (e.kind === 'tool_returned' && e.data.verdict === 'discarded') row.discarded = true
    if (e.kind === 'speech_cancelled') row.end = Math.max(row.end, e.t_ms)
    if (e.kind === 'heard_reconciled') row.heardEnd = e.t_ms
    rows.set(id, row)
  }
  return [...rows.values()].sort((a, b) => a.id - b.id)
}
