import { useRef, useState } from 'react'
import Icon from './Icon'
import { analysisErrorMessage, localDeadlineAt, plannedLatePolicy, validateRules } from '../services/assessmentRules'

export default function AssessmentRulesForm({ rules, totalMarks, sourceKey, onChange, onRegenerate, onApplyPending, onKeepEdits, onContinue }) {
  const [errors, setErrors] = useState({})
  const [showPrerequisites, setShowPrerequisites] = useState(false)
  const formRef = useRef(null)
  const result = rules.ai.result
  function change(key, value) {
    onChange({[key]:value})
    if (errors[key]) setErrors(previous=>({...previous,[key]:null}))
    setShowPrerequisites(false)
  }
  function submit(event) {
    event.preventDefault()
    const next = validateRules(rules,totalMarks)
    setErrors(next)
    const first = Object.keys(next)[0]
    if (first) {formRef.current.querySelector(`[name="${first}"]`)?.focus();return}
    if(onContinue)onContinue()
    else setShowPrerequisites(true)
  }
  function error(key) {return errors[key] && <p id={`rules-${key}-error`} className="rules-field-error">{errors[key]}</p>}
  return <form className="assessment-rules-form" ref={formRef} onSubmit={submit} noValidate onKeyDown={event=>{
    if(event.key==='Enter' && event.target.tagName!=='TEXTAREA' && event.target.tagName!=='BUTTON')event.preventDefault()
  }}>
    <section className="rules-card"><h2><span className="rules-card-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4m8-4v4M4 10h16"/></svg></span>Submission Deadline</h2>
      <div className="rules-deadline-fields"><div><label htmlFor="rules-dueDate">Due Date<span className="rules-required"> *</span></label><input id="rules-dueDate" name="dueDate" type="date" value={rules.dueDate} required onChange={event=>change('dueDate',event.target.value)} aria-invalid={Boolean(errors.dueDate)} aria-describedby={errors.dueDate?'rules-dueDate-error':'rules-local-time'}/>{error('dueDate')}</div><div><label htmlFor="rules-cutoffTime">Cutoff Time<span className="rules-required"> *</span></label><input id="rules-cutoffTime" name="cutoffTime" type="time" step="60" value={rules.cutoffTime} required onChange={event=>change('cutoffTime',event.target.value)} aria-invalid={Boolean(errors.cutoffTime)} aria-describedby={errors.cutoffTime?'rules-cutoffTime-error':'rules-local-time'}/>{error('cutoffTime')}</div></div>
      <p className="rules-hint" id="rules-local-time">Use backend server local time, without a timezone offset. The device clock is used for this draft&apos;s future-date check.</p>
    </section>
    <section className="rules-card"><div className="rules-policy-heading"><h2><span className="rules-card-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-hidden="true"><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/></svg></span>Late Submission Policy</h2><label className="rules-switch-label" htmlFor="rules-allowLate">Allow Late<button id="rules-allowLate" type="button" role="switch" aria-checked={rules.allowLate} aria-label="Allow late submissions" className="rules-switch" onClick={()=>change('allowLate',!rules.allowLate)}><span/></button></label></div>
      <label htmlFor="rules-latePenalty">Late Deduction Penalty</label><div className="rules-penalty-row"><input id="rules-latePenalty" name="latePenalty" type="text" inputMode="decimal" value={rules.latePenalty} disabled={!rules.allowLate} onChange={event=>change('latePenalty',event.target.value)} aria-invalid={Boolean(errors.latePenalty)} aria-describedby={errors.latePenalty?'rules-latePenalty-error':'rules-penalty-hint'}/><span>Marks deducted per 24 hours late</span></div>{error('latePenalty')}
      <p className="rules-hint" id="rules-penalty-hint">{rules.allowLate ? `Planned deduction: 0–${totalMarks} marks.` : 'Late submissions are disabled; the effective penalty is zero.'} This is not saved. Automatic per-24-hour deductions are not implemented by the backend.</p>
    </section>
    <section className="rules-card"><h2><span className="rules-card-icon"><Icon name="sections"/></span>Problem Constraints</h2><p className="rules-hint rules-constraints-intro">Define limits for input, output and execution. Suggestions are not approved constraints.</p>
      <div className="rules-analysis-status" aria-live="polite" aria-busy={rules.ai.status==='loading'}>
        {rules.ai.status==='loading' && <p role="status">Generating backend constraint suggestions… Your current edits will be retained.</p>}
        {rules.ai.status==='idle' && <p>Constraint analysis has not started.</p>}
        {rules.ai.status==='ready' && <p>Analysis received — teacher approval pending.{result?.model.status && <span> Model status: {result.model.status}.</span>}</p>}
        {rules.ai.status==='error' && <p role="alert">{analysisErrorMessage(rules.ai.error || {})}</p>}
        {rules.ai.appliedKey && rules.ai.appliedKey!==sourceKey && <p className="rules-warning">Basic Details changed. Review retained constraints against the current problem.</p>}
        {result?.warnings.length>0 && <ul className="rules-warning">{result.warnings.map((warning,index)=><li key={index}>{warning.code ? `${warning.code}: ` : ''}{warning.message}</li>)}</ul>}
        {result?.valueItems.some(item=>item.status) && <ul className="rules-evidence">{result.valueItems.map((item,index)=>item.status && <li key={index}>Value suggestion {index+1}: {item.status}</li>)}</ul>}
        {result?.codeItems.length>0 && <div className="rules-code-review"><h3>Backend code suggestions — review pending</h3><p>These describe the analysis response. Editing the text below does not approve a suggestion or change its original metadata.</p><ul>{result.codeItems.map((item,index)=><li key={index}><strong>{item.rule==='PROHIBITED'?'Prohibited':item.rule==='REQUIRED'?'Required':'Rule unspecified'}: {item.type}</strong><p>{item.enforcement==='STATIC_CHECK'?'Static check (lexical only; no automatic mark deduction).':item.enforcement==='TEACHER_REVIEW_ONLY'?'Teacher review only — not automatically checked.':'Enforcement unspecified by backend.'}</p>{item.evidence && <p>Evidence: {item.evidence}</p>}<p>Status: {item.status || 'Unspecified'} — teacher approval still required.</p></li>)}</ul></div>}
        <button type="button" className="rules-regenerate" onClick={onRegenerate} disabled={rules.ai.status==='loading'}>{rules.ai.status==='error'?'Retry AI analysis':'Regenerate suggestions'}</button>
      </div>
      {rules.ai.pending && <div className="rules-pending"><h3>New suggestions are ready</h3><p>Your constraint fields have not been overwritten. Compare these suggestions before replacing them.</p><h4>Value suggestions</h4><pre>{rules.ai.pending.valueText || 'No value suggestions returned.'}</pre><h4>Code suggestions</h4><pre>{rules.ai.pending.codeText || 'No code suggestions returned.'}</pre><div><button type="button" disabled={rules.ai.pending.sourceKey!==sourceKey} onClick={onApplyPending}>Replace constraint fields with suggestions</button><button type="button" onClick={onKeepEdits}>Keep my edits</button></div></div>}
      <div className="rules-constraint-fields"><div><label htmlFor="rules-valueConstraints">Value Constraints<span className="rules-required"> *</span></label><textarea id="rules-valueConstraints" name="valueConstraints" rows={4} required value={rules.valueConstraints} onChange={event=>change('valueConstraints',event.target.value)} aria-invalid={Boolean(errors.valueConstraints)} aria-describedby={errors.valueConstraints?'rules-valueConstraints-error':undefined}/><span className="rules-character-count">{rules.valueConstraints.length} characters</span>{error('valueConstraints')}</div><div><label htmlFor="rules-codeConstraints">Code Constraints</label><textarea id="rules-codeConstraints" name="codeConstraints" rows={4} value={rules.codeConstraints} onChange={event=>change('codeConstraints',event.target.value)}/><span className="rules-character-count">{rules.codeConstraints.length} characters</span></div></div>
      <p className="rules-hint">{rules.editedConstraints?'Teacher-edited text — backend approval is still pending.':'Backend suggestions — teacher review and approval are still required.'} {result && !result.codeItems.length ? 'No code constraints were returned. The backend detects explicit code requirements using phrase patterns; FLAN-T5 generates value suggestions only. You may enter code requirements manually.' : result?.codeItems.length ? 'Code suggestions come from backend rule extraction; review them before use.' : 'Code suggestions will populate only if returned by the backend.'}</p>
    </section>
    <div className="rules-additional"><label htmlFor="rules-additionalConstraints"><Icon name="drafts"/>Additional Constraints <span>(Optional)</span></label><textarea id="rules-additionalConstraints" name="additionalConstraints" rows={3} value={rules.additionalConstraints} onChange={event=>change('additionalConstraints',event.target.value)} placeholder="Add optional requirements"/><span className="rules-character-count">{rules.additionalConstraints.length} characters</span></div>
    <button className="rules-generate" type="submit" disabled={rules.ai.status==='loading'}>Generate Test Cases<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12h16m-7-7 7 7-7 7"/></svg></button>
    {showPrerequisites && <section className="rules-prerequisites" role="status"><h2>Generation prerequisites are not complete</h2><p>Your Step 1 and Step 2 values remain local. No assessment, approval or test cases have been saved or generated.</p><ul><li>A saved teacher-owned constraint draft must be reviewed, both categories approved, and finalized to obtain a real draft ID.</li><li>Inputs need supported approved integer ranges or an approved structured input schema. Plain-text constraints are not a structured schema.</li><li>An approved reference solution and a verified execution/oracle mechanism must establish expected outputs.</li><li>The assessment needs a real ID, validation/rubric allocation and the approved workflow before section assignment and generation can run.</li><li>Database writes and test generation require separate approval. Step 3 is awaiting its Figma mockup.</li></ul><p>Planned deadline: {localDeadlineAt(rules)} (server local time). Late submissions: {plannedLatePolicy(rules).allow_late?'allowed':'disabled'}; planned penalty: {plannedLatePolicy(rules).late_penalty_marks} marks.</p></section>}
  </form>
}

