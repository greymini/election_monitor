import { describe, expect, it } from 'vitest'

import { parseSse } from '../sse'

describe('parseSse', () => {
  it('parses CRLF frames, which is what the API sends', () => {
    const { events, rest } = parseSse('event: token\r\ndata: {"a":1}\r\n\r\nevent: answer\r\ndata: x')
    expect(events).toEqual([{ event: 'token', data: '{"a":1}' }])
    expect(rest).toBe('event: answer\ndata: x')
  })

  it('parses LF frames too', () => {
    expect(parseSse('event: answer\ndata: {"text":"hi"}\n\n').events)
      .toEqual([{ event: 'answer', data: '{"text":"hi"}' }])
  })

  it('defaults the event name and joins multi-line data', () => {
    expect(parseSse('data: one\ndata: two\n\n').events)
      .toEqual([{ event: 'message', data: 'one\ntwo' }])
  })

  it('keeps a frame split across chunks until it is complete', () => {
    const first = parseSse('event: answer\r\ndata: {"te')
    expect(first.events).toEqual([])
    const second = parseSse(first.rest + 'xt":"ok"}\r\n\r\n')
    expect(second.events).toEqual([{ event: 'answer', data: '{"text":"ok"}' }])
  })
})
