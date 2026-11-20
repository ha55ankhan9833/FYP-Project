import { useEffect, useRef, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import { validateReviewInput } from '../services/testCaseReview'
import { inputFields, replaceInputField, outputFormatError } from '../services/testCaseEditor'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'
import './EditTestCase.css'

export default function AddTestCase({schema,outputFormat,draft,onDraftChange,saved,unavailable,busy,error,onAdd,onCancel}) {
  const [errors,setErrors]=useState({})
  const [validating,setValidating]=useState(false)
  const lock=useRef(false),controller=useRef(null)
  useEffect(()=>()=>controller.current?.abort(),[])
  const form=draft || {description:'',input:'',output:'',confirmed:false}
  const fields=inputFields(schema,form.input)
  const simple=schema?.version===1 && schema.items?.length===2 && schema.items[0].type==='ints' && schema.items[0].vars?.length===1 && schema.items[1].type==='array' && schema.items[1].length===schema.items[0].vars[0]
  const set=patch=>onDraftChange({...form,...patch})
  const disabled=saved||unavailable||busy||validating
  async function submit(event){
    event.preventDefault()
    if(disabled||lock.current)return
    const next={}
    if(!form.description.trim())next.description='Enter a test case name.'
    if(!form.input.trim())next.input='Enter the program input.'
    const outputError=outputFormatError(form.output,outputFormat,schema)
    if(outputError)next.output=outputError
    if(!form.confirmed)next.confirmed='Confirm replacement of the unsaved preview before continuing.'
    setErrors(next)
    if(Object.keys(next).length)return
    lock.current=true;setValidating(true);controller.current=new AbortController()
    try{
      const result=await validateReviewInput(schema,form.input,controller.current.signal)
      if(controller.current.signal.aborted)return
      if(!result.valid){setErrors({input:'Input does not match the approved schema or value constraints. Check the count, ranges and input format.'});return}
      await onAdd(form)
    }catch(problem){
      if(controller.current.signal.aborted)return
      if(problem.status===401||(problem.status===422&&problem.code==='INVALID_TOKEN')){clearSession();window.location.replace('#/instructor/login?reason=expired')}
      else setErrors({input:'Input validation could not complete. Your form and previous cases are preserved.'})
    }finally{lock.current=false;setValidating(false)}
  }
  return <div className="instructor-dashboard edit-case-page"><div className="dashboard-shell edit-case-shell"><InstructorNavigation active="Drafts"/><main className="dashboard-main edit-case-main"><h1>Add Test Case</h1>
    {saved && <p className="edit-case-notice" role="status">The 10 test cases have already been saved. Adding or replacing saved cases requires backend support. Your saved set remains unchanged.</p>}
    {unavailable && <p className="edit-case-notice" role="alert">Open an approved, unsaved generated preview to add a custom test case.</p>}
    <form onSubmit={submit} noValidate>
      <label htmlFor="add-name">Test Case Name</label><input id="add-name" disabled={disabled} value={form.description} onChange={e=>set({description:e.target.value})}/>{errors.description && <p className="edit-case-error" role="alert">{errors.description}</p>}
      <fieldset disabled={disabled}><legend>Input Values</legend>
      {simple && !form.raw ? <div className="edit-case-fields"><label>{schema.items[0].vars[0]} — number of elements<input inputMode="numeric" value={form.count??form.input.split('\n')[0]} onChange={e=>{const count=e.target.value,array=form.array??form.input.split('\n').slice(1).join('\n');set({count,array,input:count+'\n'+array})}}/></label><label className="edit-case-wide">{schema.items[1].name}[i] — array values<textarea rows={2} value={form.array??form.input.split('\n').slice(1).join('\n')} onChange={e=>{const array=e.target.value,count=form.count??form.input.split('\n')[0];set({count,array,input:count+'\n'+array})}}/></label></div> : fields.length && !form.raw ? fields.map((field,i)=><label key={i}>{field.label}<textarea rows={2} value={field.value} onChange={e=>set({input:replaceInputField(form.input,field,e.target.value)})}/></label>) : <label>Program input (exact format)<textarea rows={4} value={form.input} onChange={e=>set({input:e.target.value,raw:true})}/></label>}
      </fieldset>
      <details className="edit-case-raw"><summary>Program input order / exact input</summary><pre>{form.input}</pre><label>Raw input<textarea rows={3} disabled={disabled} value={form.input} onChange={e=>set({input:e.target.value,raw:true})}/></label></details>
      {errors.input && <p className="edit-case-error" role="alert">{errors.input}</p>}
      <label htmlFor="add-output" className="edit-case-output-label">Expected Output <span>Required</span></label>{outputFormat && <p className="edit-case-format">{outputFormat}</p>}<textarea id="add-output" rows={2} disabled={disabled} value={form.output} onChange={e=>set({output:e.target.value})}/>{errors.output && <p className="edit-case-error" role="alert">{errors.output}</p>}
      <p className="edit-case-note">Your output is an unverified local suggestion. The backend calculates the verified output; your case name is a local note. Nothing is saved or published here.</p>
      {!saved&&!unavailable && <label className="add-case-confirm"><input type="checkbox" disabled={busy||validating} checked={form.confirmed} onChange={e=>set({confirmed:e.target.checked})}/>Replace the unsaved preview with a new backend-generated set of exactly 10 cases including this input. Existing local case edits will be discarded; rubric and marks are preserved.</label>}
      {errors.confirmed && <p className="edit-case-error" role="alert">{errors.confirmed}</p>}{error && <p className="edit-case-error" role="alert">{error.message}</p>}
      <button className="edit-case-update" type="submit" disabled={disabled}>{busy?'Refreshing verified preview…':validating?'Validating input…':'Add Test Case'}</button><button className="edit-case-cancel" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </form>
  </main></div></div>
}
