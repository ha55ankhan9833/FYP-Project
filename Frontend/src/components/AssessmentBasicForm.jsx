import { useRef, useState } from 'react'
import Icon from './Icon'
import { validateBasicDetails } from '../services/assessmentBasicDetails'

export default function AssessmentBasicForm({ values, course, onChange, onContinue, onDiscard }) {
  const [errors, setErrors] = useState({})
  const formRef = useRef(null)
  const quiz = values.type === 'QUIZ'
  function update(key, value) {
    onChange({ ...values, [key]: value })
    if (errors[key]) setErrors(previous => ({ ...previous, [key]: null }))
  }
  function submit(event) {
    event.preventDefault()
    const next = validateBasicDetails(values)
    setErrors(next)
    const first = Object.keys(next)[0]
    if (first) { formRef.current.querySelector(`[name="${first}"]`)?.focus(); return }
    onContinue(values)
  }
  function textField(key, label, { required = false, rows = 1 } = {}) {
    return <div className="basic-field" key={key}><label htmlFor={`basic-${key}`}>{label}{required && <span className="basic-required"> *</span>}</label><textarea id={`basic-${key}`} name={key} value={values[key]} rows={rows} required={required} aria-invalid={Boolean(errors[key])} aria-describedby={errors[key] ? `basic-${key}-error` : undefined} onChange={event => update(key, event.target.value)} />{errors[key] && <p className="basic-field-error" id={`basic-${key}-error`}>{errors[key]}</p>}</div>
  }
  return <form className="assessment-basic-form" ref={formRef} onSubmit={submit} noValidate onKeyDown={event => {
    if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA' && event.target.tagName !== 'BUTTON') event.preventDefault()
  }}>
    <div className="basic-type-toggle" role="group" aria-label="Assessment type"><button type="button" aria-pressed={!quiz} onClick={() => update('type', 'ASSIGNMENT')}><Icon name="drafts" />Assignment</button><button type="button" aria-pressed={quiz} onClick={() => update('type', 'QUIZ')}><Icon name="graduation" />Quiz</button></div>
    {errors.type && <p className="basic-field-error" role="alert">{errors.type}</p>}
    <div className="basic-field"><div className="basic-label-row"><label htmlFor="basic-title">{quiz ? 'Quiz' : 'Assignment'} Title<span className="basic-required"> *</span></label><span id="basic-title-count">{values.title.length}/60</span></div><input id="basic-title" name="title" value={values.title} maxLength={60} required aria-describedby={errors.title ? 'basic-title-count basic-title-error' : 'basic-title-count'} aria-invalid={Boolean(errors.title)} onChange={event => update('title', event.target.value)} />{errors.title && <p id="basic-title-error" className="basic-field-error">{errors.title}</p>}</div>
    <div className="basic-field"><label htmlFor="basic-course">Course</label><div className="basic-course-control"><input id="basic-course" value={[course.code, course.name].filter(Boolean).join(' • ')} readOnly /><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg></div></div>
    <div className="basic-field"><label htmlFor="basic-totalMarks">Total Marks<span className="basic-required"> *</span></label><div className="basic-marks-control"><input id="basic-totalMarks" name="totalMarks" type="text" inputMode="decimal" value={values.totalMarks} required aria-invalid={Boolean(errors.totalMarks)} aria-describedby={errors.totalMarks ? 'basic-totalMarks-error' : undefined} onChange={event => update('totalMarks', event.target.value)} /><span aria-hidden="true">pts</span></div>{errors.totalMarks && <p id="basic-totalMarks-error" className="basic-field-error">{errors.totalMarks}</p>}</div>
    <section className="basic-description-card" aria-labelledby="basic-problem-heading"><h2 id="basic-problem-heading"><span><Icon name="drafts" /></span>Problem Description</h2>{textField('problemStatement', 'Problem Statement', { required: true, rows: 4 })}{textField('inputFormat', 'Input Format', { required: true })}{textField('outputFormat', 'Output Format', { required: true })}</section>
    <section className="basic-description-card" aria-labelledby="basic-sample-heading"><h2 id="basic-sample-heading"><Icon name="code-slash" />Default Sample Case</h2>{textField('sampleInput', 'Sample Input')}{textField('sampleOutput', 'Sample Output')}</section>
    <button type="submit" className="basic-continue">Continue to Constraints<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12h16m-7-7 7 7-7 7" /></svg></button>
    <button type="button" className="basic-discard" onClick={onDiscard}>Discard draft</button>
  </form>
}
