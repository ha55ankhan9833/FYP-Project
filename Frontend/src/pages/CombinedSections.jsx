import { useEffect, useState } from 'react'
import CombinedSectionsView from '../components/CombinedSectionsView'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds, resolveSelectedSections } from '../services/assessmentFlow'
import { loadAssignedSections } from '../services/instructorDashboard'
import { combineAssessmentResults, loadSectionAssessments } from '../services/sectionDetails'
import { clearSession } from '../services/auth'

export default function CombinedSections({ flow, onCreate }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: [] })
  const [assessments, setAssessments] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    function authError(error) {
      if (![401,403,422].includes(error.status)) return false
      clearSession()
      window.location.replace('#/instructor/login?reason=expired')
      return true
    }
    async function load() {
      const ids = getFlowSectionIds(flow, identity?.user_id)
      if (ids.length < 2) { setData({ status: 'missing', sections: [] }); return }
      let sections
      try {
        const result = await loadAssignedSections({ signal: controller.signal, expectedTeacherId: identity.user_id })
        if (controller.signal.aborted) return
        sections = resolveSelectedSections(result.sections, ids)
        if (!sections) { setData({ status: 'missing', sections: [] }); return }
        setData({ status: 'ready', sections })
      } catch (error) {
        if (!controller.signal.aborted && !authError(error)) setData({ status: 'error', sections: [] })
        return
      }
      try {
        const results = []
        for (const section of sections) {
          const result = await loadSectionAssessments({ section, ownerId: identity.user_id, signal: controller.signal })
          if (result.status !== 'ready') { if (!controller.signal.aborted) setAssessments({ status: 'unavailable' }); return }
          results.push(result)
        }
        if (!controller.signal.aborted) setAssessments(combineAssessmentResults(results))
      } catch (error) {
        if (!controller.signal.aborted && !authError(error)) setAssessments({ status: 'error' })
      }
    }
    load()
    return () => controller.abort()
  }, [flow, identity?.user_id, attempt])
  return <CombinedSectionsView status={data.status} sections={data.sections} assessments={assessments} onRetry={() => { setData({ status:'loading', sections:[] }); setAssessments({ status:'loading' }); setAttempt(value=>value+1) }} onCreate={() => onCreate({ ownerId: identity.user_id, sectionIds: data.sections.map(section => section.id) })} />
}
