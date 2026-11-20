import Icon from './Icon'

const destinations = [
  ['Home', 'home', '/instructor/dashboard'],
  ['Results', 'results', '/instructor/results'],
  ['Notifications', 'bell', '/instructor/notifications'],
  ['Drafts', 'drafts', '/instructor/drafts'],
  ['Profile', 'profile', '/instructor/profile'],
]

export default function InstructorNavigation({ active = 'Home' }) {
  return <nav className="instructor-nav" aria-label="Instructor navigation">
    {destinations.map(([label, icon, route]) => <a key={label} href={`#${route}`} className={active === label ? 'is-active' : ''} aria-current={active === label ? 'page' : undefined}>
      <span className="instructor-nav-icon"><Icon name={icon} /></span><span className="instructor-nav-label">{label}</span>
    </a>)}
  </nav>
}
