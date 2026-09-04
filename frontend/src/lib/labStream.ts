/** Lab transport: deterministic, scripted replays streamed over SSE.
 *
 * Exists because a hackathon demo must not depend on the room's acoustics. The same
 * fixtures the evaluation harness scores are replayed through the *same* kernel, so the
 * timeline on screen and the numbers in results/*.json are the same execution.
 */
import type { Snapshot } from './types'

export interface LabOptions {
  scenario: string
  policy: 'safe' | 'naive'
  speed: number
  onSnapshot: (snap: Snapshot) => void
  onError: (message: string) => void
  onEnd: () => void
}

export function openLabStream(opts: LabOptions): () => void {
  const params = new URLSearchParams({
    scenario: opts.scenario,
    policy: opts.policy,
    speed: String(opts.speed),
  })
  const source = new EventSource(`/lab/stream?${params.toString()}`)
  let ended = false
  source.onmessage = (frame) => {
    try {
      const snap = JSON.parse(frame.data) as Snapshot
      if (snap.type === 'end') {
        // Close before onEnd so the browser never reconnects and replays the scenario again.
        ended = true
        source.close()
        opts.onEnd()
      } else {
        opts.onSnapshot(snap)
      }
    } catch (err) {
      opts.onError(`lab frame parse failed: ${(err as Error).message}`)
    }
  }
  source.onerror = () => {
    // A closed socket *after* the end frame is the normal ending of a replay, not a fault.
    if (!ended) {
      opts.onError('lab stream closed — is the dev server running? (python agent/dev_server.py)')
    }
    source.close()
  }
  return () => source.close()
}

export async function listScenarios(): Promise<{ id: string; title: string; tags: string[] }[]> {
  const res = await fetch('/api/scenarios')
  if (!res.ok) return []
  const body = (await res.json()) as { cases?: { id: string; title: string; tags?: string[] }[] }
  return (body.cases ?? []).map((c) => ({ id: c.id, title: c.title, tags: c.tags ?? [] }))
}

/**
 * The scored run, verbatim from `evaluation/results/latest.json`.
 * Deliberately untyped here: ResultsPanel narrows it on `run_id`/`aggregate` at the render
 * boundary, so the panel is the one place that can show a parse failure instead of a lie.
 */
export async function fetchResults(): Promise<unknown> {
  const res = await fetch('/api/results')
  if (!res.ok) return null
  return res.json()
}
