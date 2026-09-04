# Break My Voice Agent

**An interruption-safety laboratory for realtime voice agents.** Not a chatbot with a mic:
the product *is* the engineering problem.

> **Claim.** A realtime voice agent should never speak or apply a result from a request the
> user has already superseded.
>
> **Hard voice problem.** Interruption and recovery. **Acceptance test:** [§19 of the brief](docs/SPEC.md),
> executed as a test — `agent/tests/test_corpus.py::TestAcceptanceTestClauses`.

Built for the **DataForge × Rime hackathon**. Specification pinned at
[`docs/SPEC.md`](docs/SPEC.md); §-references throughout the code point back into it.

---

## 1 · What you can actually do with it

| | |
| --- | --- |
| **Talk to it** | LiveKit room in the browser. Deepgram STT → fast LLM → a deliberately slow tool → **Rime** speaks the answer. |
| **Break it** | Interrupt mid-sentence, or change the request while the slow tool is still running. |
| **Watch it fence** | Event timeline shows `REQUEST #1 INVALIDATED` → `TOOL #1 RETURNED → DISCARDED ✓` → `TOOL #2 RETURNED → ACCEPTED ✓`. |
| **Compare arms** | Flip **Naive ↔ Safe** and re-run the *same* fixture: same scenario, same delayed tool, same interruption moment (spec §5). |
| **Replay without a mic** | **Lab** mode streams the scored fixtures over SSE, so a demo or a CI job is reproducible on any machine. |
| **Read the numbers** | **Test results** tab renders `evaluation/results/latest.json`, i.e. an executed run, not typed-in figures. |

## 2 · Results from an executed run

`python evaluation/run_tests.py` on this repo, corpus `v1.0.0`, 8 cases × 2 arms, 71 unit/
integration tests green. Provenance = `run_id` inside `evaluation/results/latest.json`, which is
the file these numbers were read from:

| metric (spec §12) | naive (control arm) | **safe (this project)** |
| --- | ---: | ---: |
| `interruption_stop_latency` | **n/a — never stops** | 40.0 ms |
| `recovery_latency` | 1398.9 ms | 1507.1 ms |
| `stale_output_rate` | 1.375 | **0.000** |
| `stale_session_rate` | 1.000 (8/8) | **0.000 (0/8)** |
| `final_state_accuracy` | 0.625 (5/8) | **1.000 (8/8)** |
| `fencing_correctness` | 0.000 (0/3) | **1.000 (3/3)** |

**Read the caveats before quoting anything.** These come from the *deterministic* arm: a
discrete-event replay on a virtual clock, so latencies are **reproducible, not measured
provider timings**. The 40.0 ms stop latency equals `RIME_CLEAR_RTT_MS`, a declared
assumption in `.env.example`, and the ~1.4–1.5 s recovery figure is dominated by the
fixture's own replacement-utterance duration. What is genuinely real in this table: request
identity, invalidation, accept/discard decisions, state commits, event ordering, and the
correctness rates — those are produced by executing `agent/lab.py`. Re-running the evaluation writes a new timestamped
run and rewrites `latest.json`; the numbers above are deterministic, so they do not move.
`python evaluation/run_tests.py --live` with `RIME_API_KEY` set replaces the modelled constants with measured Rime time-to-first-audio (stamped `live-rime-cold` /
`live-rime-warm`, so cached and uncached stay separate as §2 requires).

Two fixtures say the loudest:

- `late_result_01` — the slow tool returns 1.75 s after the user moved on. Naive applies it:
  the booking ends up at **2 people** when the user last said four. Safe discards it and the
  final state stays **4 people**.
- `double_interrupt_01` — A → B → C with two races in flight. Naive leaks 3 obsolete outputs
  from 2 interrupted sessions and lands on the wrong party size; Safe fences both.

`false_speech_01` is the guard fixture: background speech is not a request, and neither arm
may invalidate. That it stays clean is what stops this corpus from flattering us.

## 3 · Setup

```bash
cp .env.example .env            # add RIME_API_KEY (server-side only, spec §21)
./scripts/bootstrap.sh          # venv + deps + tests + one evaluation run
./scripts/dev.sh                # UI on :5173, lab/token server on :8080
```

Nothing in the evaluation path needs a key or the network — the kernel is stdlib-only.
To speak for real:

```bash
python agent/main.py --check    # validates config, prints the exact Rime setup
python agent/main.py dev        # livekit-cli ephemeral room; or `start` with LIVEKIT_* set
python scripts/proof_of_voice.py --repeat 3 --interrupt-at 250   # evidence for RIME_EVIDENCE.md
```

`.venv/bin/pip install -r agent/requirements-live.txt` covers the realtime stack. Python
3.11+, Node 20+.

`./scripts/verify.sh` runs everything the demo rests on: ruff → 71 pytest → the two-arm
evaluation → the reducer smoke (real SSE frames replayed through the TypeScript state layer,
`cd frontend && npm run smoke:reducer`) → `tsc --noEmit` (app + smoke) → eslint → `vite build`.

## 4 · How the mechanism works

**Request/generation fencing (spec §6).** Every user turn gets a monotonic `request_id`;
every tool call, LLM output and TTS batch carries it. Tool results are checked against
`active_request_id`; **speech additionally carries a `generation` counter** bumped on cancel,
so an audio batch already in flight when the barge-in landed is rejected even though its
request id still matches. That is the part a framework will not do for you: LiveKit happily
stops the audio, and then a tool result from the dead turn arrives and mutates your app.

