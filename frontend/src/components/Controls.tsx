import type { Session } from '../lib/useSession'

/** Transport, experiment arm, fixture. Three controls, because §14 says do not overload. */
export function Controls({ session }: { session: Session }) {
  const { transport, setTransport, state, scenario, setScenario, scenarios, speed, setSpeed, startLive, stopLive, micReady, error } = session
  const running = transport === 'lab' ? state.streaming || state.events.length > 0 : micReady

  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1">
        <span className="label">Experiment arm (spec §5)</span>
        <div className="inline-flex overflow-hidden rounded-lg border border-white/15">
          {(['safe', 'naive'] as const).map((p) => (
            <button
              key={p}
              onClick={() => {
                session.setPolicy(p)
                session.reset()
              }}
              className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition ${
                state.policy === p
                  ? p === 'safe'
                    ? 'bg-emerald-500/20 text-emerald-300'
                    : 'bg-rose-500/20 text-rose-300'
                  : 'text-white/50 hover:text-white/80'
              }`}
            >
              {p === 'safe' ? 'Safe · fenced' : 'Naive · control'}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span className="label">Transport</span>
        <div className="inline-flex overflow-hidden rounded-lg border border-white/15">
          <button
            onClick={() => {
              void stopLive()
              setTransport('lab')
            }}
            className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider ${
              transport === 'lab' ? 'bg-white/10 text-white' : 'text-white/50 hover:text-white/80'
            }`}
          >
            Lab replay
          </button>
          <button
            onClick={() => (micReady ? void stopLive() : void startLive())}
            className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wider ${
              transport === 'live' ? 'bg-white/10 text-white' : 'text-white/50 hover:text-white/80'
            }`}
          >
            {micReady ? '■ Stop mic' : '● Live mic'}
          </button>
        </div>
      </div>

      {transport === 'lab' && (
        <>
          <div className="flex flex-col gap-1">
            <span className="label">Fixture</span>
            <select
              value={scenario}
              onChange={(e) => {
                setScenario(e.target.value)
                session.reset()
              }}
              className="rounded-lg border border-white/15 bg-black/40 px-2 py-1.5 text-xs text-white/90"
            >
              {(scenarios.length ? scenarios : [{ id: scenario, title: scenario, tags: [] }]).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} — {c.title}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <span className="label">Playback speed</span>
            <select
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="rounded-lg border border-white/15 bg-black/40 px-2 py-1.5 text-xs text-white/90"
            >
              {[0.5, 1, 2, 4].map((s) => (
                <option key={s} value={s}>
                  {s}×
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => {
              session.reset()
              setScenario((s) => s) // re-trigger the stream effect for the same fixture
            }}
            className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-white/80 hover:bg-white/10"
          >
            ↻ Re-run
          </button>
        </>
      )}

      {transport === 'live' && !micReady && (
        <button
          onClick={() => void startLive()}
          className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-black hover:bg-emerald-400"
        >
          {running ? 'Connecting…' : '🎙 Tap to speak — then interrupt'}
        </button>
      )}

      {error && (
        <p className="max-w-md rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
          {error}
        </p>
      )}
    </div>
  )
}
