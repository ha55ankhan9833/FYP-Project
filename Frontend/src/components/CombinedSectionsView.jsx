import Icon from './Icon'
import InstructorNavigation from './InstructorNavigation'
import '../pages/InstructorDashboard.css'
import '../pages/SectionDetails.css'
import '../pages/CombinedSections.css'

export default function CombinedSectionsView({ status, sections = [], assessments, onRetry, onCreate }) {
  const enrollments = sections.length && sections.every(section => Number.isInteger(section.studentCount)) ? sections.reduce((sum, section) => sum + section.studentCount, 0) : null
  const courses = [...new Set(sections.map(section => section.course).filter(Boolean))]
  const semesters = [...new Set(sections.map(section => section.semester).filter(Boolean))]
  return <div className="instructor-dashboard section-details-page combined-sections-page"><div className="dashboard-shell section-details-shell">
    <InstructorNavigation /><main className="dashboard-main section-details-main">
      <header className="section-details-header"><a className="section-details-back" href="#/instructor/assessments/new" aria-label="Back to Select Section"><Icon name="back" /></a><div><h1>Selected Sections Overview</h1>{status === 'ready' && <p>{[...courses, ...semesters.map(value => `Semester ${value}`)].join(' • ')}</p>}</div></header>
      {status === 'loading' && <div className="section-details-state" role="status">Loading selected sections…</div>}
      {status === 'error' && <div className="section-details-state" role="alert"><h2>Selected sections unavailable</h2><p>We couldn&apos;t load your sections. Please try again.</p><button type="button" onClick={onRetry}>Retry</button></div>}
      {status === 'missing' && <div className="section-details-state"><h2>Select your sections again</h2><p>Selection is missing, changed, or no longer assigned to your account.</p><a href="#/instructor/assessments/new">Back to Select Section</a></div>}
      {status === 'ready' && <>
        <p className="combined-selection-count">{sections.length} selected sections</p>
        <section className="section-overview"><h2>SELECTED SECTIONS OVERVIEW</h2><dl><div><dd>{enrollments ?? '—'}</dd><dt>Total section enrollments</dt></div><div><dd>{assessments?.status === 'ready' ? assessments.assignments.length : '—'}</dd><dt>Assignments</dt></div><div><dd>{assessments?.status === 'ready' ? assessments.quizzes.length : '—'}</dd><dt>Quizzes</dt></div></dl></section>
        <p className="section-assessment-note">Enrollment totals may count a student in more than one section. Assessment totals count each assigned assessment once.</p>
        {assessments?.status === 'loading' && <p className="section-assessment-note" role="status">Loading assessment totals…</p>}
        {['error','unavailable'].includes(assessments?.status) && <div className="section-details-state"><p>Assessment totals are unavailable.</p><button type="button" onClick={onRetry}>Retry</button></div>}
        <section className="combined-section-list" aria-label="Selected sections">{sections.map(section => <a className="dashboard-section-card" href={`#/instructor/sections/${encodeURIComponent(section.id)}`} key={section.id}><span className="dashboard-section-icon"><Icon name="sections" /></span><span className="dashboard-section-copy"><strong>{section.program ? `${section.program}-${section.name}` : section.name}</strong><span>{section.courseCode} {section.course ? `• ${section.course}` : ''}</span><span>{section.studentCount == null ? 'Student count unavailable' : `${section.studentCount} students`}{section.semester ? ` • Semester ${section.semester}` : ''}{section.academicSession ? ` • ${section.academicSession}` : ''}</span></span><Icon name="arrow" className="dashboard-section-arrow" /></a>)}</section>
        <button type="button" className="section-create-button" onClick={onCreate}><span aria-hidden="true">+</span> Create Assignment / Quiz</button>
      </>}
    </main></div></div>
}
