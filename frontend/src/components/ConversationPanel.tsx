import type { Session } from '../lib/useSession'

/** The dialogue, annotated with what the fence decided about each turn. */
export function ConversationPanel({ session }: { session: Session }) {
  const { state } = session
  return (
    <div className="flex h-full flex-col gap-2 overflow-y-auto pr-1">
      {state.turns.length === 0 && (
        <p className="rounded-lg border border-dashed border-white/15 p-4 text-sm text-white/40">
          Say something — or hit <b className="text-white/70">Re-run</b> — then interrupt the agent
          while it is speaking. The stale utterance below should never reach your ears.
        </p>
      )}
      {state.turns.map((t) => (
        <div
          key={`${t.id}-${t.request_id}`}
          className={`max-w-[92%] rounded-lg border px-3 py-2 text-sm ${
            t.role === 'user'
              ? 'self-end border-sky-400/25 bg-sky-400/10 text-sky-50'
              : 'self-start border-white/10 bg-white/[0.04] text-white/85'
          } ${t.status === 'superseded' ? 'opacity-70' : ''}`}
        >
          <div className="mb-1 flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-white/40">
            <span>{t.role === 'user' ? 'user' : 'agent · rime'}</span>
            {t.request_id !== null && <span>#{t.request_id}</span>}
            <StatusChip status={t.status} />
          </div>
          <p className="leading-snug">{t.text}</p>
          {t.heard !== undefined && (
            <p className="mt-1 border-t border-white/10 pt-1 text-xs text-white/45">
              audible before the cut: <span className="font-mono text-emerald-300">“{t.heard}”</span>
            </p>
          )}
        </div>
      ))}
    </div>
  )
}

function StatusChip({ status }: { status: 'active' | 'superseded' | 'completed' | 'spoken' }) {
  const map = {
    active: 'text-amber-300 border-amber-400/30',
    superseded: 'text-rose-300 border-rose-400/30',
    completed: 'text-white/40 border-white/15',
    spoken: 'text-emerald-300 border-emerald-400/30',
  } as const
  return <span className={`rounded border px-1.5 py-px ${map[status]}`}>{status}</span>
}
