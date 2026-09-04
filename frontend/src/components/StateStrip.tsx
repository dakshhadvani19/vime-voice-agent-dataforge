import type { Session } from '../lib/useSession'

const TONE: Record<string, string> = {
  idle: 'text-white/50 border-white/15',
  listening: 'text-sky-300 border-sky-400/40',
  thinking: 'text-amber-300 border-amber-400/40',
  speaking: 'text-emerald-300 border-emerald-400/40',
  interrupted: 'text-rose-300 border-rose-400/50',
  cancelling: 'text-rose-200 border-rose-400/50',
}

/** Current agent state, live request id, and what the app state actually is (§14). */
export function StateStrip({ session }: { session: Session }) {
  const { state } = session
  const fs = state.finalState
  const chips = [
    fs.party_size ? `${fs.party_size} people` : null,
    fs.time ?? null,
    fs.destination ? (fs.origin ? `${fs.origin} → ${fs.destination}` : `→ ${fs.destination}`) : null,
    fs.cancelled ? 'cancelled' : null,
  ].filter(Boolean) as string[]

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-white/10 bg-white/[0.03] px-4 py-3">
      <span className={`rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-widest ${TONE[state.state] ?? TONE.idle}`}>
        {state.state}
      </span>
      <span className="font-mono text-xs text-white/60">
        active <b className="text-white">#{state.activeRequestId ?? '—'}</b> · latest{' '}
        <b className="text-white">#{state.latestRequestId ?? '—'}</b>
      </span>
      <span className="font-mono text-xs text-white/60">
        stale discarded <b className="text-emerald-300">{state.staleCount}</b> · speech cancelled{' '}
        <b className="text-emerald-300">{state.cancelledCount}</b>
      </span>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <span className="label">app state</span>
        {chips.length ? (
          chips.map((c) => (
            <span key={c} className="rounded-md bg-indigo-400/15 px-2 py-0.5 font-mono text-xs text-indigo-200">
              {c}
            </span>
          ))
        ) : (
          <span className="font-mono text-xs text-white/30">empty</span>
        )}
      </div>
    </div>
  )
}
