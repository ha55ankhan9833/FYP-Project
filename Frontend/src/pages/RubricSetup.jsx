import { useEffect, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import Icon from '../components/Icon'
import GenerationReviewPanel from '../components/GenerationReviewPanel'
import TestCasesReview from '../components/TestCasesReview'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds } from '../services/assessmentFlow'
import './InstructorDashboard.css'
import './AssessmentTestCases.css'

export default function RubricSetup({workflow,basic,rules,flow,review,onReviewChange,onEdit,onAdd}){
  const identity=useInstructorSession(),s=workflow.state
  const [confirmed,setConfirmed]=useState(false)
  const loadAllocation=workflow.loadAllocation,suggestRubric=workflow.suggestRubric
  const ids=getFlowSectionIds(flow,identity?.user_id)
  const valid=s.snapshot?.ownerId===identity?.user_id&&ids.length>0&&ids.length===s.snapshot.sectionIds.length&&ids.every(id=>s.snapshot.sectionIds.includes(id))
  useEffect(()=>{if(!valid)window.location.replace('#/instructor/assessments/new/constraints')},[valid])
  useEffect(()=>{if(!valid||s.phase==='saved')return;const timer=setTimeout(()=>loadAllocation(s.snapshot.values.totalMarks,s.config.validationPercentage),250);return()=>clearTimeout(timer)},[valid,s.phase,s.snapshot,s.config.validationPercentage,loadAllocation])
  useEffect(()=>{if(valid&&s.phase==='rubric'&&s.allocationStatus==='ready')suggestRubric()},[valid,s.phase,s.assessmentId,s.config.validationPercentage,s.allocationStatus,suggestRubric])
  if(!valid)return <p role="status">Returning to your assessment details…</p>
  const local=review?.ownerId===identity.user_id&&review.assessmentId===s.assessmentId&&review.phase==='preview'
  const cases=local?review.cases:s.preview
  const dirty=local&&(cases.length!==s.preview.length||cases.some(item=>item.changed))
  return <div className="instructor-dashboard assessment-tests-page"><div className="dashboard-shell assessment-tests-shell"><InstructorNavigation active="Drafts"/><main className="dashboard-main assessment-tests-main"><a className="tests-back" href="#/instructor/assessments/new/constraints" aria-label="Back to Rules and Policy"><Icon name="back"/></a><GenerationReviewPanel workflow={workflow} onRetryInput={()=>workflow.suggestSchema(basic,rules,{force:true})}/>
    {s.phase==='preview' && <><section className="tests-card"><h2>Review your verified test cases</h2><button type="button" disabled={s.busy} onClick={async()=>{await workflow.revisePreview();onReviewChange(null);setConfirmed(false)}}>Revise rubric or weightage</button><p>{s.preview.length} genuine cases returned. Review the preview before saving; saving does not publish.</p><label className="rubric-check"><input type="checkbox" checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}/>I reviewed these test cases and want to save them.</label><button className="rubric-primary" type="button" disabled={!confirmed||dirty||s.busy} onClick={workflow.save}>{s.busy?'Saving verified cases…':'Save test cases & continue'}</button>{dirty && <><p role="alert">Local edits cannot be saved through generation. Restore the original preview before saving.</p><button type="button" onClick={()=>onReviewChange(null)}>Restore original preview</button></>}</section><TestCasesReview onAdd={onAdd} onEdit={onEdit} generatedCount={s.preview.length} cases={cases} onChange={next=>onReviewChange({ownerId:identity.user_id,assessmentId:s.assessmentId,phase:'preview',cases:next})} totalMarks={s.snapshot.values.totalMarks} type={s.snapshot.values.type} inputSchema={s.frozenConfig.schema} integrated status="preview"/></>}
  </main></div></div>
}

