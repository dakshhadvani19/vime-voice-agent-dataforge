/** LiveKit browser transport.
 *
 * Two jobs only: push the microphone up, and read the agent's telemetry off the reliable
 * data channel. Audio playback is the room's own remote track — no custom transport and no
 * play button (spec §2: Rime speech must be essential, not decorative).
 */
import { Room, RoomEvent, type LocalParticipant } from 'livekit-client'

export const TELEMETRY_TOPIC = 'vime.timeline'

export interface TokenResponse {
  token: string
  url: string
  dev?: boolean
}

export async function fetchToken(identity: string): Promise<TokenResponse> {
  const res = await fetch('/api/token', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ identity }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`token endpoint responded ${res.status}. ${detail.slice(0, 200)}`)
  }
  return (await res.json()) as TokenResponse
}

export interface LiveSession {
  room: Room
  disconnect: () => Promise<void>
  participant: LocalParticipant | undefined
}

export async function connectLive(opts: {
  identity: string
  onJson: (payload: unknown) => void
  onError: (message: string) => void
  onStateChange?: (connected: boolean) => void
}): Promise<LiveSession> {
  const { token, url } = await fetchToken(opts.identity)
  // Verified against livekit-client 2.22.2 typings: RoomOptions exposes adaptiveStream;
  // mic mute/device handling stays on SDK defaults rather than guessed option names.
  const room = new Room({ adaptiveStream: true })

  room.on(RoomEvent.DataReceived, (payload: Uint8Array, _p, _kind, topic) => {
    if (topic && topic !== TELEMETRY_TOPIC) return
    try {
      opts.onJson(JSON.parse(new TextDecoder().decode(payload)))
    } catch (err) {
      opts.onError(`malformed telemetry frame: ${(err as Error).message}`)
    }
  })
  room.on(RoomEvent.Disconnected, () => opts.onStateChange?.(false))
  room.on(RoomEvent.Reconnected, () => opts.onStateChange?.(true))

  await room.connect(url, token)
  // setMicrophoneEnabled both requests permission and publishes: one gesture, one truth.
  await room.localParticipant.setMicrophoneEnabled(true)
  opts.onStateChange?.(true)

  return {
    room,
    participant: room.localParticipant,
    disconnect: async () => {
      await room.disconnect()
    },
  }
}
