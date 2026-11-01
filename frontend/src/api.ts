// Single place for the API location. In development Vite proxies `/api` to FastAPI.
const API_BASE = '/api'
const TIMEOUT_MS = 5000

/** True only when the backend answers the health contract; any failure is false. */
export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    })
    if (!response.ok) return false
    const body: unknown = await response.json()
    return typeof body === 'object' && body !== null && 'status' in body && body.status === 'ok'
  } catch {
    return false
  }
}
