import { useEffect, useState } from 'react'
import { useInstructorSession } from '../services/instructorSession'
import { clearSession } from '../services/auth'
import { loadSectionRoster } from '../services/sectionRoster'
import SectionStudentsView from '../components/SectionStudentsView'

export default function SectionStudents({ sectionId, studentId }) {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', section: null })
  const [roster, setRoster] = useState({ status: 'loading', students: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        // Flask authorizes the JWT teacher's exact section/course allocation
        // before returning any section metadata or private roster information.
        const result = await loadSectionRoster({ sectionId, signal: controller.signal })
        if (controller.signal.aborted) return
        setData({ status: 'ready', section: result.section })
        setRoster(result)
      } catch (error) {
        if (controller.signal.aborted) return
        if ([401, 422].includes(error.status) || (error.status === 403 && error.code === 'TEACHER_ONLY')) {
          clearSession()
          window.location.replace('#/instructor/login?reason=expired')
          return
        }
        setRoster({ status: 'error', students: null })
        setData({ status: error.status === 403 ? 'access-denied' : error.status === 404 ? 'not-found' : 'error', section: null })
      }
    }
    load()
    return () => controller.abort()
  }, [sectionId, studentId, identity?.user_id, attempt])
  return <SectionStudentsView key={`${sectionId}/${studentId || ''}`} section={data.section} sectionId={sectionId} status={data.status} roster={roster} studentId={studentId} onRetry={() => {
    setData({ status: 'loading', section: null })
    setRoster({ status: 'loading', students: null })
    setAttempt(value => value + 1)
  }} />
}
