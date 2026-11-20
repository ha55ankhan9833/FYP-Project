import { useEffect, useState, useSyncExternalStore } from 'react'
import Welcome from './pages/Welcome'
import LoginPlaceholder from './pages/LoginPlaceholder'
import InstructorLogin from './pages/InstructorLogin'
import InstructorGuard from './components/InstructorGuard'
import InstructorDashboard from './pages/InstructorDashboard'
import InstructorDestination from './pages/InstructorDestination'
import SelectSection from './pages/SelectSection'
import AssessmentBasicDetails from './pages/AssessmentBasicDetails'
import AssessmentRules from './pages/AssessmentRules'
import AssessmentTestCases from './pages/AssessmentTestCases'
import RubricSetup from './pages/RubricSetup'
import EditTestCase from './pages/EditTestCase'
import AddTestCase from './pages/AddTestCase'
import useAssessmentRules from './hooks/useAssessmentRules'
import useGenerationWorkflow from './hooks/useGenerationWorkflow'
import { isE2EWorkflow } from './services/generationWorkflow'
import './components/GenerationReviewPanel.css'
import InstructorProfile from './pages/InstructorProfile'
import { getSessionVersion, subscribeSession } from './services/auth'
import SectionDetails from './pages/SectionDetails'
import { createAssessmentFlow, selectionDashboardRoute } from './services/assessmentFlow'
import CombinedSections from './pages/CombinedSections'
import SectionStudents from './pages/SectionStudents'

const subscribe = (callback) => {
  window.addEventListener('hashchange', callback)
  return () => window.removeEventListener('hashchange', callback)
}
const getRoute = () => window.location.hash.slice(1) || '/'

export default function App() {
  return <>{isE2EWorkflow && <div className="e2e-environment-banner">E2E TEST ENVIRONMENT · Flask 127.0.0.1:5000 · FYP_E2E_Test · Production proxy remains unchanged</div>}<RoutedApp/></>
}

function RoutedApp() {
  const sessionVersion = useSyncExternalStore(subscribeSession, getSessionVersion)
  const route = useSyncExternalStore(subscribe, getRoute).split('?')[0]
  if (route === '/instructor/login') return <InstructorLogin />
  if (route.startsWith('/instructor/')) return <InstructorRoutes key={sessionVersion} route={route} />
  if (route === '/student/login') return <LoginPlaceholder role="Student" />
  if (route !== '/') return <main className="placeholder"><h1>Page not found</h1><a href="#/">Back to welcome</a></main>
  return <Welcome />
}

