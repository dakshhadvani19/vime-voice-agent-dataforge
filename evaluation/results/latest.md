# Evaluation run `20260904T091222Z-simulated`

- mode: **simulated** (virtual clock (discrete-event); reproducible, provider latency modelled)
- generated: 2026-09-04T09:12:22+00:00
- git: `6ac4326b8e83e1dc566de85e2cee123834eadf5f` · python 3.11.2 · Linux 6.1.158+ x86_64
- fixtures: `evaluation/cases.json` v1.0.0 · 8 cases × 2 arms
- Rime config: `{"model": "coda", "speaker": "astra", "lang": "en", "sample_rate": 22050, "speed_alpha": 1.0, "reduce_latency": true, "transport": "websocket /ws3 (JSON streaming)", "segment": "bySentence", "endpoint": "wss://users-ws.rime.ai/ws3", "region": "global", "credential": "env:RIME_API_KEY", "package": "livekit-plugins-rime==1.7.1"}`

Latency constants below are **declared inputs** for `simulated` mode, not measurements.
Only a `live` mode row may be quoted as measured behaviour.

## Aggregated

| metric | naive (control arm) | safe (this project) |
| --- | ---: | ---: |
| `interruption_stop_latency` | n/a | 40.0 ms |
| `recovery_latency` | 1398.9 ms | 1507.1 ms |
| `stale_output_rate` | 1.375 (11/8) | 0.000 (0/8) |
| `stale_session_rate` | 1.000 (8/8) | 0.000 (0/8) |
| `final_state_accuracy` | 0.625 (5/8) | 1.000 (8/8) |
| `fencing_correctness` | 0.000 (0/3) | 1.000 (3/3) |

## Per case

| case | arm | stop latency | recovery | stale outputs | final state | fencing | verdict |
| --- | --- | ---: | ---: | ---: | :---: | ---: | :---: |
| `booking_01` | naive | n/a | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `booking_01` | safe | 40.0 ms | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `flight_01` | naive | n/a | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `flight_01` | safe | 40.0 ms | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `late_result_01` | naive | n/a | 1550.0 ms | n/a | ✗ | n/a | PASS |
| `late_result_01` | safe | n/a | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `mid_sentence_01` | naive | n/a | n/a | n/a | ✓ | n/a | PASS |
| `mid_sentence_01` | safe | 40.0 ms | n/a | n/a | ✓ | n/a | PASS |
| `double_interrupt_01` | naive | n/a | 1171.2 ms | n/a | ✗ | n/a | PASS |
| `double_interrupt_01` | safe | 40.0 ms | 1550.0 ms | n/a | ✓ | n/a | PASS |
| `cancel_01` | naive | n/a | 1400.0 ms | n/a | ✓ | n/a | PASS |
| `cancel_01` | safe | 40.0 ms | 1400.0 ms | n/a | ✓ | n/a | PASS |
| `cancel_during_tool_01` | naive | n/a | 1400.0 ms | n/a | ✗ | n/a | PASS |
| `cancel_during_tool_01` | safe | n/a | 1400.0 ms | n/a | ✓ | n/a | PASS |
| `false_speech_01` | naive | n/a | n/a | n/a | ✓ | n/a | PASS |
| `false_speech_01` | safe | n/a | n/a | n/a | ✓ | n/a | PASS |

## Event timelines

### `booking_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
barge-in observed but no cancel issued (naive policy): obsolete speech keeps playing to completion
new turn began while speaking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
STATE → thinking
RIME RESPONSE #1 COMPLETED
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `booking_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → cancelling
RIME SPEECH CANCELLED ✓ #1
HEARD STATE RECONCILED #1
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `flight_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
barge-in observed but no cancel issued (naive policy): obsolete speech keeps playing to completion
new turn began while speaking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
STATE → thinking
RIME RESPONSE #1 COMPLETED
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `flight_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → cancelling
RIME SPEECH CANCELLED ✓ #1
HEARD STATE RECONCILED #1
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `late_result_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
new turn began while thinking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
stale result for #1 clobbered state owned by #2
RIME RESPONSE #1 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
RIME RESPONSE #1 COMPLETED
```

### `late_result_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
TOOL #1 STARTED
TOOL #1 RETURNED → DISCARDED ✓
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `mid_sentence_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
barge-in observed but no cancel issued (naive policy): obsolete speech keeps playing to completion
RIME RESPONSE #1 COMPLETED
REQUEST #1 COMPLETED
STATE → listening
```

### `mid_sentence_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → cancelling
RIME SPEECH CANCELLED ✓ #1
HEARD STATE RECONCILED #1
STATE → listening
```

### `double_interrupt_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
new turn began while thinking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
USER INTERRUPTION DETECTED
barge-in observed but no cancel issued (naive policy): obsolete speech keeps playing to completion
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
stale result for #1 clobbered state owned by #2
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
STATE → thinking
STATE → speaking
RIME RESPONSE #1 STARTED
new turn began while speaking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #3 CREATED
STATE → thinking
TOOL #3 STARTED
TOOL #3 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #3 STARTED
RIME RESPONSE #1 COMPLETED
STATE → listening
RIME RESPONSE #3 COMPLETED
REQUEST #3 COMPLETED
```

### `double_interrupt_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
USER INTERRUPTION DETECTED
REQUEST #2 INVALIDATED
STATE → interrupted
STATE → cancelling
RIME SPEECH CANCELLED ✓ #2
HEARD STATE RECONCILED #2
STATE → listening
TOOL #1 STARTED
TOOL #1 RETURNED → DISCARDED ✓
REQUEST #3 CREATED
STATE → thinking
TOOL #3 STARTED
TOOL #3 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #3 STARTED
RIME RESPONSE #3 COMPLETED
REQUEST #3 COMPLETED
STATE → listening
```

### `cancel_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
barge-in observed but no cancel issued (naive policy): obsolete speech keeps playing to completion
new turn began while speaking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
RIME RESPONSE #1 COMPLETED
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `cancel_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → cancelling
RIME SPEECH CANCELLED ✓ #1
HEARD STATE RECONCILED #1
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `cancel_during_tool_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
new turn began while thinking: previous request's work was never cancelled, so its late result can still mutate state
REQUEST #2 CREATED
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
stale result for #1 clobbered state owned by #2
RIME RESPONSE #1 STARTED
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
RIME RESPONSE #1 COMPLETED
```

### `cancel_during_tool_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
USER INTERRUPTION DETECTED
REQUEST #1 INVALIDATED
STATE → interrupted
STATE → listening
REQUEST #2 CREATED
STATE → thinking
TOOL #2 STARTED
TOOL #2 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #2 STARTED
TOOL #1 STARTED
TOOL #1 RETURNED → DISCARDED ✓
RIME RESPONSE #2 COMPLETED
REQUEST #2 COMPLETED
STATE → listening
```

### `false_speech_01` — naive

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
background speech below interruption threshold: request NOT invalidated
RIME RESPONSE #1 COMPLETED
REQUEST #1 COMPLETED
STATE → listening
```

### `false_speech_01` — safe

```
STATE → listening
REQUEST #1 CREATED
STATE → thinking
TOOL #1 STARTED
TOOL #1 RETURNED → ACCEPTED ✓
STATE → speaking
RIME RESPONSE #1 STARTED
background speech below interruption threshold: request NOT invalidated
RIME RESPONSE #1 COMPLETED
REQUEST #1 COMPLETED
STATE → listening
```
