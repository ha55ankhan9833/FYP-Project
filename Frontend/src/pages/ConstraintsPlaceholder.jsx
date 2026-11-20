import InstructorNavigation from '../components/InstructorNavigation'
import { useInstructorSession } from '../services/instructorSession'
import { getFlowSectionIds } from '../services/assessmentFlow'
import { validateBasicDetails } from '../services/assessmentBasicDetails'
import './InstructorDashboard.css'

export default function ConstraintsPlaceholder({ flow, draft }) {
  const identity = useInstructorSession()
  const ids = getFlowSectionIds(flow, identity?.user_id)
  const available = ids.length > 0 && draft?.ownerId === identity?.user_id && Object.keys(validateBasicDetails(draft.values)).length === 0
  return <div className="instructor-dashboard dashboard-destination"><main className="dashboard-destination-content"><p className="dashboard-placeholder-label">Temporary placeholder — Step 2 of 3</p><h1>Constraints</h1><p>{available ? 'Your basic details and selected section IDs are retained locally. No assessment or constraint draft has been saved to the backend.' : 'Choose your sections and complete Basic Details first.'}</p><p>This screen is awaiting its Figma mockup.</p><a href={ids.length ? '#/instructor/assessments/new/details' : '#/instructor/assessments/new'}>{ids.length ? 'Back to Basic Details' : 'Select Section'}</a></main><InstructorNavigation active="Drafts" /></div>
}
