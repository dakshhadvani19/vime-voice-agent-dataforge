import { useEffect, useRef } from 'react'
import type { Session } from '../lib/useSession'
import { EVENT_TONE } from '../lib/types'

/** The observability surface from §13 — the proof, not decoration. */
export function EventTimeline({ session }: { session: Session }) {
  const { state } = session
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight, behavior: 'smooth' })
  }, [state.events.length])

  return (
    <div ref={box} className="h-full overflow-y-auto rounded-xl border border-white/10 bg-black/40 p-2 font-mono text-[11px] leading-relaxed">
      {state.events.length === 0 && <p className="p-2 text-white/30">no events yet</p>}
      {state.events.map((e) => {
        const tone = EVENT_TONE[e.kind] ?? 'neutral'
        const color =
          tone === 'good'
            ? e.kind === 'tool_returned' && e.data.verdict === 'discarded'
              ? 'text-emerald-300'
              : 'text-emerald-200'
            : tone === 'bad'
              ? 'text-rose-300'
              : tone === 'info'
                ? 'text-sky-200'
                : 'text-white/60'
        const badge =
          typeof e.data.stop_latency_ms === 'number'
            ? `stop ${e.data.stop_latency_ms.toFixed(0)}ms`
            : typeof e.data.latency_ms === 'number'
              ? `tool ${e.data.latency_ms.toFixed(0)}ms`
              : typeof e.data.value_ms === 'number'
                ? `${e.data.name} ${e.data.value_ms.toFixed(0)}ms`
                : null
        return (
          <div key={e.seq} className="flex items-start gap-2 rounded px-2 py-0.5 hover:bg-white/5">
            <span className="w-14 shrink-0 text-right text-white/25">{(e.t_ms / 1000).toFixed(3)}s</span>
            <span className={`w-6 shrink-0 text-center ${e.data.stale ? 'text-rose-400' : 'text-white/20'}`}>
              {e.data.stale ? '⚠' : '·'}
            </span>
            <span className={`flex-1 ${color}`}>
              {e.label}
              {e.data.reason ? <span className="text-white/35"> — {String(e.data.reason)}</span> : null}
              {e.data.text && e.kind === 'note' ? <span className="text-white/35"> — {String(e.data.text)}</span> : null}
              {e.data.heard ? <span className="text-white/35"> — heard “{String(e.data.heard)}”</span> : null}
            </span>
            {badge && <span className="shrink-0 rounded bg-white/5 px-1.5 text-white/45">{badge}</span>}
          </div>
        )
      })}
    </div>
  )
}
