import { useState } from 'react'
import Icon from './Icon'
import InstructorNavigation from './InstructorNavigation'
import { searchRoster, studentInitials } from '../services/sectionRoster'
import '../pages/InstructorDashboard.css'
import '../pages/SectionDetails.css'
import '../pages/SectionStudents.css'

export default function SectionStudentsView({ section, status, roster, studentId, sectionId, onRetry }) {
  const [query, setQuery] = useState('')
  const students = roster?.status === 'ready' ? roster.students : []
  const matches = searchRoster(students, query)
  const searching = Boolean(query.trim())
  const routeSectionId = section?.id || sectionId
  const path = routeSectionId ? `#/instructor/sections/${encodeURIComponent(routeSectionId)}` : '#/instructor/dashboard'
  const currentStudent = studentId && students.find(student => student.id === studentId)
  return <div className="instructor-dashboard section-students-page"><div className="dashboard-shell section-students-shell">
    <InstructorNavigation /><main className="dashboard-main section-students-main">
      <header className="section-details-header"><a className="section-details-back" href={studentId ? `${path}/students` : path} aria-label={studentId ? 'Back to students list' : 'Back to section details'}><Icon name="back" /></a><div><h1>{studentId ? 'Student Details' : 'Students'}</h1>{section && <p>{[section.program ? `${section.program}-${section.name}` : section.name, section.semester && `Semester ${section.semester}`, section.courseCode, section.course].filter(Boolean).join(' • ')}</p>}</div></header>
      {status === 'loading' && <div className="students-list-state" role="status">Loading section…</div>}
      {status === 'not-authorized' && <div className="students-list-state"><h2>Section not available</h2><p>This section is not assigned to your account in the current course scope.</p><a href="#/instructor/dashboard">Back to dashboard</a></div>}
      {status === 'access-denied' && <div className="students-list-state" role="alert"><h2>Access denied</h2><p>You do not have access to this section&apos;s roster.</p><a href="#/instructor/dashboard">Back to dashboard</a></div>}
      {status === 'not-found' && <div className="students-list-state" role="alert"><h2>Section not found</h2><p>This section is not available in the current course and term.</p><a href="#/instructor/dashboard">Back to dashboard</a></div>}
      {status === 'error' && <div className="students-list-state" role="alert"><h2>Student list unavailable</h2><p>Student list is currently unavailable. Please try again.</p><button type="button" onClick={onRetry}>Retry</button></div>}
      {status === 'ready' && section && (studentId ? <div className="students-list-state">
        <p className="student-placeholder-label">Temporary Student Details placeholder</p><h2>{currentStudent ? currentStudent.name : 'Student details unavailable'}</h2>
        <p>{currentStudent ? `Student ID: ${currentStudent.id}` : 'An authorized roster is required before this student can be displayed.'}</p><p>This screen is awaiting its Figma mockup. No performance data is shown.</p>
      </div> : <>
        <div className="students-search"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></svg>
          <input type="search" aria-label="Search students by name or registration number" placeholder="Search by name or roll number..." value={query} onChange={event => setQuery(event.target.value)} disabled={roster?.status !== 'ready'} />
          {query && <button type="button" aria-label="Clear search" onClick={() => setQuery('')}>×</button>}
        </div>
        <h2 className="students-count-heading">Students{section.studentCount == null ? ' (count unavailable)' : ` (${section.studentCount})`}</h2>
        <div className="students-list-content" aria-live="polite" aria-busy={roster?.status === 'loading'}>
          {roster?.status === 'loading' && <div role="status"><span className="students-card-skeleton" /><span className="students-card-skeleton" /><p>Loading student list…</p></div>}
          {roster?.status === 'empty' && <div className="students-list-state"><h2>No Students Enrolled</h2></div>}
          {['unavailable','error'].includes(roster?.status) && <div className="students-list-state"><h2>Student list unavailable</h2><p>Student list is currently unavailable.</p><button type="button" onClick={onRetry}>Retry</button></div>}
          {roster?.status === 'ready' && <>
            {searching && <p className="students-search-summary">Showing {matches.length} of {section.studentCount ?? students.length} students</p>}
            {!matches.length && <div className="students-list-state"><p>No students match your search.</p></div>}
            <ul className="students-card-list">{matches.map(student => <li key={student.id}><a className="student-roster-card" href={`${path}/students/${encodeURIComponent(student.id)}`}><span className={`student-initials student-palette-${[...student.id].reduce((sum, char) => sum + char.codePointAt(0), 0) % 4}`} aria-hidden="true">{studentInitials(student.name)}</span><span className="student-roster-copy"><strong>{student.name || 'Name unavailable'}</strong><span>{student.registrationNumber || 'Registration number unavailable'}</span></span><Icon name="arrow" /></a></li>)}</ul>
          </>}
        </div>
      </>)}
    </main>
  </div></div>
}

