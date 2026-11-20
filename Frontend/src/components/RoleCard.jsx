import Icon from './Icon'

export default function RoleCard({ title, description, icon, href }) {
  return (
    <a className="role-card" href={href}>
      <span className="role-icon"><Icon name={icon} /></span>
      <span className="role-copy"><span className="role-title">{title}</span><span className="role-description">{description}</span></span>
      <Icon name="arrow" className="role-arrow" />
    </a>
  )
}
