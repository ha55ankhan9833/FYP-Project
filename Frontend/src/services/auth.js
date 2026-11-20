import { apiRequest, ApiError } from './api'

const TOKEN_KEY = 'fyp.accessToken'
const PROFILE_KEY = 'fyp.instructorProfile'
let sessionVersion = 0
const sessionListeners = new Set()
export const getSessionVersion = () => sessionVersion
export function subscribeSession(listener) {
  sessionListeners.add(listener)
  return () => sessionListeners.delete(listener)
}
function notifySessionChange() {
  sessionVersion += 1
  sessionListeners.forEach(listener => listener())
}

// Display metadata is usable only after /auth/me verifies the same teacher.
export function getInstructorProfile(identity) {
  if (identity?.role !== 'teacher') return null
  try {
    const profile = JSON.parse(sessionStorage.getItem(PROFILE_KEY))
    if (identity.user_id != null && String(profile?.userId) === String(identity.user_id)) {
      const text = value => typeof value === 'string' ? value.trim() || null : null
      return { displayName: text(profile.displayName), email: text(profile.email), department: text(profile.department) }
    }
  } catch { /* Missing or invalid display metadata uses the generic greeting. */ }
  return null
}
export function getInstructorDisplayName(identity) {
  return getInstructorProfile(identity)?.displayName || null
}

export function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function clearSession() {
  let hadSession = false
  try {
    hadSession = Boolean(sessionStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(PROFILE_KEY))
    sessionStorage.removeItem(TOKEN_KEY)
    sessionStorage.removeItem(PROFILE_KEY)
  } catch { /* Storage may be disabled. */ }
  if (hadSession) notifySessionChange()
}

export async function verifyInstructor(token, signal) {
  if (!token) throw new ApiError('Authentication required', 401, 'AUTH_REQUIRED')
  const identity = await apiRequest('/api/auth/me', { token, signal })
  if (identity.role !== 'teacher' || identity.user_id == null) {
    throw new ApiError('Instructor access required', 403, 'WRONG_ROLE')
  }
  return identity
}

export async function loginInstructor(email, password, signal) {
  clearSession()
  const data = await apiRequest('/api/teacher/login', { method: 'POST', body: { email: email.trim(), password }, signal })
  if (typeof data.access_token !== 'string' || !data.access_token || data.token_type !== 'Bearer' || data.teacher?.teacher_id == null) {
    throw new ApiError('Unexpected login response', 200, 'INVALID_RESPONSE')
  }
  const identity = await verifyInstructor(data.access_token, signal)
  if (String(identity.user_id) !== String(data.teacher.teacher_id)) {
    throw new ApiError('Identity mismatch', 403, 'WRONG_ROLE')
  }
  if (signal?.aborted) throw new DOMException('Request cancelled', 'AbortError')
  try {
    sessionStorage.setItem(PROFILE_KEY, JSON.stringify({
      userId: identity.user_id,
      displayName: typeof data.teacher.teacher_name === 'string' ? data.teacher.teacher_name : null,
      email: typeof data.teacher.teacher_email === 'string' ? data.teacher.teacher_email : null,
      department: typeof data.teacher.department === 'string' ? data.teacher.department : null,
    }))
    sessionStorage.setItem(TOKEN_KEY, data.access_token)
  } catch {
    clearSession()
    throw new ApiError('Session storage unavailable', 0, 'STORAGE_UNAVAILABLE')
  }
  notifySessionChange()
  return identity
}