function InstructorRoutes({ route }) {
  const [assessmentFlow, setAssessmentFlow] = useState(null)
  const [basicDraft, setBasicDraft] = useState(null)
  const [testReview, setTestReview] = useState(null)
  const rulesController = useAssessmentRules()
  const generation=useGenerationWorkflow()
  const phase=generation.state.phase
  const [editTarget,setEditTarget]=useState(null)
  const [customDraft,setCustomDraft]=useState(null)
  function addCase(){window.location.hash='/instructor/assessments/new/test-cases/add'}
  function editCase(item) {
    setEditTarget({key:item.key,phase,assessmentId:generation.state.assessmentId,ownerId:generation.state.snapshot?.ownerId,returnRoute:route})
    window.location.hash='/instructor/assessments/new/test-cases/edit/'+encodeURIComponent(item.key)
  }

  useEffect(()=>{if(phase==='saved')window.location.hash='/instructor/assessments/new/test-cases'},[phase])
  function continueAssessment(selection) {
    if(generation.state.assessmentId){generation.reset();rulesController.reset();setBasicDraft(null)}
    setTestReview(null)
    setCustomDraft(null)
    setAssessmentFlow(createAssessmentFlow(selection.ownerId, selection.sectionIds))
    window.location.hash = '/instructor/assessments/new/details'
  }
  const destinations = {
    '/instructor/results': ['Results', 'Results'],
    '/instructor/notifications': ['Notifications', 'Notifications'],
    '/instructor/drafts': ['Drafts', 'Drafts'],
  }
  const destination = destinations[route]
  const title = destination?.[0] || (route.startsWith('/instructor/sections/') ? 'Section details' : 'Page not found')
  let screen = <InstructorDestination title={title} active={destination?.[1]} />
  if (route === '/instructor/dashboard') screen = <InstructorDashboard />
  if (route === '/instructor/profile') screen = <InstructorProfile />
  const sectionMatch = route.match(/^\/instructor\/sections\/([^/]+)(\/students)?$/)
  if (sectionMatch) {
    let sectionId
    try { sectionId = decodeURIComponent(sectionMatch[1]) } catch { sectionId = null }
    screen = <SectionDetails sectionId={sectionId} flow={assessmentFlow} studentsOnly={Boolean(sectionMatch[2])} onCreate={continueAssessment} />
  }
  if (route === '/instructor/sections/overview') screen = <CombinedSections flow={assessmentFlow} onCreate={continueAssessment} />
  const studentsMatch = route.match(/^\/instructor\/sections\/([^/]+)\/students(?:\/([^/]+))?$/)
  if (studentsMatch) {
    let sectionId, studentId
    try { sectionId = decodeURIComponent(studentsMatch[1]); studentId = studentsMatch[2] ? decodeURIComponent(studentsMatch[2]) : undefined } catch { sectionId = null; studentId = null }
    screen = <SectionStudents sectionId={sectionId} studentId={studentId} />
  }
  if (route === '/instructor/assessments/new') screen = <SelectSection flow={assessmentFlow} onContinue={selection => {
    const flow = createAssessmentFlow(selection.ownerId, selection.sectionIds)
    setAssessmentFlow(flow)
    window.location.hash = selectionDashboardRoute(flow.sectionIds)
  }} />
  if (route === '/instructor/assessments/new/details') screen = <AssessmentBasicDetails flow={assessmentFlow} draft={basicDraft} onChange={next=>{if(!generation.state.busy)setBasicDraft(next)}} onContinue={() => { window.location.hash = '/instructor/assessments/new/constraints' }} onDiscard={() => { generation.reset(); rulesController.reset(); setTestReview(null); setCustomDraft(null); setBasicDraft(null); setAssessmentFlow(null); window.location.hash = '/instructor/assessments/new' }} />
  if (route === '/instructor/assessments/new/constraints') screen = <AssessmentRules flow={assessmentFlow} basic={basicDraft} controller={rulesController} workflow={generation} onContinue={(basic,rules)=>{const opened=generation.open(basic,rules,assessmentFlow);if(opened)window.location.hash=opened==='saved'?'/instructor/assessments/new/test-cases':'/instructor/assessments/new/rubric'}} />
  if (route === '/instructor/assessments/new/rubric') screen = <RubricSetup workflow={generation} basic={basicDraft} rules={rulesController.rules} flow={assessmentFlow} review={testReview} onReviewChange={setTestReview} onEdit={editCase} onAdd={addCase}/>
  if (route === '/instructor/assessments/new/test-cases') screen = <AssessmentTestCases flow={assessmentFlow} basic={basicDraft} rules={rulesController.rules} review={testReview} onReviewChange={setTestReview} workflow={generation} onEdit={editCase} onAdd={addCase}/>
  if(route==='/instructor/assessments/new/test-cases/add') {
    const state=generation.state,ownerId=state.snapshot?.ownerId
    const local=customDraft && customDraft.ownerId===ownerId && customDraft.assessmentId===state.assessmentId
    const back=()=>{window.location.hash=phase==='saved'?'/instructor/assessments/new/test-cases':'/instructor/assessments/new/rubric'}
    screen=<AddTestCase outputFormat={state.snapshot?.values.outputFormat} schema={state.frozenConfig?.schema} draft={local?customDraft.form:null} onDraftChange={form=>setCustomDraft({ownerId,assessmentId:state.assessmentId,form})} saved={phase==='saved'} unavailable={!state.snapshot||!['preview','saved'].includes(phase)} busy={state.busy} error={state.error} onCancel={back} onAdd={async form=>{
      const success=await generation.addCustomPreview(form)
      if(success){setTestReview(null);setCustomDraft(null);if(window.location.hash==='#/instructor/assessments/new/test-cases/add')back()}
    }}/>
  }
  if(route.startsWith('/instructor/assessments/new/test-cases/edit/')) {
    const state=generation.state
    const target=editTarget
    const valid=target && target.assessmentId===state.assessmentId && target.ownerId===state.snapshot?.ownerId && target.phase===phase && route.endsWith('/'+encodeURIComponent(target.key))
    const local=valid && testReview?.ownerId===target.ownerId && testReview.assessmentId===target.assessmentId && testReview.phase===phase
    const cases=local?testReview.cases:phase==='saved'?state.cases:state.preview
    const item=valid?cases.find(candidate=>candidate.key===target.key):null
    const back=()=>{if(valid)window.history.back();else window.location.hash='/instructor/assessments/new/test-cases'}
    screen=<EditTestCase key={target?.key || route} item={item} schema={state.frozenConfig?.schema} saved={phase==='saved'} outputFormat={state.snapshot?.values.outputFormat} onCancel={back} onUpdate={next=>{
      if(!valid || phase!=='preview')return
      setTestReview({ownerId:target.ownerId,assessmentId:target.assessmentId,phase,cases:cases.map(candidate=>candidate.key===next.key?next:candidate)})
      back()
    }}/>
  }
  return <InstructorGuard key={route}>{screen}</InstructorGuard>
}



