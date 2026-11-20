import { useEffect, useState } from 'react'
import Icon from '../components/Icon'
import InstructorNavigation from '../components/InstructorNavigation'
import SectionSelectionForm from '../components/SectionSelectionForm'
import { loadAssignedSections } from '../services/instructorDashboard'
import { clearSession } from '../services/auth'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds } from '../services/assessmentFlow'
import './InstructorDashboard.css'
import './SelectSection.css'

export default function SelectSection({ flow, onContinue }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    loadAssignedSections({ signal: controller.signal, expectedTeacherId: identity?.user_id }).then(result => {
      if (controller.signal.aborted) return
      if (result?.status === 'empty' && Array.isArray(result.sections) && result.sections.length === 0) setData(result)
      else if (result?.status === 'ready' && Array.isArray(result.sections) && result.sections.length > 0 && result.sections.every(section => typeof section.id === 'string' && section.id.trim()) && new Set(result.sections.map(section => section.id)).size === result.sections.length) setData(result)
      else setData({ status: 'unavailable', sections: null })
    }).catch(error => {
      if (controller.signal.aborted) return
      if ([401, 403, 422].includes(error.status)) {
        clearSession()
        window.location.replace('#/instructor/login?reason=expired')
      } else setData({ status: 'unavailable', sections: null })
    })
    return () => controller.abort()
  }, [attempt, identity?.user_id])
  const initialIds = getFlowSectionIds(flow, identity?.user_id)
  return <div className="instructor-dashboard select-section-page">
    <div className="dashboard-shell select-section-shell">
      <InstructorNavigation />
      <main className="dashboard-main select-section-main">
        <a className="select-section-back" href="#/instructor/dashboard" aria-label="Back to Instructor Dashboard"><Icon name="back" /></a>
        <header className="select-section-header"><h1>Select Section</h1><p>Choose the section(s) to assign this assignment.</p></header>
        <SectionSelectionForm key={identity?.user_id || 'unverified'} data={data} initialIds={initialIds} onRetry={() => { setData({ status: 'loading', sections: null }); setAttempt(value => value + 1) }} onContinue={sectionIds => {
          if (identity?.role === 'teacher') onContinue({ ownerId: identity.user_id, sectionIds })
        }} />
      </main>
    </div>
  </div>
}
