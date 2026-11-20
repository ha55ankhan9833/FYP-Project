export function instructorLoginError(error) {
  if (error.code === 'STORAGE_UNAVAILABLE') return 'Your browser could not save the session. Enable session storage and try again.'
  if (error.code === 'WRONG_ROLE') return 'This account could not be verified as an instructor.'
  if (error.status === 401 && error.operation === 'teacher-login' && !error.code) return 'Incorrect email or password.'
  if (error.status === 401 || (error.status === 422 && error.code === 'INVALID_TOKEN')) return 'Your session has expired or is invalid. Please log in again.'
  if (error.code === 'TIMEOUT') return 'The server took too long to respond. Please try again.'
  if (error.code === 'NETWORK') return 'Unable to connect. Check your connection and try again.'
  if ([400, 405, 415, 422].includes(error.status)) return 'The server could not accept the login request. Refresh the page and try again. If this continues, contact support.'
  if (error.status === 404) return 'The login endpoint is unavailable. Please check that the correct backend is running.'
  if ([502, 503, 504].includes(error.status)) return 'The login service is unavailable. Please try again later.'
  if (error.status === 500) {
    // Flask deliberately gives DB failures and other login exceptions the same
    // 500 response. Do not claim to distinguish causes it does not expose.
    return error.code === 'INTERNAL_SERVER_ERROR'
      ? 'An unexpected server error occurred. Please try again or contact support.'
      : 'The server could not complete login. This may be a database or server problem. Please contact support.'
  }
  if (error.code === 'INVALID_RESPONSE') return 'The server returned an unexpected response. Please refresh the page and try again.'
  return 'Unable to complete login. Please try again.'
}
