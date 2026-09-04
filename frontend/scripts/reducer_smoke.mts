import { reducer, initialState } from '../src/state/reducer'
import type { Snapshot } from '../src/lib/types'

/** Feeds the real /lab/stream SSE frames through the real reducer (no browser needed).
 *  Proves the wire contract and the seq-dedup/turn projection on both arms. */
async function stream(scenario: string, policy: string) {
  const res = await fetch(
    `http://127.0.0.1:8080/lab/stream?scenario=${scenario}&policy=${policy}&speed=400`,
  )
  if (!res.body) throw new Error('no body')
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let s = reducer(initialState, { type: 'policy', policy: policy as 'safe' | 'naive' })
  let frames = 0
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let nl: number
    while ((nl = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, nl)
      buf = buf.slice(nl + 2)
      if (!chunk.startsWith('data: ')) continue
      const payload = JSON.parse(chunk.slice(6))
      frames++
      if (payload.type === 'timeline') s = reducer(s, { type: 'snapshot', snapshot: payload as Snapshot })
      if (payload.type === 'end') {
        void reader.cancel()
        return { frames, s }
      }
    }
  }
  return { frames, s }
}

const SEEN: Record<string, { policy: string; scenario: string; events: number; dupeSeqs: number; staleAccepted: number }> = {}
for (const scenario of ['late_result_01', 'double_interrupt_01', 'false_speech_01']) {
  for (const policy of ['naive', 'safe'] as const) {
    const { frames, s } = await stream(scenario, policy)
    // Same predicate App.tsx uses for the red banner: a stale result that was *accepted*.
    // `verdict: 'discarded' + stale: true` is the fence doing its job, not a defect.
    const staleAccepted = s.events.filter(
      (e) =>
        e.kind === 'tool_returned' &&
        (e.data as Record<string, unknown>)?.stale === true &&
        (e.data as Record<string, unknown>)?.verdict === 'accepted',
    ).length
    console.log(`\n── ${scenario} / ${policy}`)
    console.log(
      `   ${frames} frames → ${s.events.length} events | state=${s.state} active=#${s.activeRequestId}` +
        ` latest=#${s.latestRequestId} staleCount=${s.staleCount} cancelled=${s.cancelledCount}`,
    )
    console.log(
      `   turns: ${s.turns.map((t) => `${t.role}#${t.request_id ?? '-'}[${t.status}]${t.heard ? ` heard="${t.heard}"` : ''}`).join(' → ')}`,
    )
    console.log(
      `   final=${JSON.stringify(s.finalState)} accepted-but-stale=${staleAccepted} metrics=${Object.keys(s.metrics).length}`,
    )
    const seqs = s.events.map((e) => e.seq)
    const dupeSeqs = seqs.length - new Set(seqs).size
    SEEN[`${scenario}/${policy}`] = { policy, scenario, events: s.events.length, dupeSeqs, staleAccepted }
    if (dupeSeqs) console.log(`   !! ${dupeSeqs} duplicated seq values`)
    if (s.error) console.log(`   error=${s.error}`)
  }
}
const states = Object.values(SEEN)
const dupes = states.filter((x) => x.dupeSeqs > 0)
const naiveStale = states.filter((x) => x.policy === 'naive' && x.scenario === 'late_result_01')[0]
const safeStale = states.filter((x) => x.policy === 'safe' && x.scenario === 'late_result_01')[0]
if (dupes.length) { console.error(`FAIL: reducer double-counted seqs in ${dupes.length} run(s)`); process.exit(1) }
if (!states.every((x) => x.events > 0)) { console.error('FAIL: some run produced no timeline events (Engineering Lens would be empty)'); process.exit(1) }
if (!(naiveStale?.staleAccepted > 0)) { console.error('FAIL: naive arm shows no accepted-but-stale result — the UI cannot display the defect'); process.exit(1) }
if (safeStale?.staleAccepted) { console.error('FAIL: safe arm accepted a stale result'); process.exit(1) }
console.log('\n✓ smoke green: seqs unique, timeline non-empty on every run, defect visible on naive and absent on safe\n')
