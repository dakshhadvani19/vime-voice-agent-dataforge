import { useEffect, useState } from 'react'
import { fetchResults } from '../lib/labStream'
import fallback from '../generated/latest.json'

interface Run {
  run_id: string
  mode: string
  timing: string
  generated_at: string
  corpus_version: string
  case_count: number
  provenance: { git_sha: string; python: string; platform: string; harness_version: string; rime_config: Record<string, string> }
  summary: { safe_cases: number; safe_failures: number; naive_cases: number; naive_cases_reproducing_defect: number }
  aggregate: Record<'naive' | 'safe', Record<string, { value: number | null; numerator?: number; denominator?: number; n_samples?: number; source?: string }>>
  results: {
    case_id: string
    policy: string
    passed: boolean
    exhibited_defect: boolean
    failures: string[]
    metrics: Record<string, { mean: number | null; unit: string; samples?: number[]; numerator?: number; denominator?: number; source: string }>
    timeline: string[]
  }[]
}

/** Committed evidence view: figures come from evaluation/results/latest.json, i.e. from an
 * executed run, never from a hardcoded string in the UI. */
export function ResultsPanel() {
  const [run, setRun] = useState<Run | null>(null)
  const [source, setSource] = useState<'server' | 'bundled'>('bundled')

  useEffect(() => {
    fetchResults()
      .then((body) => {
        if (body) {
          setRun(body as Run)
          setSource('server')
        } else setRun(fallback as unknown as Run)
      })
      .catch(() => setRun(fallback as unknown as Run))
  }, [])

  if (!run) return <p className="text-sm text-white/40">Loading results…</p>

  const names = Object.keys(run.aggregate.safe ?? {})
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 font-mono text-[11px] text-white/55">
        <span>run {run.run_id}</span>
        <span className="text-white/30">·</span>
        <span>{run.mode}</span>
        <span className="text-white/30">·</span>
        <span>{run.case_count} cases</span>
        <span className="text-white/30">·</span>
        <span>git {run.provenance.git_sha.slice(0, 8)}</span>
        <span className="text-white/30">·</span>
        <span>rime {run.provenance.rime_config.model}/{run.provenance.rime_config.speaker}</span>
        <span className="ml-auto rounded bg-black/40 px-2 py-0.5 text-[10px] text-white/40">{source === 'server' ? 'live file' : 'bundled copy'}</span>
      </div>
      <p className="text-xs text-white/45">{run.timing}</p>

      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="text-left uppercase tracking-wider text-white/40">
            <th className="border-b border-white/10 py-2">metric</th>
            <th className="border-b border-white/10 py-2 text-right">naive</th>
            <th className="border-b border-white/10 py-2 text-right">safe</th>
            <th className="border-b border-white/10 py-2 pl-4">Δ</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {names.map((n) => {
            const nv = run.aggregate.naive?.[n]?.value
            const sv = run.aggregate.safe?.[n]?.value
            const cell = (v: number | null | undefined) =>
              v === null || v === undefined ? 'n/a' : run.aggregate.naive?.[n]?.denominator !== undefined ? `${(v * 100).toFixed(0)}%` : `${v.toFixed(1)}ms`
            const delta = nv !== null && sv !== null && nv !== undefined && sv !== undefined ? `${(nv - sv >= 0 ? '+' : '')}${(nv - sv).toFixed(nv - sv > 1 ? 0 : 3)}` : '—'
            return (
              <tr key={n}>
                <td className="border-b border-white/5 py-1.5 text-white/70">{n}</td>
                <td className="border-b border-white/5 py-1.5 text-right text-rose-200">{cell(nv)}</td>
                <td className="border-b border-white/5 py-1.5 text-right text-emerald-200">{cell(sv)}</td>
                <td className="border-b border-white/5 py-1.5 pl-4 text-white/50">{delta}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="grid gap-2 lg:grid-cols-2">
        {run.results.map((r, i) => (
          <details key={`${r.case_id}-${i}`} className="rounded-lg border border-white/10 bg-black/30 p-2">
            <summary className="cursor-pointer font-mono text-xs text-white/70">
              <span className={r.passed ? 'text-emerald-300' : 'text-rose-300'}>{r.passed ? 'PASS' : 'FAIL'}</span>{' '}
              {r.case_id} <span className="text-white/35">[{r.policy}]</span>
            </summary>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-[10px] leading-relaxed text-white/50">
              {r.timeline.join('\n')}
            </pre>
            {r.failures.length > 0 && <p className="mt-1 text-[10px] text-rose-300">{r.failures.join('; ')}</p>}
          </details>
        ))}
      </div>
    </div>
  )
}
