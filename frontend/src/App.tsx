import { useEffect, useState } from 'react'
import { checkHealth } from './api'

type Connection = 'checking' | 'online' | 'offline'

const MESSAGES: Record<Connection, string> = {
  checking: 'Verificando a conexão com o servidor…',
  online: 'Servidor disponível. O atendimento virtual ainda não está ativo.',
  offline: 'Não foi possível conectar ao servidor. Tente novamente em instantes.',
}

export default function App() {
  const [connection, setConnection] = useState<Connection>('checking')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let current = true
    checkHealth().then((ok) => {
      if (current) setConnection(ok ? 'online' : 'offline')
    })
    return () => {
      current = false
    }
  }, [attempt])

  const checking = connection === 'checking'

  function retry() {
    if (checking) return
    setConnection('checking')
    setAttempt((n) => n + 1)
  }

  return (
    <main className="page">
      <header className="brand">
        <span className="brand-mark" aria-hidden="true">
          ♪
        </span>
        <div>
          <h1>Empório da Música</h1>
          <p className="tagline">Atendimento virtual</p>
        </div>
      </header>

      <section className="card" aria-labelledby="notice-title">
        <h2 id="notice-title">Atendimento em preparação</h2>
        <p>
          O assistente virtual da loja ainda está em desenvolvimento e não responde perguntas nesta
          versão.
        </p>
      </section>

      <section className="card" aria-labelledby="connection-title">
        <h2 id="connection-title">Conexão com o servidor</h2>
        <p className={`status status-${connection}`} role="status" aria-live="polite">
          {MESSAGES[connection]}
        </p>
        <button type="button" onClick={retry} aria-disabled={checking}>
          {checking ? 'Verificando…' : 'Tentar novamente'}
        </button>
      </section>
    </main>
  )
}
