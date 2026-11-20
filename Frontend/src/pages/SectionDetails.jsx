import { useEffect, useState } from 'react'
import { useInstructorSession } from '../services/instructorSession'
import { clearSession } from '../services/auth'
import { loadAssignedSections } from '../services/instructorDashboard'
import { findAssignedSection, loadSectionAssessments } from '../services/sectionDetails'
import SectionDetailsView from '../components/SectionDetailsView'
import { getFlowSectionIds, resolveSelectedSections } from '../services/assessmentFlow'

export default function SectionDetails({ sectionId, flow, studentsOnly = false, onCreate }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', section: null })
  const [assessments, setAssessments] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    function handleAuth(error) {
      if ([401, 403, 422].includes(error.status)) {
        clearSession()
        window.location.replace('#/instructor/login?reason=expired')
        return true
      }
      return false
    }
    async function load() {
      let section
      try {
        const result = await loadAssignedSections({ signal: controller.signal, expectedTeacherId: identity?.user_id })
        if (controller.signal.aborted) return
        section = findAssignedSection(result.sections, sectionId)
        if (!section) { setData({ status: 'not-authorized', section: null }); return }
        const context = getFlowSectionIds(flow, identity.user_id)
        const ids = context.includes(section.id) ? context : [section.id]
        const validContext = resolveSelectedSections(result.sections, ids)
        setData({ status: 'ready', section, selectionIds: validContext ? ids : null })
      } catch (error) {
        if (!controller.signal.aborted && !handleAuth(error)) setData({ status: 'error', section: null })
        return
      }
      if (studentsOnly) return
      try {
        const result = await loadSectionAssessments({ section, ownerId: identity.user_id, signal: controller.signal })
        if (!controller.signal.aborted) setAssessments(result)
      } catch (error) {
        if (!controller.signal.aborted && !handleAuth(error)) setAssessments({ status: 'error' })
      }
    }
    load()
    return () => controller.abort()
  }, [sectionId, studentsOnly, identity?.user_id, attempt, flow])
  function retry() { setData({ status: 'loading', section: null }); setAssessments({ status: 'loading' }); setAttempt(value => value + 1) }
  const contextIds = getFlowSectionIds(flow, identity?.user_id)
  const inContext = contextIds.includes(sectionId)
  const backHref = inContext ? (contextIds.length > 1 ? '#/instructor/sections/overview' : '#/instructor/assessments/new') : '#/instructor/dashboard'
  return <SectionDetailsView section={data.section} status={data.status} assessments={assessments} studentsOnly={studentsOnly} backHref={backHref} createDisabled={data.status === 'ready' && !data.selectionIds} onRetry={retry} onRetryAssessments={retry} onCreate={() => { if (data.selectionIds) onCreate({ ownerId: identity.user_id, sectionIds: data.selectionIds }) }} />
}
