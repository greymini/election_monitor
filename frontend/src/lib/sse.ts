/**
 * Server-sent-event framing for the chat stream.
 *
 * sse-starlette (the API's library) ends lines with CRLF, so a frame is
 * `event: x\r\ndata: {...}\r\n\r\n`. Splitting on "\n\n" never matched a CRLF
 * stream, every frame stayed in the buffer, and live chat displayed nothing.
 */
export interface SseEvent {
  event: string
  data: string
}

/** Complete events in `buffer`, and the incomplete remainder to keep. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const normalised = buffer.replace(/\r\n?/g, '\n')
  const frames = normalised.split('\n\n')
  const rest = frames.pop() ?? ''
  const events: SseEvent[] = []
  for (const frame of frames) {
    let event = 'message'
    const data: string[] = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
    }
    if (data.length) events.push({ event, data: data.join('\n') })
  }
  return { events, rest }
}
