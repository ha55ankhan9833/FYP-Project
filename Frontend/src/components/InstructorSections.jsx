import Icon from './Icon'

export default function InstructorSections({ data, onRetry }) {
  const confirmed = ['ready', 'empty'].includes(data.status)
  return <section className="dashboard-sections" aria-labelledby="sections-heading" aria-busy={data.status === 'loading'}>
    <div className="dashboard-section-heading"><h2 id="sections-heading">My Sections</h2>
      {confirmed && <span>{data.sections.length} Assigned Sections</span>}
    </div>
    <div className="dashboard-section-content" aria-live="polite">
      {data.status === 'loading' && <div className="dashboard-section-state"><span className="dashboard-skeleton" /><span className="dashboard-skeleton" /><p>Loading your sections…</p></div>}
      {data.status === 'unavailable' && <div className="dashboard-section-state dashboard-section-unavailable"><span className="dashboard-state-icon"><Icon name="sections" /></span><h3>Sections unavailable</h3><p>Assigned sections are not available through the current service. Your section list and student counts will appear here when supported.</p><button type="button" onClick={onRetry}>Check again</button></div>}
      {data.status === 'error' && <div className="dashboard-section-state" role="alert"><h3>Unable to load your sections</h3><p>We could not reach the service. Please try again.</p><button type="button" onClick={onRetry}>Try again</button></div>}
      {data.status === 'empty' && <div className="dashboard-section-state"><span className="dashboard-state-icon"><Icon name="sections" /></span><h3>No Sections Assigned Yet</h3><p>Your sections will appear here once they are assigned.</p></div>}
      {data.status === 'ready' && <div className="dashboard-section-list">{data.sections.map(section => <a className="dashboard-section-card" key={section.id} href={`#/instructor/sections/${encodeURIComponent(section.id)}`}>
        <span className="dashboard-section-icon"><Icon name="sections" /></span><span className="dashboard-section-copy"><strong>{section.program ? `${section.program}-${section.name}` : section.name}</strong><span>{section.studentCount === null ? 'Student count unavailable' : `${section.studentCount} students`}{section.semester ? ` • Semester ${section.semester}` : ''}{section.academicSession ? ` • ${section.academicSession}` : ''}</span></span><Icon name="arrow" className="dashboard-section-arrow" />
      </a>)}</div>}
    </div>
  </section>
}
