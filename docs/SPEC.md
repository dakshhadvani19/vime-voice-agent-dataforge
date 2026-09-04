# DataForge × Rime Hackathon
## Coding-Agent Project Brief & Build Specification

Solo build • AI-assisted • ~16-hour target • Prototype-first

> Verbatim copy of the uploaded brief (`DataForge_Rime_Break_My_Voice_Agent_Build_Spec.pdf`),
> pinned in-repo so implementation decisions can always be traced back to it.
> When this file and the implementation disagree, this file wins until the owner says otherwise.

---

## 1. Executive Summary

Build an interactive voice-agent **laboratory**, not a generic voice chatbot. The working concept
is provisionally named **"Break My Voice Agent"**. The user intentionally interrupts a
speaking/reasoning agent or changes a request while an old task is still running. The system must
stop obsolete Rime speech, prevent stale model/tool results from being applied to the current
conversation, process the latest user intent, and visibly prove what happened through an event
timeline and measured metrics.

**Core product claim:** *"A realtime voice agent should never speak or apply a result from a
request that the user has already superseded."*

## 2. Competition Requirements From the Rime PS

- Build a working voice-native product/prototype for a specific user and situation.
- Rime-generated speech must be essential to the experience; a chatbot with a play button is explicitly insufficient.
- Choose one hard voice problem and define an acceptance test **before** the demo.
- Relevant hard problem for this project: **interruption and recovery**.
- During interruption, queued Rime audio should stop promptly, obsolete model/tool results must not
  re-enter the conversation, and application state must remain consistent with what the user actually heard.
- The demo should show: target user/problem, normal flow, hard voice problem, deliberate stress/failure
  case, measurement/result, and active speech provider.
- Submit working code, a working demo link or recording, README, `RIME_EVIDENCE.md`, and
  configuration/secret hygiene.
- Unverified performance numbers receive no credit; cached and uncached measurements should be
  distinguished where relevant.
- Use a current production Rime model/voice/language configuration at submission time and document
  the exact configuration used.

## 3. Proposed Product

**Name:** Break My Voice Agent
**Positioning:** An interactive laboratory for testing interruption safety and recovery in realtime voice agents.

The voice domain should remain simple. **The engineering problem is the product.**

- User speaks naturally to the agent.
- Agent starts processing and/or speaking.
- User interrupts or changes the request.
- Current speech is stopped.
- Previous request becomes obsolete.
- Any late result belonging to the obsolete request is discarded/fenced.
- The new request becomes authoritative.
- Rime speaks only the current valid response.
- The UI exposes the event timeline and measurements.

## 4. Primary User Flow

| Step | Stage | Expected behavior |
| --- | --- | --- |
| 1 | Start | User opens the public demo and presses/taps the microphone. |
| 2 | Request | User says a simple task, e.g. "Find me a flight from Ahmedabad to Delhi tomorrow evening." |
| 3 | Agent | STT → LLM → tool/action → Rime TTS. |
| 4 | Interrupt | While the agent is speaking or a tool is running, user says: "Actually, Mumbai instead of Delhi." |
| 5 | Cancel/Fence | Stop obsolete Rime audio and invalidate/fence the previous request. |
| 6 | Late result | The intentionally delayed old tool may return. The system must detect that it is stale and discard it. |
| 7 | New result | The latest request is processed and its valid result is spoken by Rime. |
| 8 | Explain | The UI shows exactly which events occurred and the measured recovery/stale-output metrics. |

## 5. Naive vs Safe Demonstration

A major differentiator should be a controlled A/B demonstration.

```
NAIVE MODE
Request A → Tool A → user interrupts → Request B → Tool B
Tool A returns late → WRONG: stale result is spoken/applied

SAFE MODE
Request A → Tool A → user interrupts → Request B → Tool B
Tool A returns late → CORRECT: stale result is discarded
```

Use the **same** scenario, **same** delayed tool, and **same** interruption in both modes. This makes
the improvement directly observable instead of relying on a verbal claim.

## 6. Core Technical Idea: Request/Generation Fencing

Every user turn/request gets a monotonically increasing request ID. Tool calls, model outputs, and
speech work carry the request ID that created them. When the user supersedes a request, the active
request ID changes. Any later result whose ID is not the active ID is treated as obsolete.

```
activeRequestId = 42

result.requestId == activeRequestId  → ACCEPT
result.requestId != activeRequestId  → DISCARD / DO NOT SPEAK
```

The implementation must also stop currently playing/queued Rime audio and reconcile application state
with what the user actually heard.

## 7. Suggested State Machine

```
IDLE
  ↓
LISTENING
  ↓
THINKING
  ↓
SPEAKING
  ↓ user interruption
INTERRUPTED
  ↓
CANCELLING / INVALIDATING
  ↓
LISTENING
```

Keep the state machine explicit and small. Avoid hidden orchestration logic spread across UI components.

## 8. Recommended Tech Stack

