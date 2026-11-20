import { useEffect, useState } from 'react'
import InstructorNavigation from '../components/InstructorNavigation'
import Icon from '../components/Icon'
import AssessmentBasicForm from '../components/AssessmentBasicForm'
import { basicDetailsForOwner } from '../services/assessmentBasicDetails'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds, resolveSelectedSections, selectionDashboardRoute } from '../services/assessmentFlow'
import { loadAssignedSections } from '../services/instructorDashboard'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'
import './AssessmentBasicDetails.css'

export default function AssessmentBasicDetails({ flow, draft, onChange, onContinue, onDiscard }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: [] })
  const [attempt, setAttempt] = useState(0)
  const values = basicDetailsForOwner(draft, identity?.user_id)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      const ids = getFlowSectionIds(flow, identity?.user_id)
      if (!ids.length) { window.location.replace('#/instructor/assessments/new'); return }
      try {
        const result = await loadAssignedSections({ signal: controller.signal, expectedTeacherId: identity.user_id })
        if (controller.signal.aborted) return
        const sections = resolveSelectedSections(result.sections, ids)
        if (!sections) { window.location.replace('#/instructor/assessments/new'); return }
        const codes = new Set(sections.map(section => section.courseCode))
        if (codes.size !== 1 || !sections[0].courseCode) { setData({ status:'course-error',sections:[] }); return }
        setData({ status:'ready',sections })
      } catch (error) {
        if (controller.signal.aborted) return
        if ([401,403,422].includes(error.status)) { clearSession();window.location.replace('#/instructor/login?reason=expired') }
        else setData({status:'error',sections:[]})
      }
    }
    load()
    return () => controller.abort()
  }, [flow, identity?.user_id, attempt])
  const title = values.type === 'QUIZ' ? 'Create Quiz' : 'Create Assignment'
  const sectionIds = getFlowSectionIds(flow, identity?.user_id)
  const back = selectionDashboardRoute(sectionIds)
  const first = data.sections[0]
  return <div className="instructor-dashboard assessment-basic-page"><div className="dashboard-shell assessment-basic-shell"><InstructorNavigation active="Drafts" /><main className="dashboard-main assessment-basic-main">
    <header className="basic-page-header"><a href={`#${back}`} aria-label="Back to selected sections"><Icon name="back" /></a><div><h1>{title}</h1>{first && <p>{[first.semester && `Semester ${first.semester}`, first.courseCode, first.course].filter(Boolean).join(' • ')}</p>}</div></header>
    <p className="basic-step-indicator"><span aria-hidden="true">●</span> Step 1 of 3: Basic Details</p>
    {data.status === 'loading' && <p className="basic-loading" role="status">Checking selected sections…</p>}
    {data.status === 'error' && <div className="section-details-state" role="alert"><p>Selected sections could not be loaded.</p><button type="button" onClick={() => {setData({status:'loading',sections:[]});setAttempt(value=>value+1)}}>Retry</button></div>}
    {data.status === 'course-error' && <div role="alert"><p>Selected sections must share a confirmed course.</p><a href="#/instructor/assessments/new">Select sections again</a></div>}
    {data.status === 'ready' && <><details className="basic-section-summary"><summary>{data.sections.length} selected section{data.sections.length === 1 ? '' : 's'}</summary><ul>{data.sections.map(section => <li key={section.id}>{section.program ? `${section.program}-${section.name}` : section.name} — {section.id}</li>)}</ul></details>
      <AssessmentBasicForm values={values} course={{code:first.courseCode,name:first.course}} onChange={next => onChange({ownerId:identity.user_id,values:next,course:{code:first.courseCode,name:first.course}})} onContinue={next => {onChange({ownerId:identity.user_id,values:next,course:{code:first.courseCode,name:first.course}});onContinue()}} onDiscard={onDiscard} />
    </>}
  </main></div></div>
}
