/** Telemetry contract shared with agent/main.py and agent/dev_server.py.
 *
 * The shapes below are the JSON the kernel publishes on the LiveKit data channel
 * (topic `vime.timeline`) and over the Lab-mode SSE stream. Keeping them declared here —
 * rather than reading `any` off the wire — is what stops the UI from quietly displaying a
 * field the runtime renamed. `agent/tests/test_corpus.py` asserts the same keys.
 */

export type AgentState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'interrupted'
  | 'cancelling'

export type EventKind =
  | 'request_created'
  | 'request_invalidated'
  | 'request_completed'
  | 'tool_started'
  | 'tool_returned'
  | 'speech_started'
  | 'speech_completed'
  | 'speech_cancelled'
  | 'speech_word'
  | 'interruption_detected'
  | 'heard_reconciled'
  | 'state_changed'
  | 'measurement'
  | 'note'

export interface TimelineEvent {
  seq: number
  kind: EventKind
  request_id: number | null
  t_ms: number
  state: AgentState | null
  label: string
  data: {
    text?: string
    verdict?: 'accepted' | 'discarded'
    stale?: boolean
    reason?: string
    severity?: string
    stop_latency_ms?: number
    latency_ms?: number
    context_id?: string
    heard?: string
    unheard_ms?: number
    delay_ms?: number
    value_ms?: number
    name?: string
    cancelled?: boolean
    connection_reused?: boolean
    transport?: string
    [key: string]: unknown
  }
}

export type MetricSource =
  | 'simulated'
  | 'live'
  | 'live-cold'
  | 'live-warm'
  | 'live-clock-no-provider'
  | 'live-rime-cold'
  | 'live-rime-warm'

export interface MetricReading {
  name: string
  definition: string
  unit: string
  n: number
  mean: number | null
  median?: number | null
  min?: number | null
  max?: number | null
  p95?: number | null
  value?: number | null
  numerator?: number
  denominator?: number
  samples?: number[]
  /** Provenance of the number; see `MetricSource`. Kept open so a new stamp from the
   *  kernel (e.g. `live-rime-cold`) renders instead of crashing the UI. */
  source: MetricSource | (string & {})
  note?: string | null
}

export type MetricsMap = Record<string, MetricReading>

export interface FinalState {
  domain?: string
  party_size?: number | null
  time?: string | null
  origin?: string | null
  destination?: string | null
  cancelled?: boolean
}

export interface Snapshot {
  type: 'timeline' | 'hello' | 'end'
  policy: 'safe' | 'naive'
  state: AgentState
  active_request_id: number | null
  latest_request_id: number | null
  events: TimelineEvent[]
  final_state: FinalState
  metrics: MetricsMap
  error?: string
  scenario?: string
}

/** The metric names the brief defines (§12) drive the panel order; extras append after. */
export const PRIMARY_METRICS = [
  'interruption_stop_latency',
  'recovery_latency',
  'stale_output_rate',
  'stale_session_rate',
  'final_state_accuracy',
  'fencing_correctness',
] as const

export const EVENT_TONE: Record<EventKind, 'neutral' | 'good' | 'bad' | 'info'> = {
  request_created: 'info',
  request_invalidated: 'info',
  request_completed: 'neutral',
  tool_started: 'neutral',
  tool_returned: 'good',
  speech_started: 'info',
  speech_completed: 'neutral',
  speech_cancelled: 'good',
  speech_word: 'neutral',
  interruption_detected: 'bad',
  heard_reconciled: 'info',
  state_changed: 'neutral',
  measurement: 'neutral',
  note: 'neutral',
}

export function isStale(e: TimelineEvent): boolean {
  return Boolean(e.data.stale)
}

export function isDiscarded(e: TimelineEvent): boolean {
  return e.data.verdict === 'discarded'
}
