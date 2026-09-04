import { useState } from 'react'
import { Controls } from './components/Controls'
import { ConversationPanel } from './components/ConversationPanel'
import { EventTimeline } from './components/EventTimeline'
import { MetricsPanel } from './components/MetricsPanel'
import { RaceRibbon } from './components/RaceRibbon'
import { ResultsPanel } from './components/ResultsPanel'
import { StateStrip } from './components/StateStrip'
import { useSession } from './lib/useSession'

const CLAIM = 'A realtime voice agent should never speak or apply a result from a request the user has already superseded.'

export default function App() {
  const session = useSession()
  const [lens, setLens] = useState(true)
  const [view, setView] = useState<'lab' | 'results'>('lab')
  const { state, stopLatencyMs, recoveryMs, acceptedStale } = session

  return (
    <div className="mx-auto flex min-h-screen max-w-[1500px] flex-col gap-4 p-4 lg:p-6">
      <header className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold tracking-tight text-white">
            Break My Voice Agent
            <span className="ml-2 rounded bg-white/5 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-white/45">
              dataforge × rime
            </span>
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-white/55">{CLAIM}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md border border-violet-400/30 bg-violet-400/10 px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-violet-200">
            speech: rime {`{`}coda/astra{`}`}
          </span>
          <button
            onClick={() => setLens((v) => !v)}
            className={`rounded-md border px-3 py-1 text-xs font-semibold uppercase tracking-wider ${
              lens ? 'border-sky-400/40 bg-sky-400/15 text-sky-200' : 'border-white/15 text-white/50'
            }`}
          >
            Engineering lens
          </button>
          <div className="inline-flex overflow-hidden rounded-md border border-white/15">
            {(['lab', 'results'] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1 text-xs font-semibold uppercase tracking-wider ${
                  view === v ? 'bg-white/10 text-white' : 'text-white/45 hover:text-white/80'
                }`}
              >
                {v === 'lab' ? 'Lab' : 'Test results'}
              </button>
            ))}
          </div>
        </div>
      </header>

      <section className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
        <Controls session={session} />
      </section>

      {view === 'lab' ? (
        <>
          <StateStrip session={session} />
          <div className="grid flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
            <div className="flex min-h-[320px] flex-col gap-3">
              <div className="card flex-1 p-3">
                <h2 className="head">Live conversation</h2>
                <ConversationPanel session={session} />
              </div>
              <div className="card p-3">
                <h2 className="head">Race view · one bar per request</h2>
                <RaceRibbon session={session} />
              </div>
            </div>
            <div className="flex min-h-[320px] flex-col gap-3">
              {lens ? (
                <div className="card flex-[1.2] overflow-hidden p-3">
                  <h2 className="head">
                    Event timeline
                    <span className="ml-2 font-mono text-[10px] normal-case text-white/35">
                      {state.events.length} events · {state.policy} arm
                    </span>
                  </h2>
                  <EventTimeline session={session} />
                </div>
              ) : (
                <div className="card flex-1 p-3">
                  <p className="text-sm text-white/45">
                    Turn on <b className="text-white/70">Engineering lens</b> to watch the kernel fence each
                    request in real time.
                  </p>
                </div>
              )}
              <div className="card p-3">
                <h2 className="head">
                  Measured
                  <span className="ml-2 font-mono text-[10px] normal-case text-white/35">
                    {session.transport === 'live' ? 'live timings' : 'virtual clock'}
                  </span>
                </h2>
                <MetricsPanel session={session} />
                {acceptedStale.length > 0 && (
                  <p className="mt-2 rounded-md border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                    ⚠ {acceptedStale.length} obsolete result(s) were <b>applied</b> in this run. That is the
                    naive arm’s failure mode — switch the arm to Safe to see it blocked.
                  </p>
                )}
              </div>
            </div>
          </div>
          <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-white/35">
            <span>stop latency: <b className="font-mono text-white/60">{stopLatencyMs === null ? 'n/a' : `${stopLatencyMs.toFixed(1)} ms`}</b></span>
            <span>recovery: <b className="font-mono text-white/60">{recoveryMs === null ? 'n/a' : `${recoveryMs.toFixed(1)} ms`}</b></span>
            <span>state: <b className="font-mono text-white/60">{state.state}</b></span>
            <span className="ml-auto">hard voice problem: interruption &amp; recovery · no database · no accounts</span>
          </footer>
        </>
      ) : (
        <div className="card flex-1 p-4">
          <h2 className="head">Test results · produced by <span className="font-mono">python evaluation/run_tests.py</span></h2>
          <ResultsPanel />
        </div>
      )}
    </div>
  )
}
