# RIME_EVIDENCE.md

Evidence that Rime is the **essential** spoken output of this project — not a play button
appended to a chatbot — plus the exact configuration in use, as the brief requires.

Generated/verified: **2026-09-04** · repo `vime-voice-agent-dataforge` · harness `1.0.0`
Provenance handle for every figure below: the `run_id` and `provenance` block inside
`evaluation/results/latest.json`. Deliberately not copied into prose — `run_tests.py` writes a
new timestamped run on every invocation, so a hand-copied run_id goes stale and a stale
citation is exactly the failure this project exists to prevent.
Status of the live capture: **pending an API key** (see §5). Nothing below is quoted as a
measurement of Rime unless it is inside a filled-in block from `scripts/proof_of_voice.py`.

---

## 1 · Exact Rime configuration in use

Authoritative copy: run `python agent/main.py --check` or `GET :8080/health` — both print
this same object, and `agent/config.py` refuses to start on an unsupported combination.

```json
{
  "model": "coda",
  "speaker": "astra",
  "lang": "en",
  "sample_rate": 22050,
  "speed_alpha": 1.0,
  "reduce_latency": true,
  "transport": "websocket /ws3 (JSON streaming)",
  "segment": "bySentence",
  "endpoint": "wss://users-ws.rime.ai/ws3",
  "region": "global",
  "credential": "env:RIME_API_KEY",
  "package": "livekit-plugins-rime==1.7.1"
}
```

Overridable via `RIME_MODEL` / `RIME_SPEAKER` / `RIME_LANG`. The low-latency alternative
documented by Rime is `"model": "mistv3"` with a Mist-v3-published voice (e.g. `astra`,
`cove`, `peak`) — flip it and re-run §5 so the evidence matches what actually spoke.

**Why this is a current production configuration, checked rather than assumed**

| Item | Value used | How it was confirmed on 2026-09-04 |
| --- | --- | --- |
| Model ids | `coda`, `mistv3`, `mistv2` | introspected `livekit/plugins/rime/models.py`: `TTSModels = Literal['mistv2','mistv3','coda']` |
| Voice `astra` + `lang=en` on `coda` | published pairing | Rime "Coda voices" catalog (162 English voices; `astra` featured) and the "Streaming TTS" example request body |
| `mistv3` as the latency option | "lowest time to first audio" | Rime "TTS in five minutes" |
| HTTP endpoint | `POST https://users.rime.ai/v1/rime-tts` | Rime "Streaming TTS" |
| WS endpoint | `wss://users-ws.rime.ai/ws3` (recommended JSON) | Rime "WebSocket API Overview" |
| Plugin kwargs | `model, speaker, lang, sample_rate, speed_alpha, use_websocket, segment, reduce_latency, api_key` | `inspect.signature(rime.TTS.__init__)` on the installed 1.7.1 |
| Voice/model coupling | each voice serves exactly one language; a bad triple is **not reliably rejected** by the API | Rime "Coda voices" note → hence `agent/config.py` validates locally (`agent/tests/test_config.py`) |

`arcana` is deliberately **not** used: it appears in the LiveKit docs marked deprecated, and
this build accepts only the three ids above.

## 2 · Where Rime speech is load-bearing

Rime is not optional in the judged path, and there is no text-only fallback in it:

1. **The response is spoken.** `agent/main.py` builds the session with
   `tts=build_livekit_tts(cfg)`; the assistant's reply is audio. Removing Rime leaves the
   product with no output channel at all.
2. **Cancellation is a Rime primitive.** Spec §4 step 5 demands obsolete audio *stop*. We use
   Rime's documented `{"operation": "clear"}` on `/ws3` (and the HTTP transport's documented
   "close the connection"), so "stopped" is an action on the vendor's stream, not a UI
   boolean.
3. **Word timestamps drive the heard-reconciliation.** Rime's `timestamps` events
   (`word_timestamps.{words,start,end}`) feed `PlaybackLedger`, which decides what the user
   actually heard at the instant of the cut. No vendor word timing → no
   `HEARD RECONCILED` row.
4. **Context IDs carry the fence.** `req-<id>-gen-<n>` is sent as Rime's `contextId` and
   echoed on each event, so a late audio chunk is provably attributable to a dead request.
5. **`TTSMetrics` supplies live numbers.** `ttfb`, `cancelled`, `streamed`,
   `connection_reused` are forwarded from Rime's own metrics into the same event log the UI
   renders — that is where `--live` latency figures come from.
6. **The STT/LLM are swappable; Rime is not.** `providers.py` exposes Deepgram/OpenAI via
   LiveKit without ceremony, and no alternative TTS is wired in. Per §9 we did not substitute
   an open-source TTS anywhere.

No provider-comparison dashboard exists (§17); Rime is simply the voice.

## 3 · What the Rime path does under interruption (mechanics)

