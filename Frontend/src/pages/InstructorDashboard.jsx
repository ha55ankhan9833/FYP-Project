import { useEffect, useState } from 'react'
import Icon from '../components/Icon'
import InstructorNavigation from '../components/InstructorNavigation'
import InstructorSections from '../components/InstructorSections'
import { loadAssignedSections } from '../services/instructorDashboard'
import { useInstructorSession } from '../services/instructorSession'
import { clearSession } from '../services/auth'
import './InstructorDashboard.css'

export default function InstructorDashboard() {
  const identity = useInstructorSession()
  const [data, setData] = useState({ status: 'loading', sections: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    loadAssignedSections({ signal: controller.signal, expectedTeacherId: identity?.user_id }).then(result => {
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
  const semesters = [...new Set((data.sections || []).map(section => section.semester).filter(Boolean))]
  const courses = [...new Set((data.sections || []).map(section => section.course).filter(Boolean))]
  return <div className="instructor-dashboard">
    <div className="dashboard-shell">
      <div className="dashboard-brand"><Icon name="code-slash" /><span>AUTO TEST CASE GENERATOR</span></div>
      <InstructorNavigation />
      <main className="dashboard-main">
        <header className="dashboard-header">
          <h1>{data.teacher?.name || identity?.displayName ? `Hello, ${data.teacher?.name || identity.displayName}` : 'Hello, Instructor!'} <span role="img" aria-label="Waving hand">👋</span></h1>
          {(semesters.length > 0 || courses.length > 0) && <div className="dashboard-badges">{semesters.map(semester => <span key={`semester-${semester}`}>Semester {semester}</span>)}{courses.map(course => <span key={`course-${course}`}>{course}</span>)}</div>}
          <p>Manage assignments, quizzes and submissions for your assigned sections.</p>
        </header>
        <div className="dashboard-content">
          <section className="dashboard-create" aria-labelledby="create-heading">
            <h2 id="create-heading">Create &amp; Generate</h2>
            <p>Create an assignment or quiz for one or more of your assigned sections.</p>
            <a className="dashboard-create-button" href="#/instructor/assessments/new"><span aria-hidden="true">+</span> Create New Assignment / Quiz</a>
          </section>
          <InstructorSections data={data} onRetry={() => { setData({ status: 'loading', sections: null }); setAttempt(value => value + 1) }} />
        </div>
      </main>
    </div>
  </div>
}
