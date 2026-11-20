import InstructorNavigation from './InstructorNavigation'
import '../pages/InstructorDashboard.css'
import '../pages/InstructorProfile.css'

export default function InstructorProfileView({ identity, data, onLogout, onRetry }) {
  const teacher = identity?.user_id != null && data.teacher?.id === String(identity.user_id) ? data.teacher : null
  const name = teacher?.name || identity?.profile?.displayName || identity?.displayName
  const email = identity?.profile?.email
  const department = teacher?.department || identity?.profile?.department
  const courses = [...new Set((teacher && data.status === 'ready' ? data.sections : []).map(section => section.course).filter(Boolean))]

  return <div className="instructor-dashboard instructor-profile">
    <div className="dashboard-shell profile-shell">
      <InstructorNavigation active="Profile" />
      <main className="dashboard-main profile-main">
        <header className="profile-page-header"><h1>Profile</h1></header>
        <section className="profile-identity" aria-label="Instructor account">
          <span className="profile-avatar" aria-hidden="true">{name ? Array.from(name.trim())[0]?.toLocaleUpperCase() : '?'}</span>
          <div className="profile-identity-copy"><h2>{name || 'Instructor name unavailable'}</h2><p>{email || 'Email unavailable'}</p><span className="profile-account-badge">Instructor Account</span></div>
        </section>
        <section className="profile-information" aria-labelledby="profile-information-heading">
          <h2 id="profile-information-heading">Information</h2>
          <dl className="profile-information-card">
            <div><dt>Instructor ID</dt><dd>{identity?.user_id || 'Instructor ID unavailable'}</dd></div>
            <div><dt>My Courses</dt><dd aria-live="polite">{data.status === 'loading' ? 'Loading course information…' : courses.length ? courses.join(', ') : 'Course information unavailable'}{courses.length > 0 && <small>Confirmed for the current course scope</small>}</dd></div>
            <div><dt>Department</dt><dd>{department || 'Department unavailable'}</dd></div>
          </dl>
          {data.status === 'error' && <div className="profile-load-error" role="status"><p>Could not load the latest profile and course information.</p><button type="button" onClick={onRetry}>Retry</button></div>}
        </section>
        <button className="profile-logout" type="button" onClick={onLogout}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 4H4v16h6m4-12 4 4-4 4m-6-4h13" /></svg>Log Out</button>
      </main>
    </div>
  </div>
}

