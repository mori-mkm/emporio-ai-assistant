import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, sendMessage, startSession, type Source } from './api'

type Message = {
  id: number
  role: 'customer' | 'assistant'
  text: string
  sources?: Source[]
  simulated?: boolean
}
type Problem = { message: string; action: 'retry' | 'restart' | 'start' }

// Errors that only a new conversation can fix.
const RESTART_CODES = new Set([
  'session_not_found',
  'session_expired',
  'data_changed',
  'conversation_limit',
  'conversation_too_long',
])
const MAX_CHARS = 2000

export default function App() {
  const [ready, setReady] = useState(false)
  const [simulated, setSimulated] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const epoch = useRef(0) // bumps on every new conversation; late replies of older ones are dropped
  const sending = useRef(false)
  const retryId = useRef<string | null>(null) // reused only after a network failure
  const nextId = useRef(0)
  const input = useRef<HTMLTextAreaElement>(null)
  const logEnd = useRef<HTMLDivElement>(null)

  function begin() {
    epoch.current++
    sending.current = false
    retryId.current = null
    setReady(false)
    setBusy(false)
    setMessages([])
    setDraft('')
    setProblem(null)
    setAnnouncement('Iniciando conversa…')
    void connect(epoch.current)
  }

  // Async only: state updates happen after the request, never synchronously in the mount effect.
  async function connect(current: number) {
    try {
      const info = await startSession()
      if (current !== epoch.current) return
      setSimulated(info.simulated)
      setReady(true)
      setAnnouncement('Nova conversa iniciada.')
      input.current?.focus()
    } catch (error) {
      if (current !== epoch.current) return
      setAnnouncement('')
      setProblem({ message: messageOf(error), action: 'start' })
    }
  }

  const connected = useRef(false)
  useEffect(() => {
    if (connected.current) return // StrictMode runs effects twice in development
    connected.current = true
    void connect(epoch.current)
  }, [])

  useEffect(() => {
    logEnd.current?.scrollIntoView?.({ block: 'end' })
  }, [messages, busy])

  async function send() {
    const text = draft.trim()
    if (!ready || sending.current || !text) return
    sending.current = true
    const current = epoch.current
    const requestId = retryId.current ?? crypto.randomUUID()
    retryId.current = requestId
    const pendingId = ++nextId.current
    setMessages((m) => [...m, { id: pendingId, role: 'customer', text }])
    setBusy(true)
    setProblem(null)
    setAnnouncement('Enviando mensagem. O assistente está respondendo…')
    try {
      const reply = await sendMessage(text, requestId)
      if (current !== epoch.current) return
      retryId.current = null
      setDraft('')
      setMessages((m) => [
        ...m,
        {
          id: ++nextId.current,
          role: 'assistant',
          text: reply.text,
          sources: reply.sources,
          simulated: reply.mode === 'simulated',
        },
      ])
      setAnnouncement('')
    } catch (error) {
      if (current !== epoch.current) return
      // Keep the text in the box; drop the unsent bubble so the log shows only delivered turns.
      setMessages((m) => m.filter((msg) => msg.id !== pendingId))
      const network = error instanceof ApiError && error.network
      if (!network) retryId.current = null
      const restart = error instanceof ApiError && RESTART_CODES.has(error.code)
      setAnnouncement('')
      setProblem({ message: messageOf(error), action: restart ? 'restart' : 'retry' })
    } finally {
      if (current === epoch.current) {
        sending.current = false
        setBusy(false)
        input.current?.focus()
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void send()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send()
    }
  }

  function onDraftChange(value: string) {
    setDraft(value)
    retryId.current = null // edited text is a new request
  }

  const canSend = ready && !busy && draft.trim().length > 0

  return (
    <div className="page">
      <header className="brand">
        <span className="brand-mark" aria-hidden="true">
          ♪
        </span>
        <div className="brand-text">
          <h1>Empório da Música</h1>
          <p className="tagline">Atendimento virtual</p>
        </div>
        <button type="button" className="secondary" onClick={begin}>
          Nova conversa
        </button>
      </header>

      {simulated && (
        <p className="simulated-banner" role="note">
          <strong>Modo simulado:</strong> as respostas vêm de um roteiro de teste. Nenhum modelo de
          IA é chamado e elas não representam o atendimento real.
        </p>
      )}

      <main className="chat">
        <div className="log" role="log" aria-live="polite" aria-label="Conversa">
          {messages.length === 0 && ready && (
            <p className="empty">
              Olá! Pergunte sobre instrumentos, preços, disponibilidade ou políticas da loja.
            </p>
          )}
          {messages.map((m) => (
            <article key={m.id} className={`message ${m.role}`}>
              <h2 className="visually-hidden">{m.role === 'customer' ? 'Você' : 'Assistente'}</h2>
              {m.simulated && <span className="badge">Resposta simulada</span>}
              <p className="text">{m.text}</p>
              {m.sources && m.sources.length > 0 && (
                <p className="sources">
                  Fontes do manual:{' '}
                  {m.sources.map((s) => `§${s.section} ${s.title} (p. ${s.pages})`).join('; ')}
                </p>
              )}
            </article>
          ))}
          {busy && (
            <p className="typing" aria-hidden="true">
              O assistente está respondendo…
            </p>
          )}
          <div ref={logEnd} />
        </div>

        <p className="visually-hidden" role="status">
          {announcement}
        </p>

        {problem && (
          <div className="problem" role="alert">
            <p>{problem.message}</p>
            {problem.action === 'retry' ? (
              <button type="button" onClick={() => void send()} disabled={!draft.trim()}>
                Tentar novamente
              </button>
            ) : (
              <button type="button" onClick={begin}>
                {problem.action === 'start' ? 'Tentar novamente' : 'Nova conversa'}
              </button>
            )}
          </div>
        )}

        <form className="composer" onSubmit={onSubmit}>
          <label htmlFor="message">Sua mensagem</label>
          <textarea
            id="message"
            ref={input}
            value={draft}
            maxLength={MAX_CHARS}
            rows={3}
            readOnly={busy}
            aria-describedby="message-hint"
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="composer-row">
            <span id="message-hint" className="hint">
              Enter envia · Shift+Enter quebra linha
            </span>
            <button type="submit" disabled={!canSend}>
              {busy ? 'Enviando…' : 'Enviar'}
            </button>
          </div>
        </form>
      </main>
    </div>
  )
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Algo deu errado. Tente novamente.'
}
