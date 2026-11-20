import { useEffect, useState } from 'react'
import { clearSession, getInstructorDisplayName, getInstructorProfile, getToken, verifyInstructor } from '../services/auth'
import { InstructorSessionContext } from '../services/instructorSession'

export default function InstructorGuard({ children }) {
  const [state, setState] = useState('checking')
  const [retry, setRetry] = useState(0)
  const [identity, setIdentity] = useState(null)
  useEffect(() => {
    const controller = new AbortController()
    let checking = false
    async function check() {
      if (checking) return
      if (!getToken()) {
        setIdentity(null)
        setState('checking')
        clearSession()
        window.location.replace('#/instructor/login?reason=expired')
        return
      }
      checking = true
      try {
        const verified = await verifyInstructor(getToken(), controller.signal)
        if (!controller.signal.aborted) {
          setIdentity({ ...verified, displayName: getInstructorDisplayName(verified), profile: getInstructorProfile(verified) })
          setState('verified')
        }
      } catch (error) {
        if (controller.signal.aborted) return
        setState('checking')
        setIdentity(null)
        if ([401, 403, 422].includes(error.status)) {
          clearSession()
          window.location.replace('#/instructor/login?reason=expired')
        } else setState('unavailable')
      } finally { checking = false }
    }
    check()
    const interval = setInterval(check, 60000)
    window.addEventListener('focus', check)
    window.addEventListener('pageshow', check)
    return () => {
      controller.abort()
      clearInterval(interval)
      window.removeEventListener('focus', check)
      window.removeEventListener('pageshow', check)
    }
  }, [retry])
  if (state === 'verified') return <InstructorSessionContext.Provider value={identity}>{children}</InstructorSessionContext.Provider>
  return <main className="placeholder" aria-live="polite">
    <h1>{state === 'checking' ? 'Checking your session…' : 'Unable to verify your session'}</h1>
    {state === 'unavailable' && <><p>The server is unavailable or could not be reached. Please try again.</p><button type="button" onClick={() => { setState('checking'); setRetry(value => value + 1) }}>Try again</button><br /><a href="#/instructor/login">Back to login</a></>}
  </main>
}
