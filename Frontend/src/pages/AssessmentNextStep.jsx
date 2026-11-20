import { useInstructorSession } from '../services/instructorSession'
import InstructorNavigation from '../components/InstructorNavigation'
import { getFlowSectionIds, resolveSelectedSections } from '../services/assessmentFlow'
import { loadAssignedSections } from '../services/instructorDashboard'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'

export default function AssessmentNextStep({ flow }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: [] })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      const ids = getFlowSectionIds(flow, identity?.user_id)
      if (!ids.length) { setData({ status:'missing', sections:[] }); return }
      try {
        const result = await loadAssignedSections({ signal:controller.signal, expectedTeacherId:identity.user_id })
        if (controller.signal.aborted) return
        const sections = resolveSelectedSections(result.sections, ids)
        setData(sections ? {status:'ready',sections} : {status:'missing',sections:[]})
      } catch (error) {
        if (controller.signal.aborted) return
        if ([401,403,422].includes(error.status)) { clearSession(); window.location.replace('#/instructor/login?reason=expired') }
        else setData({status:'error',sections:[]})
      }
    }
    load()
    return () => controller.abort()
  }, [flow, identity?.user_id, attempt])
  return <div className="instructor-dashboard dashboard-destination">
    <main className="dashboard-destination-content"><p className="dashboard-placeholder-label">Temporary next-step placeholder</p><h1>Assessment creation</h1>
      {data.status === 'loading' && <p role="status">Checking selected sections…</p>}
      {data.status === 'missing' && <p>Selection is missing or no longer assigned. Choose your sections again.</p>}
      {data.status === 'error' && <div role="alert"><p>Selected sections could not be loaded.</p><button type="button" onClick={() => { setData({status:'loading',sections:[]}); setAttempt(value=>value+1) }}>Retry</button></div>}
      {data.status === 'ready' && <><p>{data.sections.length} section(s) selected locally. No assessment or section assignment has been saved.</p><ul aria-label="Selected section IDs">{data.sections.map(section => <li key={section.id}>{section.program ? `${section.program}-${section.name}` : section.name} — {section.id}</li>)}</ul></>}
      <p>The next screen is awaiting its Figma mockup.</p><a href="#/instructor/assessments/new">Back to Select Section</a>
    </main><InstructorNavigation active={null} />
  </div>
}
import { useEffect, useState } from 'react'