| Layer | Choice | Reason |
| --- | --- | --- |
| Frontend | React + Vite + TypeScript | Fastest fit for the developer; simple public web demo. |
| Styling | Tailwind CSS | Rapid polished UI. |
| Realtime client | LiveKit client SDK | Browser audio/realtime transport. |
| Agent backend | Python + LiveKit Agents | Use existing realtime voice-agent infrastructure. |
| TTS | Rime | Mandatory primary spoken output for this challenge. |
| STT | A fast streaming provider such as Deepgram, via LiveKit integration | Do not spend hackathon time building STT. |
| LLM | Fast, low-latency hosted LLM | The task does not require a large reasoning model. |
| VAD/turn detection | LiveKit turn detection and/or Silero VAD | Use existing components rather than implementing speech segmentation from scratch. |
| Evaluation | Python scripts + JSON fixtures | Repeatable interruption/stress tests. |
| Charts/UI | Simple SVG/Recharts or equivalent | Timeline, latency and pass/fail visualization. |
| Deployment | Vercel for frontend + simple backend host/LiveKit-compatible deployment | Keep infrastructure minimal. |
| Persistence | None initially | No database/auth unless proven necessary. |

## 9. Open-Source / Existing Building Blocks

- **LiveKit Agents**: use as the realtime voice-agent backbone instead of implementing transport, turn
  handling and orchestration from scratch.
- **LiveKit examples/starters**: use them only as infrastructure starting points; the application logic
  and experiment must be original.
- **Silero VAD**: optional open-source VAD building block through the LiveKit ecosystem.
- **Pipecat**: viable alternative realtime voice framework, but do not switch frameworks unless LiveKit
  becomes blocked.
- **Rime is the required primary TTS provider**; do not replace it with an open-source TTS in the judged path.

**Important:** do not copy a complete existing voice-agent demo and merely rename it. Existing starter
code is infrastructure; the interruption-safety mechanism, experiment design, evaluation fixtures,
metrics and UX should be owned by this project.

## 10. Evaluation Dataset / Test Fixtures

A large public dataset is not required. Create a small, explicit, versioned stress-test corpus. The
dataset should contain ground-truth expected final intent and be easy to replay.

```json
{
  "id": "booking_01",
  "initial": "Book a table for two tomorrow at 8 PM",
  "interrupt": "Actually make it four people at 9",
  "expected": "4 people at 9 PM"
}
```

Coverage required: parameter correction (2→4) · time correction (8 PM→9 PM) · destination correction
(Delhi→Mumbai) · cancellation ("Actually forget it.") · mid-sentence interruption · interruption during
a deliberately slow tool call · double interruption (A→B→C) · late old tool result · false/background
speech where practical.

## 11. Deliberately Slow Tool

Implement one fake/simulated tool whose latency is intentionally controllable. This is preferable to
relying on an unpredictable external API for the stress test.

```python
async def slow_tool(query):
    await sleep(configured_delay)
    return result
```

The tool should expose a visible delay setting or a fixed test delay. During an interruption, the old
tool can finish in the background; the application must prove that its late result is ignored when obsolete.

## 12. Metrics

| Metric | Definition |
| --- | --- |
| Interruption stop latency | time speech stops − time user interruption is detected |
| Recovery latency | time new valid speech starts − time user interruption is detected |
| Stale output rate | obsolete results spoken/applied ÷ interrupted sessions |
| Final-state accuracy | correct final state ÷ total test cases |
| Tool cancellation/fencing correctness | obsolete tool results correctly rejected ÷ obsolete tool results |

**Never invent benchmark numbers.** The application/evaluation script must produce the reported values.
Clearly label cached vs uncached measurements where applicable.

## 13. Event Timeline UI

```
REQUEST #17 CREATED
TOOL #17 STARTED
RIME SPEAKING
USER INTERRUPTION DETECTED
RIME SPEECH CANCELLED ✓
REQUEST #17 INVALIDATED
REQUEST #18 CREATED
TOOL #17 RETURNED → DISCARDED ✓
TOOL #18 RETURNED → ACCEPTED ✓
RIME RESPONSE #18 STARTED
```

The event stream is not merely decorative. It is an observability surface showing the exact causal
sequence that proves the core claim.

## 14. UI / UX Direction

- Landing/experiment view: clear instruction such as "Interrupt me while I'm speaking."
- Live conversation area with microphone status and current agent state.
- Engineering Lens toggle that reveals the event timeline.
- Naive vs Safe mode toggle for the controlled comparison.
- Visible current request ID and status.
- Recovery latency and stale-output status.
- Test Results view with actual pass/fail measurements.
- Do not overload the interface with unrelated product features.

## 15. Architecture

```
Browser
  │
  ▼
LiveKit realtime transport
  │
  ▼
Python LiveKit Agent
  ├── STT
  ├── fast LLM
  ├── request/state manager  ← CORE PROJECT LOGIC
  ├── delayed test tool
  └── Rime TTS
        │
        ▼
     Browser audio

Frontend receives/visualizes:
  request IDs • state transitions • tool events • speech events • metrics
```

