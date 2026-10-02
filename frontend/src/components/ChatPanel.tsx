import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { formatDetail, getToken, setSession } from '../lib/api'
import { parseSse } from '../lib/sse'

interface ToolEvent { name: string; is_error: boolean; seconds: number; preview: string }
interface Turn {
  role: 'user' | 'assistant'
  content: string
  tools?: ToolEvent[]
  charts?: unknown[]
  cost?: number | null
  notice?: string
  truncated?: boolean
}

/** Docked chat panel. Streams over SSE so a slow analysis shows progress
 *  instead of a spinner, and every tool call is visible - the panel is the
 *  audit trail for how an answer was produced. */
export default function ChatPanel({ isAdmin, onClose }: { isAdmin?: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, busy])

  async function send() {
    const message = input.trim()
    if (!message || busy) return
    setInput('')
    setBusy(true)

    const history = turns.slice(-12).map((turn) => ({ role: turn.role, content: turn.content }))
    setTurns((prev) => [...prev, { role: 'user', content: message }])

    const tools: ToolEvent[] = []
    const charts: unknown[] = []

    try {
      const response = await fetch(`${import.meta.env.VITE_API_BASE ?? '/api'}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        },
        body: JSON.stringify({ message, history }),
      })
      // A JSON error body is not a stream: without this check a 401, 403 or
      // 500 was parsed as SSE, nothing was shown and the session never ended.
      if (response.status === 401) {
        setSession(null)
        window.dispatchEvent(new CustomEvent('giridih:unauthorised'))
        throw new Error('Session expired. Please sign in again.')
      }
      if (!response.ok) {
        let detail = `Request failed (${response.status})`
        try {
          detail = formatDetail((await response.json()).detail) ?? detail
        } catch {
          /* non-JSON body */
        }
        throw new Error(detail)
      }
      if (!response.body) throw new Error('No response stream')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        // On the last read, flush the decoder and terminate any final frame
        // that arrived without its blank line, rather than dropping it.
        buffer += done ? `${decoder.decode()}\n\n` : decoder.decode(value, { stream: true })
        const parsed = parseSse(buffer)
        buffer = parsed.rest
        for (const { event, data } of parsed.events) {
          let payload: Record<string, unknown> = {}
          try {
            payload = JSON.parse(data)
          } catch {
            continue
          }

          if (event === 'tool') tools.push(payload as unknown as ToolEvent)
          else if (event === 'chart') charts.push(payload)
          else if (event === 'answer') {
            setTurns((prev) => [...prev, {
              role: 'assistant',
              content: String(payload.text ?? ''),
              tools: [...tools],
              charts: [...charts],
              cost: (payload.cost_usd as number | null) ?? null,
              notice: (payload.notice as string) || undefined,
              truncated: Boolean(payload.truncated),
            }])
          } else if (event === 'error') {
            setTurns((prev) => [...prev, {
              role: 'assistant',
              content: String(payload.message ?? 'The assistant could not answer that.'),
            }])
          }
        }
        if (done) break
      }
    } catch (error) {
      setTurns((prev) => [...prev, {
        role: 'assistant',
        content: error instanceof Error ? error.message : 'Network error.',
      }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card flex h-[calc(100vh-6rem)] flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2"
           style={{ borderColor: 'var(--gridline)' }}>
        <span className="text-sm font-semibold">{t('chat.title')}</span>
        <button className="btn px-2 py-0.5 text-2xs" onClick={onClose}>
          {t('common.close')}
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 space-y-3 overflow-auto px-3 py-3">
        {!turns.length && (
          <p className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {t('chat.disclaimer')}
          </p>
        )}
        {turns.map((turn, index) => (
          <div key={index}>
            <div className="text-2xs font-semibold uppercase tracking-wide"
                 style={{ color: 'var(--text-muted)' }}>
              {turn.role === 'user' ? 'You' : t('chat.title')}
            </div>
            {turn.tools?.length ? (
              <div className="mb-1 flex flex-wrap gap-1">
                {turn.tools.map((tool, i) => (
                  <span key={i} className="chip" title={tool.preview}
                        style={tool.is_error ? { color: 'var(--status-critical)' } : undefined}>
                    {tool.is_error ? '×' : '✓'} {t('chat.toolRan', { name: tool.name })} · {tool.seconds}s
                  </span>
                ))}
              </div>
            ) : null}
            <div className="whitespace-pre-wrap rounded-lg px-2.5 py-2 text-sm"
                 style={{ background: turn.role === 'user' ? 'var(--surface-2)' : 'transparent' }}>
              {turn.content}
            </div>
            {turn.notice && (
              <p className="mt-1 text-2xs" style={{ color: 'var(--status-warning)' }}>
                ! {turn.notice}
              </p>
            )}
            {turn.truncated && (
              <p className="mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
                The assistant ran out of tool rounds; narrow the question for a fuller answer.
              </p>
            )}
            {isAdmin && turn.cost != null && (
              <p className="tnum mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
                ≈ ${turn.cost.toFixed(4)}
              </p>
            )}
          </div>
        ))}
        {busy && (
          <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('chat.thinking')}</div>
        )}
      </div>

      <div className="border-t p-2" style={{ borderColor: 'var(--gridline)' }}>
        <div className="flex gap-1.5">
          <textarea
            className="field min-h-[2.5rem] flex-1 resize-none"
            rows={2}
            value={input}
            placeholder={t('chat.placeholder')}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <button className="btn btn-primary self-end" onClick={() => void send()} disabled={busy}>
            {t('chat.send')}
          </button>
        </div>
      </div>
    </div>
  )
}
