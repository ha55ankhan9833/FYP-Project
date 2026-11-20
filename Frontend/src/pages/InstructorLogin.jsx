import { useEffect, useRef, useState } from 'react'
import Icon from '../components/Icon'
import { clearSession, getToken, loginInstructor, verifyInstructor } from '../services/auth'
import { instructorLoginError } from '../services/loginErrors'
import './InstructorLogin.css'

export default function InstructorLogin() {
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState({})
  const [message, setMessage] = useState('')
  const emailRef = useRef(null)
  const passwordRef = useRef(null)
  const requestRef = useRef(null)
  const expired = window.location.hash.includes('reason=expired')

  useEffect(() => {
    const token = getToken()
    const controller = new AbortController()
    requestRef.current = controller
    if (token) {
      verifyInstructor(token, controller.signal).then(() => {
        if (!controller.signal.aborted) window.location.replace('#/instructor/dashboard')
      }).catch(error => {
        if (controller.signal.aborted) return
        if ([401, 403, 422].includes(error.status)) clearSession()
        setMessage(instructorLoginError(error))
      })
    }
    return () => { controller.abort(); requestRef.current?.abort() }
  }, [])

  async function submit(event) {
    event.preventDefault()
    if (busy) return
    const email = emailRef.current.value.trim()
    const password = passwordRef.current.value
    const nextErrors = {}
    if (!email) nextErrors.email = 'Enter your email address.'
    else if (!emailRef.current.validity.valid) nextErrors.email = 'Enter a valid email address.'
    if (!password) nextErrors.password = 'Enter your password.'
    setErrors(nextErrors)
    setMessage('')
    if (Object.keys(nextErrors).length) {
      ;(nextErrors.email ? emailRef : passwordRef).current.focus()
      return
    }
    setBusy(true)
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    try {
      await loginInstructor(email, password, controller.signal)
      passwordRef.current.value = ''
      window.location.replace('#/instructor/dashboard')
    } catch (error) {
      if (!controller.signal.aborted) {
        setMessage(instructorLoginError(error))
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return <main className="instructor-login">
    <a className="login-back" href="#/" aria-label="Back to role selection"><Icon name="back" /></a>
    <header className="login-header">
      <span className="login-logo"><Icon name="code-slash" /></span>
      <h1>Welcome Back!</h1>
      <p>Login to continue</p>
    </header>
    <div className="login-role">Instructor</div>
    {expired && !message && <p className="login-notice" role="status">Your session has expired or is invalid. Please log in again.</p>}
    <form className="login-form" onSubmit={submit} noValidate aria-busy={busy}>
      <div className="login-field">
        <label htmlFor="instructor-email">Email</label>
        <input ref={emailRef} id="instructor-email" name="email" type="email" autoComplete="username" placeholder="Enter your email" required disabled={busy} aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? 'email-error' : undefined} />
        {errors.email && <p className="login-field-error" id="email-error">{errors.email}</p>}
      </div>
      <div className="login-field">
        <label htmlFor="instructor-password">Password</label>
        <div className="login-password">
          <input ref={passwordRef} id="instructor-password" name="password" type={visible ? 'text' : 'password'} autoComplete="current-password" placeholder="Enter Your Password" required disabled={busy} aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? 'password-error' : undefined} />
          <button className="login-visibility" type="button" onClick={() => setVisible(value => !value)} aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible} aria-controls="instructor-password"><Icon name={visible ? 'eye' : 'eye-off'} /></button>
        </div>
        {errors.password && <p className="login-field-error" id="password-error">{errors.password}</p>}
      </div>
      <p className="login-forgot" aria-disabled="true" title="Password reset is not available yet">Forgot your password? <span>(Unavailable)</span></p>
      {message && <p className="login-error" role="alert">{message}</p>}
      <button className="login-submit" type="submit" disabled={busy}>{busy ? 'Logging in…' : 'Login'}</button>
    </form>
  </main>
}
