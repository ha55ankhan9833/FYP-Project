import { useEffect, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import Icon from '../components/Icon'
import TestCasesReview from '../components/TestCasesReview'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds, resolveSelectedSections } from '../services/assessmentFlow'
import { validateBasicDetails } from '../services/assessmentBasicDetails'
import { validateRules } from '../services/assessmentRules'
import { generationBlockers } from '../services/testCaseReview'
import { loadAssignedSections } from '../services/instructorDashboard'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'
import './AssessmentTestCases.css'

import '../components/GenerationReviewPanel.css'

export default function AssessmentTestCases({flow,basic,rules,review,onReviewChange,workflow,onEdit,onAdd}) {
  const identity=useInstructorSession()
  const hasWorkflow=Boolean(workflow),workflowPhase=workflow?.state.phase,hasSnapshot=Boolean(workflow?.state.snapshot)
  const [scope,setScope]=useState({status:'loading',sections:[]})
  const [attempt,setAttempt]=useState(0)
  useEffect(()=>{
    const controller=new AbortController()
    async function load(){
      if(hasWorkflow && workflowPhase!=='saved'){window.location.replace(hasSnapshot?'#/instructor/assessments/new/rubric':'#/instructor/assessments/new/constraints');return}
      const ids=getFlowSectionIds(flow,identity?.user_id)
      if(!ids.length){window.location.replace('#/instructor/assessments/new');return}
      if(!basic || basic.ownerId!==identity?.user_id || Object.keys(validateBasicDetails(basic.values)).length){window.location.replace('#/instructor/assessments/new/details');return}
      if(!rules || rules.ownerId!==identity?.user_id || (workflowPhase!=='saved' && Object.keys(validateRules(rules,basic.values.totalMarks)).length)){window.location.replace('#/instructor/assessments/new/constraints');return}
      try{
        const response=await loadAssignedSections({expectedTeacherId:identity.user_id,signal:controller.signal})
        if(controller.signal.aborted)return
        const sections=resolveSelectedSections(response.sections,ids)
        if(!sections){window.location.replace('#/instructor/assessments/new');return}
        setScope({status:'ready',sections})
      }catch(error){
        if(controller.signal.aborted)return
        if([401,403,422].includes(error.status)){clearSession();window.location.replace('#/instructor/login?reason=expired')}
        else setScope({status:'error',sections:[]})
      }
    }
    load();return()=>controller.abort()
  },[flow,basic,rules,identity?.user_id,attempt,workflowPhase,hasWorkflow,hasSnapshot])
  const generated=workflow?.state
  const phase=generated?.phase
  const integrated=['preview','saved'].includes(phase)
  const source=phase==='saved'?generated.cases:generated?.preview||[]
  const hasLocal=review?.ownerId===identity?.user_id && review.assessmentId===generated?.assessmentId && review.phase===phase
  const cases=hasLocal?review.cases:source
  const dirty=hasLocal&&(cases.length!==source.length||cases.some(item=>item.changed))
  const values=generated?.snapshot?.values||basic?.values
  return <div className="instructor-dashboard assessment-tests-page"><div className="dashboard-shell assessment-tests-shell"><InstructorNavigation active="Drafts"/><main className="dashboard-main assessment-tests-main"><a className="tests-back" href="#/instructor/assessments/new/rubric" aria-label="Back to Rubric Setup"><Icon name="back"/></a><p className="tests-step">● Step 3 of 3: Test Cases</p>
    {scope.status==='loading' && <p role="status">Checking selected sections…</p>}
    {scope.status==='error' && <section className="tests-card" role="alert"><p>Selected sections could not be verified. Generation and publication are blocked.</p><button type="button" onClick={()=>{setScope({status:'loading',sections:[]});setAttempt(value=>value+1)}}>Retry</button></section>}
    {scope.status==='ready' && <><p className="tests-context">{values.title} · {values.type==='QUIZ'?'Quiz':'Assignment'} · {scope.sections.length} selected section{scope.sections.length===1?'':'s'}</p><details className="tests-section-context"><summary>Selected sections</summary><ul>{scope.sections.map(section=><li key={section.id}>{section.program}-{section.name} · {section.id}</li>)}</ul></details>
      {integrated && <><p className="tests-note">Test allocation: {generated.allocation?.test_marks} · Rubric allocation: {generated.allocation?.rubric_marks}. {phase==='saved'?'Saved cases retrieved from backend.':'Preview only; not saved.'}</p>{generated.error && <p className="tests-error" role="alert">{generated.error.message}</p>} {dirty && <button type="button" onClick={()=>onReviewChange(null)}>Restore backend {phase==='preview'?'preview':'cases'}</button>}</>}
      <TestCasesReview onAdd={onAdd} onEdit={onEdit} generatedCount={source.length} cases={cases} onChange={next=>onReviewChange({ownerId:identity.user_id,assessmentId:generated?.assessmentId,phase,cases:next})} totalMarks={values.totalMarks} type={values.type} blockers={integrated?[]:generationBlockers(rules)} inputSchema={generated?.frozenConfig?.schema||rules.inputSchema} integrated={integrated} readyToPublish={phase==='saved'&&!dirty&&generated?.readiness?.ready_to_publish} status={integrated?phase:'blocked'}/></>}
  </main></div></div>
}


