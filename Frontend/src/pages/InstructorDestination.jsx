import InstructorNavigation from '../components/InstructorNavigation'
import './InstructorDashboard.css'

export default function InstructorDestination({ title, active }) {
  return <div className="instructor-dashboard dashboard-destination">
    <main className="dashboard-destination-content"><p className="dashboard-placeholder-label">Screen placeholder</p><h1>{title}</h1><p>This screen is awaiting its Figma mockup.</p><a href="#/instructor/dashboard">Back to dashboard</a></main>
    <InstructorNavigation active={active ?? null} />
  </div>
}
