import { useEffect, useRef, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import { clearSession } from '../services/auth'
import { validateReviewInput } from '../services/testCaseReview'
import { editedPreviewCase, inputFields, replaceInputField, outputFormatError } from '../services/testCaseEditor'
import './InstructorDashboard.css'
import './EditTestCase.css'

export default function EditTestCase({item, schema, saved, outputFormat, onUpdate, onCancel}) {
  const [form, setForm] = useState(() => ({description:item?.description || '', input:item?.input || '', output:item?.output || ''}))
  const [errors, setErrors] = useState({})
  const [busy, setBusy] = useState(false)
  const lock = useRef(false)
  const controller = useRef(null)
  useEffect(() => () => controller.current?.abort(), [])
  const fields = inputFields(schema, form.input)
  async function update(event) {
    event.preventDefault()
    if (saved || !item || lock.current) return
    const next = {}
    if (!form.input.trim()) next.input = 'Enter the program input.'
    const outputError = outputFormatError(form.output, outputFormat, schema)
    if (outputError) next.output = outputError
    setErrors(next)
    if (Object.keys(next).length) return
    lock.current = true; setBusy(true)
    controller.current = new AbortController()
    try {
      const result = await validateReviewInput(schema, form.input, controller.current.signal)
      if (controller.current.signal.aborted) return
      if (!result.valid) {setErrors({input:'Input does not satisfy the approved format or value constraints. Check the element count, ranges and input order.'}); return}
      onUpdate(editedPreviewCase(item, form))
    } catch (error) {
      if (controller.current.signal.aborted) return
      if (error.status === 401 || (error.status === 422 && error.code === 'INVALID_TOKEN')) {
        clearSession(); window.location.replace('#/instructor/login?reason=expired')
      } else setErrors({input:error.code === 'SCHEMA_REQUIRED' ? 'An approved input schema is required to validate this edit.' : error.status === 403 ? 'You do not have access to validate this input.' : 'Input validation is unavailable or the schema is unsupported. Nothing has been changed; please retry.'})
    } finally {lock.current = false; setBusy(false)}
  }
  return <div className="instructor-dashboard edit-case-page"><div className="dashboard-shell edit-case-shell"><InstructorNavigation active="Drafts"/><main className="dashboard-main edit-case-main">
    <h1>Edit Test Case {item?.number ?? ''}</h1>
    {!item ? <><p role="alert">This case is no longer available in your current assessment. Return to the test cases to select it again.</p><button onClick={onCancel}>Back to test cases</button></> : <>
      {saved && <p className="edit-case-notice" role="status">Saved test cases cannot be updated with the current backend. The original case remains unchanged.</p>}
      <form onSubmit={update} noValidate>
        <label htmlFor="case-name">Test Case Name</label><input id="case-name" value={form.description} readOnly={saved} disabled={busy} onChange={e=>setForm({...form,description:e.target.value})}/>
        <fieldset disabled={saved || busy}><legend>Input Values</legend>
          {fields.length ? <div className="edit-case-fields">{fields.map((field,index)=><label key={index} className={field.multiline?'edit-case-wide':''}>{field.label}{field.multiline ? <textarea rows={2} value={field.value} onChange={e=>setForm({...form,input:replaceInputField(form.input,field,e.target.value)})}/> : <input inputMode="numeric" value={field.value} onChange={e=>setForm({...form,input:replaceInputField(form.input,field,e.target.value)})}/>}</label>)}</div> : <label>Program input (exact format)<textarea rows={5} value={form.input} onChange={e=>setForm({...form,input:e.target.value})}/></label>}
        </fieldset>
        <details className="edit-case-raw"><summary>Program input order / exact input</summary><pre>{form.input}</pre><label>Raw input<textarea rows={4} disabled={saved || busy} value={form.input} onChange={e=>setForm({...form,input:e.target.value})}/></label></details>
        {errors.input && <p className="edit-case-error" role="alert">{errors.input}</p>}
        <label htmlFor="case-output" className="edit-case-output-label">Expected Output <span>Required</span></label>
        {outputFormat && <p className="edit-case-format">{outputFormat}</p>}
        <textarea id="case-output" rows={2} readOnly={saved} disabled={busy} value={form.output} onChange={e=>setForm({...form,output:e.target.value})}/>
        {errors.output && <p className="edit-case-error" role="alert">{errors.output}</p>}
        {!saved && <p className="edit-case-note">Local preview only. Changed input or output is unverified and cannot be saved through generation. Review output correctness yourself.</p>}
        <button className="edit-case-update" disabled={saved || busy} type="submit">{busy?'Validating input…':'Update Test Case'}</button>
        <button className="edit-case-cancel" type="button" onClick={onCancel}>Cancel</button>
      </form>
    </>}
  </main></div></div>
}
