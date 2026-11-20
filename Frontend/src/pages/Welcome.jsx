import Icon from '../components/Icon'
import RoleCard from '../components/RoleCard'

export default function Welcome() {
  return (
    <main className="welcome">
      <header className="brand">
        <span className="brand-icon"><Icon name="code" /></span>
        <h1>Auto Test Case <span>Generator</span></h1>
      </header>
      <nav className="role-options" aria-label="Choose your role">
        <RoleCard title="Instructor" description="Create and manage quizzes, assignments, test cases and evaluate submissions." icon="briefcase" href="#/instructor/login" />
        <RoleCard title="Student" description="View assigned quizzes, submit your code and check your results." icon="graduation" href="#/student/login" />
      </nav>
    </main>
  )
}
