import { useState } from 'react'

export default function SectionSelectionForm({ data, initialIds = [], onContinue, onRetry }) {
  const [selectedIds, setSelectedIds] = useState(initialIds)
  const sections = data.status === 'ready' && Array.isArray(data.sections) ? data.sections : []
  const validIds = new Set(sections.map(section => section.id))
  const selection = [...new Set(selectedIds)].filter(id => validIds.has(id))

  function toggle(id, checked) {
    setSelectedIds(previous => checked ? [...new Set([...previous, id])] : previous.filter(value => value !== id))
  }

  function submit(event) {
    event.preventDefault()
    if (selection.length) onContinue(selection)
  }

  return <form className="section-selection-form" onSubmit={submit}>
    <div className="section-selection-content" aria-live="polite" aria-busy={data.status === 'loading'}>
      {data.status === 'loading' && <div className="section-selection-loading" role="status"><span className="selection-skeleton" /><span className="selection-skeleton" /><p>Loading your assigned sections…</p></div>}
      {data.status === 'empty' && <div className="section-selection-state"><h2>No Sections Assigned Yet</h2><p>Your assigned sections will appear here once they are configured.</p></div>}
      {['unavailable', 'error'].includes(data.status) && <div className="section-selection-state"><h2>Sections Unavailable</h2><p>We couldn&apos;t load your assigned sections right now.</p><button type="button" onClick={onRetry}>Retry</button></div>}
      {data.status === 'ready' && <fieldset className="section-selection-list"><legend className="selection-sr-only">Choose one or more assigned sections</legend>
        {sections.map(section => <label className="section-selection-card" key={section.id}>
          <input type="checkbox" name="sectionIds" value={section.id} checked={selection.includes(section.id)} onChange={event => toggle(section.id, event.target.checked)} />
          <span className="section-selection-copy"><strong>{section.program ? `${section.program}-${section.name}` : section.name}</strong><span>{section.studentCount == null ? 'Student count unavailable' : `${section.studentCount} students`}{section.semester ? ` • Semester ${section.semester}` : ''}{section.academicSession ? ` • ${section.academicSession}` : ''}</span></span>
        </label>)}
      </fieldset>}
    </div>
    <button className="selection-continue" type="submit" disabled={!selection.length}>Continue <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 12h16m-7-7 7 7-7 7" /></svg></button>
  </form>
}
