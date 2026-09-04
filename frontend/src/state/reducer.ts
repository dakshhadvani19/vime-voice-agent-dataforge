import type { AgentState, MetricReading, Snapshot, TimelineEvent } from '../lib/types'
import { PRIMARY_METRICS } from '../lib/types'

/** Single reducer for all conversation truth.
 *
 * Deliberately not `useState` soup inside components: the brief (§7) forbids orchestration
 * logic spread across UI components, and a lab that measures race conditions cannot itself
 * race. Everything the panels display is derived here from kernel snapshots.
 */

export interface ChatTurn {
  id: number
  role: 'user' | 'agent'
  text: string
  request_id: number | null
  status: 'active' | 'superseded' | 'completed' | 'spoken'
  /** Words actually audible before a barge-in cut the utterance. */
  heard?: string
}

export interface LabState {
  connected: boolean
  transport: 'live' | 'lab' | 'none'
  state: AgentState
  policy: 'safe' | 'naive'
  activeRequestId: number | null
  latestRequestId: number | null
  events: TimelineEvent[]
  turns: ChatTurn[]
  metrics: Record<string, MetricReading>
  finalState: Snapshot['final_state']
  staleCount: number
  cancelledCount: number
  scenario?: string
  error?: string
  streaming: boolean
}

export const initialState: LabState = {
  connected: false,
  transport: 'none',
  state: 'idle',
  policy: 'safe',
  activeRequestId: null,
  latestRequestId: null,
  events: [],
  turns: [],
  metrics: {},
  finalState: {},
  staleCount: 0,
  cancelledCount: 0,
  streaming: false,
}

export type Action =
  | { type: 'snapshot'; snapshot: Snapshot }
  | { type: 'connected'; transport: 'live' | 'lab' }
  | { type: 'disconnected' }
  | { type: 'policy'; policy: 'safe' | 'naive' }
  | { type: 'reset' }
  | { type: 'error'; message: string }

export function reducer(state: LabState, action: Action): LabState {
  switch (action.type) {
    case 'connected':
      return { ...state, connected: true, transport: action.transport, error: undefined }
    case 'disconnected':
      return { ...state, connected: false, streaming: false, state: 'idle' }
    case 'policy':
      return { ...state, policy: action.policy }
    case 'error':
      return { ...state, error: action.message, streaming: false }
    case 'reset':
      return { ...initialState, policy: state.policy, transport: state.transport, connected: state.connected }
    case 'snapshot':
      return applySnapshot(state, action.snapshot)
    default:
      return state
  }
}

function applySnapshot(state: LabState, snap: Snapshot): LabState {
  const incoming = snap.events ?? []
  // Append-only, deduped by `seq`: the kernel numbers events, so a replayed or duplicated
  // batch (SSE reconnect, data-channel resend) must never double-count a metric on screen.
  const seen = new Set(state.events.map((e) => e.seq))
  const fresh = incoming.filter((e) => !seen.has(e.seq)).sort((a, b) => a.seq - b.seq)
  if (!fresh.length && snap.state === state.state && !Object.keys(snap.metrics ?? {}).length) {
    return state
  }
  const events = [...state.events, ...fresh]
  return {
    ...state,
    events,
    state: snap.state ?? state.state,
    policy: snap.policy ?? state.policy,
    activeRequestId: snap.active_request_id ?? state.activeRequestId,
    latestRequestId: snap.latest_request_id ?? state.latestRequestId,
    finalState: snap.final_state ?? state.finalState,
    metrics: { ...state.metrics, ...(snap.metrics ?? {}) },
    turns: reduceTurns(state.turns, fresh),
    staleCount: events.filter((e) => e.kind === 'tool_returned' && e.data.verdict === 'discarded').length,
    cancelledCount: events.filter((e) => e.kind === 'speech_cancelled').length,
    scenario: snap.scenario ?? state.scenario,
    streaming: snap.type === 'end' ? false : state.streaming,
  }
}

function reduceTurns(turns: ChatTurn[], events: TimelineEvent[]): ChatTurn[] {
  const next = [...turns]
  const byRequest = new Map<number, number>() // request_id -> index in `next`
  for (const t of next) if (t.request_id !== null) byRequest.set(t.request_id, t.id)

  for (const e of events) {
    const rid = e.request_id
    if (e.kind === 'request_created' && rid !== null) {
      const id = next.length
      next.push({
        id,
        role: 'user',
        text: String(e.data.text ?? ''),
        request_id: rid,
        status: 'active',
      })
      byRequest.set(rid, id)
    } else if (e.kind === 'request_invalidated' && rid !== null) {
      const idx = byRequest.get(rid)
      if (idx !== undefined) next[idx] = { ...next[idx], status: 'superseded' }
    } else if (e.kind === 'speech_started' && rid !== null) {
      next.push({
        id: next.length,
        role: 'agent',
        text: String(e.data.text ?? ''),
        request_id: rid,
        status: e.data.stale ? 'superseded' : 'spoken',
      })
    } else if (e.kind === 'speech_cancelled' && rid !== null) {
      const idx = next.findLastIndex((t) => t.role === 'agent' && t.request_id === rid)
      if (idx >= 0) {
        next[idx] = {
          ...next[idx],
          status: 'superseded',
          heard: String(e.data.heard ?? next[idx].heard ?? ''),
        }
      }
    } else if (e.kind === 'heard_reconciled' && rid !== null) {
      const idx = next.findLastIndex((t) => t.role === 'agent' && t.request_id === rid)
      if (idx >= 0) next[idx] = { ...next[idx], heard: String(e.data.heard ?? '') }
    } else if (e.kind === 'request_completed' && rid !== null) {
      const idx = byRequest.get(rid)
      if (idx !== undefined && next[idx].status === 'active') {
        next[idx] = { ...next[idx], status: 'completed' }
      }
    }
  }
  return next
}

/** Metric rows in brief order first, then anything the runtime added (e.g. rime_ttfb). */
export function orderedMetrics(metrics: Record<string, MetricReading>): MetricReading[] {
  const primary = PRIMARY_METRICS.map((name) => metrics[name]).filter((m) => m !== undefined)
  const extras = Object.values(metrics).filter((m) => !(PRIMARY_METRICS as readonly string[]).includes(m.name))
  return [...primary, ...extras]
}

export function formatMetric(m: MetricReading): string {
  const v = m.value ?? m.mean
  if (v === null || v === undefined) return 'n/a'
  if (m.unit === 'rate') return `${(v * 100).toFixed(0)}%`
  return `${v.toFixed(1)} ms`
}
