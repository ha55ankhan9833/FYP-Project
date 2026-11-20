import { useEffect, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import Icon from '../components/Icon'
import AssessmentRulesForm from '../components/AssessmentRulesForm'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds, resolveSelectedSections } from '../services/assessmentFlow'
import { validateBasicDetails } from '../services/assessmentBasicDetails'
import { emptyRules, rulesSourceKey } from '../services/assessmentRules'
import { loadAssignedSections } from '../services/instructorDashboard'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'
import './AssessmentRules.css'

export default function AssessmentRules({ flow, basic, controller, onContinue, workflow }) {
  const identity = useInstructorSession()
  const [scope, setScope] = useState({status:'loading',sections:[]})
  const [attempt, setAttempt] = useState(0)
  const ensureAnalysis = controller.ensureAnalysis
  useEffect(() => {
    const request = new AbortController()
    async function load() {
      const ids = getFlowSectionIds(flow,identity?.user_id)
      if (!ids.length) {window.location.replace('#/instructor/assessments/new');return}
      if (basic?.ownerId!==identity?.user_id || Object.keys(validateBasicDetails(basic.values)).length) {window.location.replace('#/instructor/assessments/new/details');return}
      try {
        const result = await loadAssignedSections({signal:request.signal,expectedTeacherId:identity.user_id})
        if(request.signal.aborted)return
        const sections = resolveSelectedSections(result.sections,ids)
        if(!sections){window.location.replace('#/instructor/assessments/new');return}
        setScope({status:'ready',sections})
        ensureAnalysis(basic)
      }catch(error){
        if(request.signal.aborted)return
        if([401,403,422].includes(error.status)){clearSession();window.location.replace('#/instructor/login?reason=expired')}
        else setScope({status:'error',sections:[]})
      }
    }
    load()
    return()=>request.abort()
  },[flow,basic,identity?.user_id,ensureAnalysis,attempt])
  const rules = controller.rules?.ownerId===identity?.user_id ? controller.rules : emptyRules(identity?.user_id)
  const suggestSchema=workflow?.suggestSchema
  useEffect(()=>{if(scope.status==='ready'&&rules.backendDraftId&&suggestSchema)suggestSchema(basic,rules)},[scope.status,basic,rules,suggestSchema])
  return <div className="instructor-dashboard assessment-rules-page"><div className="dashboard-shell assessment-rules-shell"><InstructorNavigation active="Drafts"/><main className="dashboard-main assessment-rules-main">
    <a className="rules-back" href="#/instructor/assessments/new/details" aria-label="Back to Basic Details"><Icon name="back"/></a>
    <p className="rules-step-indicator"><span aria-hidden="true">●</span>Step 2 of 3: Rules &amp; Policy</p>
    {scope.status==='loading' && <p role="status">Checking selected sections…</p>}
    {scope.status==='error' && <div className="rules-card" role="alert"><p>Selected sections could not be loaded.</p><button type="button" onClick={()=>{setScope({status:'loading',sections:[]});setAttempt(value=>value+1)}}>Retry</button></div>}
    {scope.status==='ready' && <><p className="rules-context">{basic.values.type==='QUIZ'?'Quiz':'Assignment'} • {scope.sections.length} selected section{scope.sections.length===1?'':'s'} • Review your constraints before continuing</p>{!workflow?.state.frozenConfig && !workflow?.state.busy && <AssessmentRulesForm onContinue={()=>onContinue?.(basic,rules)} rules={rules} totalMarks={basic.values.totalMarks} sourceKey={rulesSourceKey(basic.values)} onChange={patch=>{workflow?.invalidateReview();controller.update(identity.user_id,patch)}} onRegenerate={()=>{workflow?.invalidateReview();ensureAnalysis(basic,{force:true})}} onApplyPending={()=>{workflow?.invalidateReview();controller.applyPending(identity.user_id,rulesSourceKey(basic.values))}} onKeepEdits={()=>controller.keepEdits(identity.user_id)}/>} {workflow?.state.frozenConfig && <section className="rules-card"><h2>Reviewed constraints</h2><p>These approved values are retained for this assessment.</p><h3>Value Constraints</h3><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{workflow.state.snapshot.valueConstraints.join('\n')}</pre><h3>Code Constraints</h3><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{workflow.state.snapshot.codeConstraints.join('\n') || 'No code constraints'}</pre><a href="#/instructor/assessments/new/rubric">Return to Rubric Setup</a></section>} {workflow?.state.error && <p role="alert" className="rules-field-error">{workflow.state.error.message}</p>}</>}
  </main></div></div>
}






