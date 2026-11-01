// Single place for the API location. In development Vite proxies `/api` to FastAPI (same origin).
// The browser only sends text and a request id; the backend owns session, history and config.
const API_BASE = '/api'
const SESSION_TIMEOUT_MS = 10_000
const MESSAGE_TIMEOUT_MS = 100_000 // backend turn limit is 90 s

export type Source = { section: string; title: string; pages: string }
export type Reply = { status: string; mode: string; text: string; sources: Source[] }
export type SessionInfo = { mode: string; simulated: boolean }

/** `network`: the request may not have reached the server, so the same request id can be retried. */
export class ApiError extends Error {
  readonly code: string
  readonly network: boolean
  constructor(code: string, message: string, network = false) {
    super(message)
    this.code = code
    this.network = network
  }
}

const NETWORK_MESSAGE = 'Não foi possível falar com o servidor. Verifique a conexão e tente novamente.'
const GENERIC_MESSAGE = 'Algo deu errado. Tente novamente.'

async function call(path: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch {
    throw new ApiError('network', NETWORK_MESSAGE, true)
  }
  if (response.ok) return response
  let code = 'http_error'
  let message = GENERIC_MESSAGE
  try {
    const body = await response.json()
    if (typeof body?.error?.code === 'string') code = body.error.code
    if (typeof body?.error?.message === 'string') message = body.error.message
  } catch {
    // A proxy without the backend answers with no JSON body.
    if (response.status >= 500) throw new ApiError('network', NETWORK_MESSAGE, true)
  }
  throw new ApiError(code, message)
}

export async function startSession(): Promise<SessionInfo> {
  const body = await (await call('/sessions', { method: 'POST' }, SESSION_TIMEOUT_MS)).json()
  return { mode: String(body.mode), simulated: body.simulated === true }
}

export async function sendMessage(text: string, requestId: string): Promise<Reply> {
  const response = await call(
    '/messages',
    { method: 'POST', body: JSON.stringify({ text, request_id: requestId }) },
    MESSAGE_TIMEOUT_MS,
  )
  const body = await response.json()
  return {
    status: String(body.status),
    mode: String(body.mode),
    text: String(body.reply?.text ?? ''),
    sources: Array.isArray(body.reply?.sources) ? body.reply.sources : [],
  }
}
