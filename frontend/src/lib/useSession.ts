import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { connectLive, type LiveSession } from './livekit'
import { listScenarios, openLabStream } from './labStream'
import { reducer, initialState, type LabState } from '../state/reducer'
import type { Snapshot } from './types'

export type Transport = 'live' | 'lab'

/** Owns the transport and feeds one reducer. Components never talk to LiveKit or SSE. */
export function useSession() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [transport, setTransport] = useState<Transport>('lab')
  const [scenario, setScenario] = useState('booking_01')
  const [speed, setSpeed] = useState(1)
  const [liveError, setLiveError] = useState<string>()
  const [micReady, setMicReady] = useState(false)
  const live = useRef<LiveSession | null>(null)
  const closeLab = useRef<null | (() => void)>(null)
  const [scenarios, setScenarios] = useState<{ id: string; title: string; tags: string[] }[]>([])

  const onJson = useCallback((payload: unknown) => {
    const snap = payload as Snapshot
    if (!snap || typeof snap !== 'object' || !('events' in snap)) return
    dispatch({ type: 'snapshot', snapshot: snap })
  }, [])

  // Lab replays: re-open the stream whenever scenario/policy/speed changes.
  useEffect(() => {
    if (transport !== 'lab') return
    setLiveError(undefined)
    const close = openLabStream({
      scenario,
      policy: state.policy,
      speed,
      onSnapshot: (snap) => dispatch({ type: 'snapshot', snapshot: snap }),
      onError: (message) => setLiveError(message),
      onEnd: () => dispatch({ type: 'snapshot', snapshot: { type: 'end' } as unknown as Snapshot }),
    })
    closeLab.current = close
    dispatch({ type: 'connected', transport: 'lab' })
    return () => {
      close()
      closeLab.current = null
      dispatch({ type: 'disconnected' })
    }
    // state.policy intentionally in deps: switching arms must re-run the same fixture.
  }, [transport, scenario, state.policy, speed])

  useEffect(() => {
    listScenarios().then(setScenarios).catch(() => setScenarios([]))
  }, [])

  const startLive = useCallback(async () => {
    setLiveError(undefined)
    try {
      const session = await connectLive({
        identity: `visitor-${Math.random().toString(36).slice(2, 7)}`,
        onJson,
        onError: setLiveError,
        onStateChange: (connected) =>
          dispatch(connected ? { type: 'connected', transport: 'live' } : { type: 'disconnected' }),
      })
      live.current = session
      setMicReady(true)
      setTransport('live')
    } catch (err) {
      // Surfaced instead of swallowed: "grant the mic" vs "agent worker not running" need
      // different fixes on stage.
      setLiveError((err as Error).message)
      dispatch({ type: 'error', message: (err as Error).message })
    }
  }, [onJson])

  const stopLive = useCallback(async () => {
    await live.current?.disconnect().catch(() => undefined)
    live.current = null
    setMicReady(false)
    dispatch({ type: 'disconnected' })
    setTransport('lab')
  }, [])

  const reset = useCallback(() => dispatch({ type: 'reset' }), [])
  const setPolicy = useCallback(
    (policy: 'safe' | 'naive') => dispatch({ type: 'policy', policy }),
    [],
  )

  const derived = useMemo(() => summarize(state), [state])
  return {
    state,
    ...derived,
    transport,
    setTransport,
    scenario,
    setScenario,
    scenarios,
    speed,
    setSpeed,
    micReady,
    error: liveError ?? state.error,
    startLive,
    stopLive,
    setPolicy,
    reset,
  }
}

/** Everything the strip and cards show, computed once from kernel events — the UI does not
 * re-derive latency from its own timers, which would measure the browser instead of the run. */
function summarize(state: LabState) {
  const stops = state.events
    .filter((e) => e.kind === 'speech_cancelled' && typeof e.data.stop_latency_ms === 'number')
    .map((e) => e.data.stop_latency_ms as number)
  const lastDetect = [...state.events].reverse().find((e) => e.kind === 'interruption_detected')
  const recovery = (() => {
    if (!lastDetect) return null
    const after = state.events.find(
      (e) => e.kind === 'speech_started' && e.seq > lastDetect.seq && !e.data.stale,
    )
    return after ? after.t_ms - lastDetect.t_ms : null
  })()
  return {
    stopLatencyMs: stops.length ? stops.reduce((a, b) => a + b, 0) / stops.length : null,
    recoveryMs: recovery,
    interruptionCount: state.events.filter((e) => e.kind === 'interruption_detected').length,
    lastInterruption: lastDetect ?? null,
    discarded: state.events.filter((e) => e.kind === 'tool_returned' && e.data.verdict === 'discarded'),
    acceptedStale: state.events.filter((e) => e.kind === 'tool_returned' && e.data.verdict === 'accepted' && e.data.stale),
    defectNotes: state.events.filter((e) => e.kind === 'note' && e.data.severity === 'defect'),
  }
}

export type Session = ReturnType<typeof useSession>