```
fence.owns(request_id)          → ACCEPT    (tool result, state commit)
fence.owns_generation(id, gen)  → ALLOW SPEAK (queued Rime audio)
otherwise                       → DISCARD, and never speak it
```

**Two-step cancellation** (`agent/interruption.py`). `begin()` is synchronous: detect,
invalidate, bump the generation, hand the runtime a cancellation plan. `finish()` is called
*after* the provider confirms silence, and that is where stop latency is measured — so the
number reflects an actual stop rather than a request to stop. A naive caller cannot lie about
it either: the kernel clamps `cancelled=True` to `False` under the naive policy.

**Reconciliation with what was heard** (spec §2's nastiest requirement). Rime's `/ws3`
returns word-level timestamps, so we keep a `PlaybackLedger` and, at the moment of the cut,
record which words were actually audible. The timeline shows
`HEARD RECONCILED — heard "Done. I've noted"`, and the UI strikes the rest through.

**State machine** (`agent/state_manager.py`, spec §7) is explicit and small, with a legal
transition table. Illegal sequencing *raises* rather than recovering silently — in the safe
arm. In the naive arm the same overlap is logged as a `severity=defect` note, because the
whole point of the control arm is to show what the silent version costs you.

**Context IDs are the fence, on the wire.** Each utterance is tagged `req-<id>-gen-<n>` and
sent to Rime as `contextId`, which the API echoes on every `chunk`/`timestamps`/`done`
event. Audio that comes back with a fenced token is dropped. This is the vendor's own
correlation primitive doing the work, not a bookkeeping fiction.

## 5 · Layout

```
agent/
  events.py          timeline model + injectable clock (wall vs virtual)
  state_manager.py   ★ fence, state machine, playback ledger
  interruption.py    ★ two-step detect → confirmed-stop
  lab.py             ★ orchestrator: zero I/O, zero sleeps, time in / decisions out
  tools.py           deterministic intent parse + the deliberately slow tool + state store
  providers.py       livekit-plugins-rime wiring + direct /ws3 + HTTP client + NullRime
  config.py          Rime model/voice/lang validated against the published catalog
  metrics.py         §12 metrics, derived only from recorded events
  main.py            LiveKit worker (AgentServer / rtc_session / function_tool)
  dev_server.py      stdlib token + Lab-mode SSE server
  live_runner.py     --live arm: real clock, real Rime, cold/warm labels
  tests/             71 unit + integration tests
frontend/            Vite + React + TS + Tailwind; livekit-client data channel
                     `npm run smoke:reducer` replays real SSE frames through the TS reducer
evaluation/
  cases.json         v1.0.0 stress corpus with ground truth
  simulate.py        discrete-event replay on a virtual clock
  run_tests.py       both arms → results/*.json + latest.md (+ provenance, assumptions)
  results/           latest.json + latest.md of a real run — the only source of quoted numbers
                     (timestamped runs are gitignored: they are regenerable, the copy is not)
scripts/             bootstrap · dev · verify · proof_of_voice
docs/SPEC.md         the brief, verbatim
```

`lab.py` is the seam that keeps this honest: the live runtime and the simulator call the
**same** methods in the **same** order and differ only in where the clock comes from.

## 6 · Deliberately not built (spec §17)

No accounts, no database, no RAG, no multi-agent graph, no avatar, no model training, no
custom WebRTC, no provider-comparison dashboard, no tenth tool. The voice domain stays a
booking/flight microcosm, and `parse_intent` is a deterministic rule extractor rather than
an LLM call in the *measurement* path — not laziness: ground truth has to be reproducible,
and a sampled model would make naive-vs-safe incomparable. The live agent does use an LLM
for the same slot with the same output contract.

## 7 · Known limitations

- The corpus runs through a **simulated clock**; provider latency is only measured under
  `--live`/the LiveKit runtime. No live provider figures are claimed yet.
- `parse_intent` covers the fixture phrasings (party size, clock time, `X instead of Y`,
  cancellation). It is not a general parser and will misread unusual sentences.
- Stop latency on the HTTP transport measures the local playback gate; true *audible* stop
  latency needs `/ws3` + browser playback, which is what the LiveKit runtime reports through
  `TTSMetrics` (`ttfb`, `cancelled`, `connection_reused`).
- `false_speech` is modelled at the turn-detection layer (the interruption never reaches the
  kernel), so this repo does not itself tune VAD thresholds.
- LiveKit agent code paths are import- and type-verified but not exercisable here without
  credentials, so they are the first thing to smoke-test on your machine.
- Browser bundle is one ~830 kB chunk (mostly `livekit-client`); no code-splitting yet.

## 8 · AI assistance

This repository was scaffolded and written by an AI coding agent (Arena Agent Mode) against
`docs/SPEC.md`, with the human directing scope. Provider APIs were verified against the
installed packages and live vendor docs rather than recalled: `livekit-agents 1.7.1` and
`livekit-plugins-rime 1.7.1` signatures were introspected in-environment, and Rime's
endpoints/protocol/catalog came from `docs.rime.ai` (fetched 2026-09-04). Existing starters
were used only as wiring references; the fence, ledger, fixtures, metrics harness and UI are
original here, per spec §9.

## 9 · License

MIT — see [`LICENSE`](LICENSE) for the third-party table.
