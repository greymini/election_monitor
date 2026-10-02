import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, downloadCsv, formatDetail, toCsv } from '../api'
import { mockServer } from '../../test/server'

afterEach(() => vi.restoreAllMocks())

describe('api.get', () => {
  it('sends the stored token', async () => {
    const server = mockServer({ '/x': { body: { ok: true } } })
    localStorage.setItem('giridih.token', 'tok')
    await api.get('/x')
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].headers.Authorization)
      .toBe('Bearer tok')
    void server
  })

  it('ends the session on a 401', async () => {
    mockServer({ '/x': { status: 401, body: { detail: 'expired' } } })
    localStorage.setItem('giridih.token', 'tok')
    const fired = vi.fn()
    window.addEventListener('giridih:unauthorised', fired)
    await expect(api.get('/x')).rejects.toThrow(/session expired/i)
    expect(localStorage.getItem('giridih.token')).toBeNull()
    expect(fired).toHaveBeenCalled()
  })

  it('surfaces the server detail on other errors', async () => {
    mockServer({ '/x': { status: 403, body: { detail: 'This view needs one of: admin' } } })
    await expect(api.get('/x')).rejects.toThrow('This view needs one of: admin')
  })
})

describe('formatDetail', () => {
  it('reads strings and validation lists', () => {
    expect(formatDetail('plain')).toBe('plain')
    expect(formatDetail([{ msg: 'a' }, { msg: 'b' }])).toBe('a; b')
    expect(formatDetail({ odd: true })).toBeNull()
  })
})

describe('toCsv', () => {
  it('quotes commas, quotes and newlines, and leaves NULL empty', () => {
    expect(toCsv([{ a: 'x,y', b: 'say "hi"', c: null, d: 3 }]))
      .toBe('a,b,c,d\r\n"x,y","say ""hi""",,3')
  })
})

describe('downloadCsv', () => {
  it('tells the user when an export fails instead of doing nothing', async () => {
    mockServer({ '/acs/32/rolls/changes': { status: 500, body: { detail: 'boom' } } })
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await downloadCsv('/acs/32/rolls/changes', 'x.csv')
    expect(alert).toHaveBeenCalledWith(expect.stringMatching(/export failed/i))
  })
})
