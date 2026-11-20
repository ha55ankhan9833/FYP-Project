import { useEffect, useState } from 'react'
import InstructorProfileView from '../components/InstructorProfileView'
import { useInstructorSession } from '../services/instructorSession'
import { clearSession } from '../services/auth'
import { loadAssignedSections } from '../services/instructorDashboard'
import './InstructorDashboard.css'
import './InstructorProfile.css'

export default function InstructorProfile() {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    loadAssignedSections({ expectedTeacherId: identity?.user_id, signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData(result)
    }).catch(error => {
      if (controller.signal.aborted) return
      if ([401, 403, 422].includes(error.status)) {
        clearSession()
        window.location.replace('#/instructor/login?reason=expired')
      } else setData({ status: 'error', sections: null })
    })
    return () => controller.abort()
  }, [attempt, identity?.user_id])
  function logout() {
    clearSession()
    window.location.replace('#/instructor/login')
  }

  return <InstructorProfileView identity={identity} data={data} onLogout={logout} onRetry={() => {
    setData({ status: 'loading', sections: null })
    setAttempt(value => value + 1)
  }} />
}
