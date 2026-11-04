import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, sendMessage, startSession, type Source } from './api'

type Message = {
  id: number
  role: 'customer' | 'assistant'
  text: string
  sources?: Source[]
  simulated?: boolean
}
// Where a question came from. Both use the same send path and the same session.
type Origin = 'composer' | 'shortcut'
type Problem = {
  message: string
  code?: string
  action: 'retry' | 'restart' | 'start'
  retry?: { text: string; origin: Origin }
}

// Errors that only a new conversation can fix.
const RESTART_CODES = new Set([
  'session_not_found',
  'session_expired',
  'data_changed',
  'conversation_limit',
  'conversation_too_long',
])
// The subset that means the conversation is full (as opposed to lost or outdated).
const LIMIT_CODES = new Set(['conversation_limit', 'conversation_too_long'])
const MAX_CHARS = 2000

// Only categories that have products in the challenge catalog (Guitarras 5, Baixos 4, Violões 35,
// Ukuleles 15, Teclados e Pianos 3, Baterias e Percussão 3). The empty wind/orchestral ones are left out.
const CATEGORIES = [
  { label: 'Violões', question: 'Quais violões vocês têm disponíveis?' },
  { label: 'Guitarras', question: 'Quais guitarras vocês têm disponíveis?' },
  { label: 'Baixos', question: 'Quais baixos vocês têm disponíveis?' },
  { label: 'Ukuleles', question: 'Quais ukuleles vocês têm disponíveis?' },
  { label: 'Teclados e Pianos', question: 'Quais teclados e pianos vocês têm disponíveis?' },
  { label: 'Baterias e Percussão', question: 'Quais baterias e itens de percussão vocês têm disponíveis?' },
]

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
  // The request id is reused only to resend the same text from the same place after a network failure.
  const retry = useRef<{ text: string; origin: Origin; requestId: string } | null>(null)
  const nextId = useRef(0)
  const input = useRef<HTMLTextAreaElement>(null)
  const restartButton = useRef<HTMLButtonElement>(null)
  const chat = useRef<HTMLElement>(null)
  const log = useRef<HTMLDivElement>(null)

  // `keepTyped`: a restart forced by an error keeps what the customer typed;
  // the "Nova conversa" button is an explicit reset and clears everything.
  function begin(keepTyped = false) {
    epoch.current++
    sending.current = false
    retry.current = null
    setReady(false)
    setBusy(false)
    setMessages([])
    if (!keepTyped) setDraft('')
    setProblem(null)
    setAnnouncement('Iniciando conversa…')
    void connect(epoch.current, true)
  }

  // Async only: state updates happen after the request, never synchronously in the mount effect.
  // `focus`: only after a restart; the first load does not move the page or open a keyboard.
  async function connect(current: number, focus = false) {
    try {
      const info = await startSession()
      if (current !== epoch.current) return
      setSimulated(info.simulated)
      setReady(true)
      setAnnouncement('Nova conversa iniciada.')
      if (focus) input.current?.focus({ preventScroll: true })
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

  // Follow the conversation inside the history only, and bring the whole panel into view once
  // there is something to read or an alert to act on. The first, empty screen never moves.
  useEffect(() => {
    if (messages.length === 0 && !busy && !problem) return
    const box = log.current
    if (box) box.scrollTop = box.scrollHeight
    chat.current?.scrollIntoView?.({ block: 'nearest' })
  }, [messages, busy, problem])

  const needsRestart = problem?.action === 'restart'
  // The composer is replaced by the restart area; keep keyboard users on the only action left.
  useEffect(() => {
    if (needsRestart) restartButton.current?.focus({ preventScroll: true })
  }, [needsRestart])

  async function send(raw: string, origin: Origin) {
    const text = raw.trim()
    if (!ready || sending.current || needsRestart || !text) return
    sending.current = true
    const current = epoch.current
    const previous = retry.current
    const requestId =
      previous && previous.text === text && previous.origin === origin
        ? previous.requestId
        : crypto.randomUUID()
    retry.current = { text, origin, requestId }
    const pendingId = ++nextId.current
    setMessages((m) => [...m, { id: pendingId, role: 'customer', text }])
    setBusy(true)
    setProblem(null)
    setAnnouncement('Enviando mensagem. O assistente está respondendo…')
    let restart = false
    try {
      const reply = await sendMessage(text, requestId)
      if (current !== epoch.current) return
      retry.current = null
      if (origin === 'composer') setDraft('')
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
      // Typed text stays in the box; drop the unsent bubble so the log shows only delivered turns.
      setMessages((m) => m.filter((msg) => msg.id !== pendingId))
      const network = error instanceof ApiError && error.network
      if (!network) retry.current = null
      const code = error instanceof ApiError ? error.code : undefined
      restart = code !== undefined && RESTART_CODES.has(code)
      setAnnouncement('')
      setProblem({
        message: messageOf(error),
        code,
        action: restart ? 'restart' : 'retry',
        retry: { text, origin },
      })
    } finally {
      if (current === epoch.current) {
        sending.current = false
        setBusy(false)
        if (!restart) input.current?.focus({ preventScroll: origin !== 'composer' })
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void send(draft, 'composer')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send(draft, 'composer')
    }
  }

  // Retry resends the message box (it may have been edited) or the shortcut's question.
  const retryText = problem?.retry?.origin === 'composer' ? draft : (problem?.retry?.text ?? '')
  const canAsk = ready && !busy && !needsRestart
  const canSend = canAsk && draft.trim().length > 0
  const limitReached = needsRestart && LIMIT_CODES.has(problem?.code ?? '')

  return (
    <div className="site">
      <a className="skip-link" href="#message">
        Ir para o campo de mensagem
      </a>
      <div className="top-strip">
        <p className="container top-strip-row">
          <span>Empório da Música · Campo Grande/MS</span>
          <span className="demo-tag">Demonstração</span>
        </p>
      </div>

      <header className="masthead">
        <div className="container masthead-row">
          <h1 className="logo">
            <span className="logo-word">Empório</span>
            <span className="logo-tag">da Música</span>
            <span className="visually-hidden"> — atendimento virtual</span>
          </h1>
          <nav className="categories" aria-label="Perguntar por categoria">
            <ul>
              {CATEGORIES.map((c) => (
                <li key={c.label}>
                  <button
                    type="button"
                    className="category"
                    disabled={!canAsk}
                    aria-label={`Perguntar sobre ${c.label}`}
                    onClick={() => void send(c.question, 'shortcut')}
                  >
                    {c.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>

      <section className="topics" aria-label="Com o que o assistente ajuda">
        <ul className="container">
          <li>
            <TagIcon />
            Instrumentos e preços
          </li>
          <li>
            <CardIcon />
            Pagamento e parcelamento
          </li>
          <li>
            <BoxIcon />
            Trocas, entregas e garantia
          </li>
        </ul>
      </section>

      <div className="container layout">
        <main className="chat" ref={chat} aria-labelledby="chat-title">
          <div className="chat-head">
            <h2 id="chat-title">Atendimento Empório</h2>
            {!needsRestart && (
              <button type="button" className="new-conversation" onClick={() => begin()}>
                Nova conversa
              </button>
            )}
          </div>

          {simulated && (
            <details className="simulated-note">
              <summary>Modo simulado · respostas de teste, sem chamada à IA.</summary>
              <p>
                As respostas vêm de um roteiro de teste do servidor. Nenhum modelo de IA é chamado e
                elas não representam o atendimento real.
              </p>
            </details>
          )}

          <div className="log" ref={log} role="log" aria-live="polite" aria-label="Conversa" tabIndex={0}>
            {messages.length === 0 && ready && (
              <div className="empty">
                <p className="empty-title">Encontre seu próximo instrumento.</p>
                <p>Tire dúvidas sobre produtos, preços, pagamentos e políticas da loja.</p>
                <p className="empty-hint">Escreva sua pergunta abaixo ou escolha um instrumento no topo.</p>
              </div>
            )}
            {messages.map((m) => (
              <article key={m.id} className={`message ${m.role}`}>
                <h3 className="author">{m.role === 'customer' ? 'Você' : 'Assistente Empório'}</h3>
                {m.simulated && <span className="badge">Resposta simulada</span>}
                <p className="text">{m.text}</p>
                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    <p className="sources-title">Fontes do manual</p>
                    <ul>
                      {m.sources.map((s) => (
                        <li key={`${s.section}-${s.pages}`}>
                          §{s.section} {s.title} · p. {s.pages}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </article>
            ))}
            {busy && (
              <p className="typing" aria-hidden="true">
                O assistente está respondendo<span className="dots">…</span>
              </p>
            )}
          </div>

          <div className="controls">
            <p className="visually-hidden" role="status">
              {announcement}
            </p>

            {needsRestart ? (
              <div className="restart" role="alert">
                <p>{limitReached ? 'Esta conversa chegou ao limite.' : problem?.message}</p>
                <button type="button" ref={restartButton} onClick={() => begin(true)}>
                  Iniciar nova conversa
                </button>
              </div>
            ) : (
              <>
                {problem && (
                  <div className="problem" role="alert">
                    <p>{problem.message}</p>
                    {problem.action === 'retry' && problem.retry ? (
                      <button
                        type="button"
                        onClick={() => void send(retryText, problem.retry!.origin)}
                        disabled={!retryText.trim()}
                      >
                        Tentar novamente
                      </button>
                    ) : (
                      <button type="button" onClick={() => begin(true)}>
                        Tentar novamente
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
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onKeyDown}
                  />
                  <div className="composer-row">
                    <span id="message-hint" className="hint">
                      Enter envia · Shift+Enter quebra linha
                    </span>
                    <button type="submit" className="send" disabled={!canSend}>
                      {busy ? 'Enviando…' : 'Enviar'}
                    </button>
                  </div>
                </form>
              </>
            )}
          </div>
        </main>
      </div>

      <footer className="site-footer">
        <p className="container">Empório da Música · Instrumentos musicais</p>
      </footer>
    </div>
  )
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Algo deu errado. Tente novamente.'
}

// Inline icons (decorative): no icon font or extra dependency.
function TagIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M3 12.6V4a1 1 0 0 1 1-1h8.6l8.1 8.1a1.5 1.5 0 0 1 0 2.1l-7.5 7.5a1.5 1.5 0 0 1-2.1 0z"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <circle cx="8" cy="8" r="1.6" fill="currentColor" />
    </svg>
  )
}
function CardIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="2.5" y="5" width="19" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M2.5 9.5h19M6 15h4" stroke="currentColor" strokeWidth="2" />
    </svg>
  )
}
function BoxIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  )
}