## 16. Suggested Repository Structure

```
break-my-voice-agent/
├── frontend/
│   ├── src/
│   ├── components/
│   └── lib/
├── agent/
│   ├── main.py
│   ├── state_manager.py
│   ├── interruption.py
│   ├── tools.py
│   └── providers.py
├── evaluation/
│   ├── cases.json
│   ├── run_tests.py
│   └── results/
├── README.md
├── RIME_EVIDENCE.md
├── .env.example
└── LICENSE
```

## 17. What NOT to Build

- No generic "AI voice assistant" positioning.
- No authentication/accounts/database.
- No large RAG system unless absolutely necessary.
- No multi-agent architecture.
- No model training/fine-tuning.
- No animated avatar as a primary feature.
- No ten-tool agent.
- No huge dataset.
- No custom WebRTC/realtime transport.
- No provider-comparison dashboard as the main product.
- No features that do not strengthen the interruption/recovery claim.

## 18. 16-Hour Build Plan

| Time | Task |
| --- | --- |
| 0–1 | Freeze claim, user flow, acceptance test, architecture. |
| 1–3 | Get LiveKit → STT → LLM → Rime end-to-end voice flow working. |
| 3–5 | Implement request IDs, interruption, Rime cancellation, stale-result fencing. |
| 5–7 | Implement delayed tool and reproducible test fixtures. |
| 7–10 | Build React UI, microphone flow, event timeline, Naive/Safe modes. |
| 10–12 | Implement evaluation metrics and stress tests. |
| 12–13 | Write README and RIME_EVIDENCE.md; document exact provider configuration. |
| 13–14 | Deploy. |
| 14–15 | Record 4–5 minute demo. |
| 15–16 | Bug fixing, secrets/config check, final verification. |

## 19. Acceptance Test

```
Given:
  Request A starts a slow tool call and/or Rime speech.
When:
  User interrupts and changes the request to B.
Then:
  1. Obsolete Rime audio stops promptly.
  2. Request A becomes obsolete.
  3. Request B becomes the active request.
  4. A late result from A is not spoken or applied.
  5. Result B is accepted.
  6. Rime speaks the valid B response.
  7. The UI/event log proves the sequence.
  8. Recovery latency and stale-result outcome are recorded.
```

## 20. Demo Script

- 0:00–0:30 — Explain the problem: voice agents can continue obsolete work after the user changes intent.
- 0:30–1:15 — Normal voice interaction.
- 1:15–2:00 — Interrupt while speaking / during tool work.
- 2:00–2:45 — Show the deliberately delayed old result and prove it is blocked.
- 2:45–3:30 — Run the same case in Naive mode and show the failure.
- 3:30–4:15 — Run Safe mode and show the recovery metrics.
- 4:15–4:45 — Brief architecture and Rime's role.
- Keep the final recording within the 4–5 minute requirement.

## 21. AI Coding-Agent Instructions

The coding agent should treat this document as the high-level product and engineering specification. It
should not expand scope without explicit approval.

- First inspect the project directory and establish the smallest working architecture.
- Prefer official LiveKit/Rime integration patterns and current documentation.
- **Do not invent APIs, package names, Rime model IDs, voices, or LiveKit methods; verify them against
  current official documentation.**
- Build the end-to-end audio path before polishing UI.
- Keep the interruption/state manager isolated and testable.
- Write unit/integration tests for stale-result rejection.
- Create a deterministic delayed tool for reproducible stress tests.
- Keep provider credentials server-side and use `.env.example` placeholders only.
- Make all performance measurements originate from actual code execution.
- Document third-party packages, licenses, provider configuration, known limitations and AI-assisted code.
- Do not add authentication, database, RAG, avatars, multiple agents, or unrelated features unless the
  core acceptance test is already working.

## 22. Definition of Done

- A user can speak to the agent from a public browser demo.
- Rime is the primary spoken output in the judged path.
- User interruption actually stops obsolete speech.
- A deliberately delayed old tool result can be produced.
- The old result is demonstrably rejected after the request is superseded.
- The latest request completes and is spoken.
- Naive and Safe behavior can be compared.
- The UI shows an event timeline.
- The system produces real recovery/stale-output metrics.
- At least one repeatable stress-test fixture exists.
- README and RIME_EVIDENCE.md explain setup, architecture, claim, acceptance test, results and limitations.
- No secrets are committed.
- The demonstrated code exists in the repository.

## 23. Source Basis

Primary source for competition requirements: the uploaded DataForge × Rime Hackathon Challenge brief.
Key requirements include voice being essential, one hard voice problem, measurable acceptance testing,
interruption/recovery behavior, working code/demo, README, evidence, current Rime configuration, and
secret hygiene.

File reference: `turn1file0` (uploaded Rime Hackathon Challenge PDF).
