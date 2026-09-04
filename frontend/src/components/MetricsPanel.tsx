import { formatMetric, orderedMetrics } from '../state/reducer'
import type { Session } from '../lib/useSession'

/** Metric cards. The provenance label is not decoration: a simulated figure and a measured
 * one must never look the same on screen (spec §2, §12). */
export function MetricsPanel({ session }: { session: Session }) {
  const { state } = session
  const metrics = orderedMetrics(state.metrics)
  const simulated = metrics.some((m) => m.source.startsWith('simulated'))

  return (
    <div className="space-y-3">
      {simulated && (
        <p className="rounded-md border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-[11px] text-amber-200">
          Latency figures are from the <b>virtual clock</b> (deterministic replay). They are
          reproducible, not measured provider timings. Switch to <b>Live mic</b> to stamp
          <span className="font-mono"> live </span> sources.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
        {metrics.length === 0 && (
          <p className="col-span-full text-sm text-white/35">Metrics appear once the kernel emits measurements.</p>
        )}
        {metrics.map((m) => {
          const rate = m.unit === 'rate'
          const value = formatMetric(m)
          const good = rate ? (m.value ?? m.mean ?? 0) <= 0.0001 || m.name === 'final_state_accuracy' || m.name === 'fencing_correctness' : true
          return (
            <div key={m.name} className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">{m.name}</span>
                <span className="rounded bg-black/40 px-1.5 py-px font-mono text-[9px] text-white/40">{m.source}</span>
              </div>
              <div className={`mt-1 font-mono text-xl font-semibold ${rate && !good ? 'text-rose-300' : 'text-white'}`}>
                {value}
                {m.numerator !== undefined && m.denominator !== undefined && (
                  <span className="ml-1 text-xs font-normal text-white/35">
                    ({m.numerator}/{m.denominator})
                  </span>
                )}
              </div>
              <p className="mt-1 text-[10px] leading-snug text-white/35">{m.definition}</p>
              {m.note && <p className="mt-1 text-[10px] leading-snug text-amber-200/70">{m.note}</p>}
            </div>
          )
        })}
      </div>
    </div>
  )
}
