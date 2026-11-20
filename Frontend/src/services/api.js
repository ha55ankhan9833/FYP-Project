export class ApiError extends Error {
  constructor(message, status = 0, code = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export async function apiRequest(path, { token, body, signal, method = 'GET', timeoutMs = 15000 } = {}) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  try {
    const response = await fetch(path, {
      method,
      headers: {
        Accept: 'application/json',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const error=new ApiError('API request failed', response.status, data?.code)
      // Retain only documented recovery/validation fields, never tokens or request bodies.
      error.data=Object.fromEntries(['message','assessment_id','details','errors','missing','missing_for_generation','verification','validated_inputs','finalization_status','reason'].filter(key=>data?.[key]!=null).map(key=>[key,data[key]]))
      throw error
    }
    if (!data || data.status !== 'success') throw new ApiError('Unexpected API response', response.status, 'INVALID_RESPONSE')
    return data
  } catch (error) {
    if (error instanceof ApiError) {
      // Identify the failed step without retaining request bodies or headers.
      error.operation = path === '/api/teacher/login' ? 'teacher-login' : 'other'
      throw error
    }
    if (signal?.aborted) throw error
    throw new ApiError('Unable to reach the server', 0, controller.signal.aborted ? 'TIMEOUT' : 'NETWORK')
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}
