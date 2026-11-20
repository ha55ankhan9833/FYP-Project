import { useEffect, useRef, useState } from 'react'
import { allocatedMarks, validateReviewInput } from '../services/testCaseReview'
import { clearSession } from '../services/auth'

function ReviewDialog({labelId,onClose,children}) {
  const container=useRef(null)
  useEffect(()=>{
    const previous=document.activeElement
    if(!container.current.contains(document.activeElement))container.current.focus()
    return()=>previous?.focus()
  },[])
  return <div className="tests-dialog-backdrop"><section ref={container} tabIndex={-1} className="tests-dialog" role="dialog" aria-modal="true" aria-labelledby={labelId} onKeyDown={event=>{
    if(event.key==='Escape'){event.preventDefault();onClose()}
    if(event.key==='Tab'){
      const controls=[...event.currentTarget.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)')]
      const first=controls[0],last=controls.at(-1)
      if(!first){event.preventDefault();return}
      if(event.shiftKey&&(document.activeElement===first||document.activeElement===event.currentTarget)){event.preventDefault();last.focus()}
      else if(!event.shiftKey&&(document.activeElement===last||document.activeElement===event.currentTarget)){event.preventDefault();first.focus()}
    }
  }}>{children}</section></div>
}

export default function TestCasesReview({cases,onChange,onEdit,onAdd,totalMarks,type='ASSIGNMENT',blockers=[],inputSchema=null,status='blocked',integrated=false,readyToPublish=false,generatedCount=cases.length}) {
  const [editor,setEditor]=useState(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [deleting,setDeleting]=useState(null)
  const sequence=useRef(0)
  const total=allocatedMarks(cases)
  const exceeded=total>Number(totalMarks)+0.000001
  const label=type==='QUIZ'?'Quiz':'Assignment'
  function open(item=null){setError('');setEditor(item?{...item}:{key:`local-${++sequence.current}`,id:null,description:'',input:'',output:'',marks:null,origin:'manual',verification:'unverified',changed:true})}
  async function save(event){
    event.preventDefault()
    if(!editor.input.trim() || !editor.output.trim()){setError('Input and expected output are required.');return}
    setBusy(true);setError('')
    try{
      const result=await validateReviewInput(inputSchema,editor.input)
      if(!result.valid){setError('Input does not satisfy the approved schema. Review its syntax, constraints and semantic requirements.');return}
      const next={...editor,changed:true,verification:'unverified'}
      onChange(cases.some(item=>item.key===next.key)?cases.map(item=>item.key===next.key?next:item):[...cases,next]);setEditor(null)
    }catch(problem){
      if([401,422].includes(problem.status)){clearSession();window.location.replace('#/instructor/login?reason=expired')}
      else setError(problem.code==='SCHEMA_REQUIRED'?'An approved input schema is required before validating this local edit.':'Input validation failed. No changes have been applied; retry when the backend is available.')
    }finally{setBusy(false)}
  }
  return <>
    <section className="tests-summary tests-card"><div className="tests-summary-heading"><h1><span aria-hidden="true">✧</span> Generated Test Cases</h1><span className={`tests-count ${cases.length?'has-cases':''}`}>{generatedCount} {status==='blocked'?'available':status==='saved'?'Generated':status==='preview'?'Previewed':'returned'}</span></div><p>Review inputs and expected outputs before making this {label.toLowerCase()} available to students.</p><div className="tests-marks">Assessment: {totalMarks} marks · Case allocation: {Number(total.toFixed(6))} marks{cases.some(item=>item.marks==null) && ' (some marks unspecified)'}</div><p className="tests-note">All test cases share the selected test-case allocation equally. Rubric marks are separate. This is not a student score.</p></section>
    {status==='generating' && <p role="status" className="tests-card">Generating test cases…</p>}
    {status==='error' && <p role="alert" className="tests-card">Generation failed. No successful generation is being claimed.</p>}
    {blockers.length>0 && <section className="tests-card tests-blockers"><h2>Generation blocked</h2><p>No generation request was sent. Complete the following review workflow first:</p><ul>{blockers.map(item=><li key={item.key}><strong>{item.title}</strong><p>{item.detail}</p></li>)}</ul><a href="#/instructor/assessments/new/constraints">Return to Rules &amp; Policy</a></section>}
    {!cases.length && status!=='generating' && <section className="tests-card tests-empty"><h2>No test cases {status==='blocked'?'generated':'returned'}</h2><p>{status==='blocked'?'Your Basic Details, selected sections and Rules & Policy remain in the local draft.':'The response contains no cases. Review the backend result before proceeding.'}</p></section>}
    {cases.length>0 && <p className="tests-note" role="status">{status==='partial'?'Partial response — the requested case set is incomplete. ':''}Counts reflect the available list. Output verification is shown only when supplied by a trusted generation result.</p>}
    {exceeded && <p className="tests-error" role="alert">Allocated case marks exceed assessment marks. Publishing is blocked.</p>}
    <>{cases.length!==generatedCount && <p className="tests-note" role="status">Showing {cases.length} cases in this local review. The generated set remains unchanged.</p>}</><div className="tests-list">{cases.map((item,index)=><article className="tests-card tests-case" key={item.key}><header><h2>Test Case {item.number??index+1}</h2><span>{item.marks==null?'Marks unspecified':`${item.marks} Marks`}</span></header>{item.description && <p>{item.description}</p>}<p className="tests-note">{item.id!=null?'Saved case':item.origin==='oracle-preview'?'Preview case':'Manual case'} · {item.changed?'Local changes, not saved':item.origin==='server'?'Saved test case':item.origin==='oracle-preview'?'Verified preview — not saved':'Manual entry'}</p>{item.custom && <p className="tests-note">Custom input · local name. Displayed output comes from the backend.{item.teacherOutput!==item.output && ' It differs from your teacher-entered suggestion; review it before saving.'}</p>}<div className="tests-input"><h3>Input</h3><pre>{item.input}</pre></div><div className="tests-output"><h3>Expected output</h3><pre>{item.output}</pre><p className="tests-note">{item.verification==='verified'&&!item.changed?'Verified by the generation source':'Verification not established — teacher review required'}</p></div><footer><button type="button" onClick={()=>onEdit ? onEdit(item) : open(item)}>Edit</button><button type="button" className="tests-delete" onClick={()=>setDeleting(item)}>Delete</button></footer></article>)}</div>
    <button className="tests-add" type="button" onClick={()=>onAdd ? onAdd() : open()} disabled={!onAdd&&(integrated||!inputSchema || blockers.length>0)}>＋ Add Custom Test Case</button><p className="tests-note">{integrated?'Custom inputs require a newly verified preview of exactly 10 cases. Saved cases cannot be extended or replaced here. ':''}Applying a local edit requires a confirmed input format. Case descriptions are local notes; per-case mark editing is unsupported. Local edits are not saved and expected outputs must be verified again.</p>
    <button className="tests-publish" type="button" disabled>Publish {label} <span aria-hidden="true">→</span></button><p className="tests-note">{integrated?<>{readyToPublish && 'The saved cases are ready for publishing review. '}Publishing is not enabled on this screen yet. Students will not see this assessment until it is published.</>:'Complete the review and verified test-case generation before publishing.'}</p>
    {editor && <ReviewDialog labelId="tests-editor-title" onClose={()=>{if(!busy)setEditor(null)}}><h2 id="tests-editor-title">{cases.some(item=>item.key===editor.key)?'Edit':'Add'} local test case</h2><form onSubmit={save}><label>Description (local note)<input autoFocus value={editor.description} onChange={event=>setEditor({...editor,description:event.target.value})}/></label><label>Input<textarea rows={5} value={editor.input} onChange={event=>setEditor({...editor,input:event.target.value})}/></label><label>Expected output<textarea rows={4} value={editor.output} onChange={event=>setEditor({...editor,output:event.target.value})}/></label><p>Applying changes validates input only. Expected output is unverified; nothing is saved to the server.</p>{error && <p className="tests-error" role="alert">{error}</p>}<div><button type="button" disabled={busy} onClick={()=>setEditor(null)}>Cancel</button><button type="submit" disabled={busy}>{busy?'Validating…':'Validate and apply locally'}</button></div></form></ReviewDialog>}
    {deleting && <ReviewDialog labelId="tests-delete-title" onClose={()=>setDeleting(null)}><h2 id="tests-delete-title">Remove this case from the local review?</h2><p>This changes only the visible local list. The backend case will not be deleted. A changed case set cannot be published.</p><div><button type="button" autoFocus onClick={()=>setDeleting(null)}>Cancel</button><button type="button" onClick={()=>{onChange(cases.filter(item=>item.key!==deleting.key));setDeleting(null)}}>Remove locally</button></div></ReviewDialog>}
  </>
}




