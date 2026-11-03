import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, sendMessage, startSession, type Source } from './api'

type Message = {
  id: number
  role: 'customer' | 'assistant'
  text: string
  sources?: Source[]
  simulated?: boolean
}
// Where a question was typed or picked. Every origin uses the same send path and the same session.
type Origin = 'composer' | 'search' | 'shortcut'
type Problem = {
  message: string
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
// Topics covered by the store manual (payment §3, exchanges §4, delivery §5, warranty §8, hours §2).
const QUESTIONS = [
  'Como funciona o desconto no PIX?',
  'Posso parcelar no cartão?',
  'Como faço para trocar um produto?',
  'Qual o prazo de entrega para outras cidades?',
  'Como funciona a garantia?',
  'Qual o horário de funcionamento da loja?',
]

export default function App() {
  const [ready, setReady] = useState(false)
  const [simulated, setSimulated] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const epoch = useRef(0) // bumps on every new conversation; late replies of older ones are dropped
  const sending = useRef(false)
  // The request id is reused only to resend the same text from the same place after a network failure.
  const retry = useRef<{ text: string; origin: Origin; requestId: string } | null>(null)
  const nextId = useRef(0)
  const input = useRef<HTMLTextAreaElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const logEnd = useRef<HTMLDivElement>(null)

  // `keepTyped`: a restart forced by an error (e.g. the session was lost) keeps what the customer
  // typed; the "Nova conversa" button is an explicit reset and clears everything.
  function begin(keepTyped = false) {
    epoch.current++
    sending.current = false
    retry.current = null
    setReady(false)
    setBusy(false)
    setMessages([])
    if (!keepTyped) {
      setDraft('')
      setQuery('')
    }
    setProblem(null)
    setAnnouncement('Iniciando conversa…')
    void connect(epoch.current, true)
  }

  // Async only: state updates happen after the request, never synchronously in the mount effect.
  // `focus`: only after an explicit "Nova conversa"; the first load does not move the page or open a keyboard.
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

  // Follow the conversation, but never move the page on the first, empty screen.
  useEffect(() => {
    if (messages.length > 0 || busy) logEnd.current?.scrollIntoView?.({ block: 'nearest' })
  }, [messages, busy])

  async function send(raw: string, origin: Origin) {
    const text = raw.trim()
    if (!ready || sending.current || !text) return
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
    try {
      const reply = await sendMessage(text, requestId)
      if (current !== epoch.current) return
      retry.current = null
      if (origin === 'composer') setDraft('')
      if (origin === 'search') setQuery('')
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
      // Typed text stays where it was typed; drop the unsent bubble so the log shows only delivered turns.
      setMessages((m) => m.filter((msg) => msg.id !== pendingId))
      const network = error instanceof ApiError && error.network
      if (!network) retry.current = null
      const restart = error instanceof ApiError && RESTART_CODES.has(error.code)
      setAnnouncement('')
      setProblem({
        message: messageOf(error),
        action: restart ? 'restart' : 'retry',
        retry: { text, origin },
      })
    } finally {
      if (current === epoch.current) {
        sending.current = false
        setBusy(false)
        if (origin === 'search') search.current?.focus({ preventScroll: true })
        else input.current?.focus({ preventScroll: origin !== 'composer' })
      }
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    void send(draft, 'composer')
  }

  function onAsk(event: FormEvent) {
    event.preventDefault()
    void send(query, 'search')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void send(draft, 'composer')
    }
  }

  // Retry resends what is in the box the question came from (it may have been edited).
  const retryOrigin = problem?.retry?.origin
  const retryText =
    retryOrigin === 'composer' ? draft : retryOrigin === 'search' ? query : (problem?.retry?.text ?? '')
  const canAsk = ready && !busy
  const canSend = canAsk && draft.trim().length > 0

  return (
    <div className="site">
      <a className="skip-link" href="#message">
        Ir para o campo de mensagem
      </a>
      <p className="demo-strip">
        <span className="container">Demo conceitual de atendimento com IA · não é um canal de vendas</span>
      </p>

      <header className="masthead">
        <div className="container masthead-row">
          <h1 className="logo">
            <span className="logo-word">Empório</span>
            <span className="logo-tag">da Música</span>
            <span className="visually-hidden"> — atendimento virtual</span>
          </h1>
          <form className="ask" onSubmit={onAsk} aria-label="Pergunta rápida">
            <label htmlFor="ask" className="visually-hidden">
              Pergunte ao assistente
            </label>
            <input
              id="ask"
              ref={search}
              type="text"
              value={query}
              maxLength={MAX_CHARS}
              readOnly={busy}
              enterKeyHint="send"
              autoComplete="off"
              placeholder="Encontre um instrumento ou pergunte"
              onChange={(e) => setQuery(e.target.value)}
            />
            <button type="submit" className="ask-button" disabled={!canAsk || !query.trim()}>
              <SearchIcon />
              <span className="visually-hidden">Perguntar</span>
            </button>
          </form>
        </div>
        <nav className="categories" aria-label="Perguntar por categoria">
          <ul className="container">
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
      </header>

      <section className="topics" aria-label="Com o que o assistente ajuda">
        <ul className="container">
          <li>
            <TagIcon />
            <span>
              <strong>Instrumentos e preços</strong> do catálogo da loja
            </span>
          </li>
          <li>
            <CardIcon />
            <span>
              <strong>Pagamento e parcelamento</strong> conforme o manual
            </span>
          </li>
          <li>
            <BoxIcon />
            <span>
              <strong>Trocas, entregas e garantia</strong> com a seção citada
            </span>
          </li>
        </ul>
      </section>

      <div className="container layout">
        <main className="chat" aria-labelledby="chat-title">
          <div className="chat-head">
            <div>
              <h2 id="chat-title">Atendimento Empório</h2>
              <p>Assistente virtual · respostas do catálogo e do manual da loja</p>
            </div>
            <button type="button" className="new-conversation" onClick={() => begin()}>
              Nova conversa
            </button>
          </div>

          {simulated && (
            <p className="simulated-banner" role="note">
              <strong>Modo simulado:</strong> as respostas vêm de um roteiro de teste. Nenhum modelo de
              IA é chamado e elas não representam o atendimento real.
            </p>
          )}

          <div className="log" role="log" aria-live="polite" aria-label="Conversa">
            {messages.length === 0 && ready && (
              <div className="empty">
                <p className="empty-title">Olá! Como posso ajudar?</p>
                <p>
                  Pergunte sobre instrumentos, preços, estoque, formas de pagamento, trocas e entregas
                  da Empório da Música. Escreva abaixo, use a busca no topo ou escolha uma categoria.
                </p>
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
            <div ref={logEnd} />
          </div>

          <p className="visually-hidden" role="status">
            {announcement}
          </p>

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
        </main>

        <aside className="side">
          <section aria-labelledby="faq-title">
            <h2 id="faq-title" className="side-title">
              Perguntas frequentes
            </h2>
            <ul className="questions">
              {QUESTIONS.map((q) => (
                <li key={q}>
                  <button type="button" disabled={!canAsk} onClick={() => void send(q, 'shortcut')}>
                    {q}
                  </button>
                </li>
              ))}
            </ul>
            <p className="side-note">
              Sugestões, categorias e a busca continuam a conversa atual. Para recomeçar, use “Nova
              conversa”.
            </p>
          </section>
          <section aria-labelledby="about-title" className="about">
            <h2 id="about-title" className="side-title">
              Sobre esta demo
            </h2>
            <ul>
              <li>Preços, estoque e promoções vêm de uma cópia do catálogo da Empório da Música.</li>
              <li>Regras de pagamento, trocas, entregas e garantia vêm do manual da loja.</li>
              <li>O assistente não faz compras, reservas, cancelamentos nem consulta pedidos.</li>
            </ul>
          </section>
        </aside>
      </div>

      <footer className="site-footer">
        <div className="container footer-row">
          <p className="footer-logo">
            Empório <span>da Música</span>
          </p>
          <p>
            Demo conceitual de atendimento com IA para a Empório da Música. As respostas usam o
            catálogo e o manual da loja; confirme valores e condições antes de comprar.
          </p>
        </div>
        <p className="footer-bottom">
          <span className="container">Projeto técnico de demonstração · não é um canal oficial de vendas</span>
        </p>
      </footer>
    </div>
  )
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : 'Algo deu errado. Tente novamente.'
}

// Inline icons (decorative): no icon font or extra dependency.
function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="m15.5 15.5 5 5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}
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
