export function createAssessmentFlow(ownerId, sectionIds) {
  if (ownerId == null || !Array.isArray(sectionIds) || !sectionIds.length ||
    sectionIds.some(id => typeof id !== 'string' || !id.trim()) || new Set(sectionIds).size !== sectionIds.length) {
    throw new Error('A verified owner and valid unique section IDs are required')
  }
  return { ownerId, sectionIds: [...sectionIds] }
}

export function getFlowSectionIds(flow, ownerId) {
  if (ownerId == null || flow?.ownerId !== ownerId || !Array.isArray(flow.sectionIds)) return []
  return [...flow.sectionIds]
}

export function selectionDashboardRoute(sectionIds) {
  if (!sectionIds.length) return '/instructor/assessments/new'
  return sectionIds.length === 1 ? `/instructor/sections/${encodeURIComponent(sectionIds[0])}` : '/instructor/sections/overview'
}

export function resolveSelectedSections(sections, sectionIds) {
  if (!sectionIds.length || !Array.isArray(sections)) return null
  const matches = sectionIds.map(id => sections.find(section => section.id === id))
  return matches.every(Boolean) ? matches : null
}
