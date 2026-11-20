import { useState } from 'react'
import Icon from './Icon'
import InstructorNavigation from './InstructorNavigation'
import '../pages/InstructorDashboard.css'
import '../pages/SectionDetails.css'

export default function SectionDetailsView({ section, status, assessments, studentsOnly = false, backHref = '#/instructor/dashboard', createDisabled = false, onCreate, onRetry, onRetryAssessments }) {
  const [tab, setTab] = useState('assignments')
  const label = section ? (section.program ? `${section.program}-${section.name}` : section.name) : 'Section Details'
  const records = assessments?.status === 'ready' ? assessments[tab] : null
  const sectionPath = section ? `#/instructor/sections/${encodeURIComponent(section.id)}` : '#/instructor/dashboard'
  return <div className="instructor-dashboard section-details-page"><div className="dashboard-shell section-details-shell">
    <InstructorNavigation />
    <main className="dashboard-main section-details-main">
      <header className="section-details-header"><a className="section-details-back" href={studentsOnly ? sectionPath : backHref} aria-label={studentsOnly ? 'Back to section details' : 'Back to previous section screen'}><Icon name="back" /></a><div><h1>{studentsOnly ? `Students — ${label}` : label}</h1>{section && <p>{[section.semester && `Semester ${section.semester}`, section.courseCode, section.course, section.academicSession].filter(Boolean).join(' • ')}</p>}</div></header>
      {status === 'loading' && <div className="section-details-state" role="status">Loading your section…</div>}
      {status === 'error' && <div className="section-details-state" role="alert"><h2>Section unavailable</h2><p>We couldn&apos;t load your section right now.</p><button type="button" onClick={onRetry}>Retry</button></div>}
      {status === 'not-authorized' && <div className="section-details-state"><h2>Section not available</h2><p>This section is not assigned to your account in the current course scope.</p><a href="#/instructor/dashboard">Back to dashboard</a></div>}
      {status === 'ready' && section && (studentsOnly ? <div className="section-details-state"><p className="section-placeholder-label">Students screen placeholder</p><h2>Student roster unavailable</h2><p>A secure section roster and submission-status API is not available yet. No student records are displayed.</p></div> : <>
        <section className="section-overview" aria-labelledby="section-overview-heading"><h2 id="section-overview-heading">SECTION OVERVIEW</h2><dl>
          <div><dd>{section.studentCount ?? '—'}</dd><dt>Students</dt></div>
          <div><dd>{assessments?.status === 'ready' ? assessments.assignments.length : '—'}</dd><dt>Assignments</dt></div>
          <div><dd>{assessments?.status === 'ready' ? assessments.quizzes.length : '—'}</dd><dt>Quiz</dt></div>
        </dl></section>
        <button className="section-create-button" type="button" disabled={createDisabled} onClick={() => onCreate(section.id)}><span aria-hidden="true">+</span> Create Assignment / Quiz</button>
        {createDisabled && <p className="section-assessment-note">Your selection changed. <a href="#/instructor/assessments/new">Select your sections again.</a></p>}
        <div className="section-assessment-tabs" role="group" aria-label="Assessment type"><button type="button" aria-pressed={tab === 'assignments'} onClick={() => setTab('assignments')}>Assignments</button><button type="button" aria-pressed={tab === 'quizzes'} onClick={() => setTab('quizzes')}>Quizzes</button></div>
        <section className="section-assessments" aria-live="polite" aria-labelledby="section-assessments-heading"><div className="section-assessments-heading"><h2 id="section-assessments-heading">{tab === 'assignments' ? 'Assignments' : 'Quizzes'}</h2><span>{records ? `${records.length} Total` : '— Total'}</span></div>
          <p className="section-assessment-note">Assigned to this section. Active submission status is unavailable.</p>
          {assessments?.status === 'loading' && <p role="status">Loading section assessments…</p>}
          {assessments?.status === 'error' && <div className="section-details-state" role="alert"><p>Unable to load section assessments. Counts are unavailable.</p><button type="button" onClick={onRetryAssessments}>Retry</button></div>}
          {assessments?.status === 'unavailable' && <div className="section-details-state"><p>Assessment types could not be reliably classified. Counts are unavailable.</p></div>}
          {records?.length === 0 && <p className="section-confirmed-empty">No {tab} assigned to this section.</p>}
          {records?.length > 0 && <ul className="section-assessment-list">{records.map(record => <li key={record.id}><strong>{record.title}</strong><span>Assessment {record.id}</span></li>)}</ul>}
        </section>
        <section className="section-students-card"><div><h2>Students</h2><a href={`${sectionPath}/students`}>{section.studentCount == null ? 'View students' : `View all ${section.studentCount} students`}<Icon name="arrow" /></a></div><p>View enrolled students and their submission status.</p></section>
      </>)}
    </main>
  </div></div>
}