```
Rime SPEAKING #1 (contextId=req-1-gen-1)
  → user barges in                    [INTERRUPTION_DETECTED]
  → generation bumped to gen-2        [REQUEST #1 INVALIDATED]
  → {"operation":"clear"} sent         discards Rime's queued buffer
  → live audio track interrupted       obsolete playback stops
  → any /ws3 chunk still arriving with a fenced contextId is dropped
  → response #2 synthesized and spoken  [RIME RESPONSE #2 STARTED]
```

Replay it frame by frame without a microphone:

```bash
curl -N "localhost:8080/lab/stream?scenario=booking_01&policy=safe&speed=4"
curl -N "localhost:8080/lab/stream?scenario=booking_01&policy=naive&speed=4"   # control arm
```

## 4 · Demo checklist (spec §2), mapped

| Required in the demo | Where it happens |
| --- | --- |
| Target user and situation | README §1 + app header: developers/operators of realtime voice agents; a user who changes their mind mid-utterance |
| Normal flow | Lab fixture `booking_01`, or live mic without interrupting |
| The hard voice problem | Interruption and recovery — stated on the landing screen ("interrupt me while I'm speaking") |
| Deliberate stress/failure case | `late_result_01` (slow tool lands 1.75 s after the user moved on) and `double_interrupt_01` (A→B→C) |
| Measurement / result | Metrics panel + Test Results tab, both read from `evaluation/results/latest.json` |
| Active speech provider | Header badge `speech: rime {coda/astra}`, `GET :8080/health`, and the §1 block above |
| Working demo | `./scripts/dev.sh` → :5173 (Lab mode needs no keys; Live mode needs LiveKit + Rime creds) |
| Working code in repo | `agent/`, `frontend/`, `evaluation/` — committed, MIT-licensed |
| Secret hygiene | only `.env.example` placeholders; `RIME_EVIDENCE` never contains a key value; `agent/tests/test_corpus.py::TestSecretHygiene` scans the tree in CI |

## 5 · Live capture — run this and paste it here

Do this **before recording the demo**. Unverified performance numbers receive no credit, so
this section stays a template until it contains output produced by a real call.

```bash
cp .env.example .env          # set RIME_API_KEY
.venv/bin/python agent/main.py --check
.venv/bin/python scripts/proof_of_voice.py --repeat 3 --interrupt-at 250 --json
.venv/bin/python evaluation/run_tests.py --live --repeat 3
```

The last command re-stamps every figure `live-rime-cold` / `live-rime-warm` instead of
`simulated`, which is the cached-vs-uncached distinction §2 asks for. Then fill in:

```
### 5.1 Synthesis proof
<!-- paste agent/media/rime-proof.json here: TTFB per pass, bytes, files -->
<!-- audio artefacts: agent/media/rime-proof-{1,2,3}.wav  (gitignored; attach to the demo) -->

### 5.2 Interruption proof
<!-- paste the "abort@" line: bytes received before the stream was closed, and TTFB -->
<!-- and record a 15 s screen clip: agent mid-sentence → user speaks → audio stops <X> ms later -->

### 5.3 Measured metrics (live arm)
<!-- paste the [SAFE] block from `run_tests.py --live`, with its run_id -->
<!-- report n and spread, not a lone mean; keep the [NAIVE] block for the A/B -->

### 5.4 Dashboard of the run
<!-- run_id, git sha, python version, platform: all in results/latest.json under "provenance" -->
```

Numbers currently in this repository, for the record, and their provenance:

| Figure | Value | Provenance |
| --- | --- | --- |
| corpus correctness (safe) | stale 0/8, accuracy 8/8, fencing 3/3 | **executed** — `evaluation/results/latest.json` (see its `run_id` + `provenance` for the exact run) |
| corpus correctness (naive) | stale 11/8, accuracy 5/8, fencing 0/3 | **executed** — same run, control arm (same fixture files, same event order) |
| stop latency 40.0 ms / recovery ≈1.5 s | 40.0 / 1507.1 | **modelled** on a virtual clock: 40.0 is `RIME_CLEAR_RTT_MS` from `.env.example`; the rest is fixture utterance length. Reproducible, *not* a Rime measurement |
| Rime TTFB, audible stop latency | not yet measured | blocked on `RIME_API_KEY` — belongs in §5.1–5.3 |

## 6 · Honest boundaries

- The judged live path is Rime, but the *numbers in this file so far* come from the
  deterministic kernel, not from Rime. That distinction is maintained in every artifact:
  metrics carry a `source` field (`simulated` / `live-rime-cold` / `live-rime-warm` /
  `live-clock-no-provider`) and the UI prints it next to each figure.
- `proof_of_voice.py` and `run_tests.py --live` deliberately **skip** with exit 0 and a
  reproducible `curl` when no key is present, rather than emitting synthetic "measured"
  values. A run without credentials can never silently look like a run with them.
- Latency depends on region: set `RIME_REGION` and use the nearest regional endpoint before
  quoting any timing to a judge.
