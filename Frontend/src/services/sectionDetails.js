import { apiRequest, ApiError } from './api'
import { getToken } from './auth'

export function findAssignedSection(sections, sectionId) {
  return Array.isArray(sections) ? sections.find(section => section.id === sectionId) || null : null
}

export function summarizeSectionAssessments(records) {
  if (records.some(record => !['assignment', 'quiz'].includes(String(record.assessment_type).trim().toLowerCase()))) {
    return { status: 'unavailable', assignments: null, quizzes: null }
  }
  const normalize = record => ({ id: record.assessment_id, title: typeof record.title === 'string' && record.title.trim() ? record.title : `Assessment ${record.assessment_id}` })
  return {
    status: 'ready',
    assignments: records.filter(record => record.assessment_type.trim().toLowerCase() === 'assignment').map(normalize),
    quizzes: records.filter(record => record.assessment_type.trim().toLowerCase() === 'quiz').map(normalize),
  }
}

export function combineAssessmentResults(results) {
  const assignments = new Map(), quizzes = new Map()
  for (const result of results) {
    if (result.status !== 'ready') return { status: 'unavailable' }
    result.assignments.forEach(record => assignments.set(record.id, record))
    result.quizzes.forEach(record => quizzes.set(record.id, record))
  }
  if ([...assignments.keys()].some(id => quizzes.has(id))) return { status: 'unavailable' }
  return { status: 'ready', assignments: [...assignments.values()], quizzes: [...quizzes.values()] }
}

export async function loadSectionAssessments({ section, ownerId, signal }) {
  const token = getToken()
  if (!token) throw new ApiError('Authentication required', 401, 'AUTH_REQUIRED')
  if (!section?.id || !section.courseCode || !ownerId) throw new ApiError('Invalid section scope', 200, 'INVALID_RESPONSE')
  const payload = await apiRequest('/api/assessments', { token, signal })
  if (!Array.isArray(payload.assessments) || payload.total !== payload.assessments.length ||
    payload.assessments.some(record => String(record.teacher_id) !== String(ownerId) || typeof record.course_code !== 'string' || !record.course_code.trim() || !Number.isInteger(record.assessment_id) || record.assessment_id < 1) ||
    new Set(payload.assessments.map(record => record.assessment_id)).size !== payload.assessments.length) {
    throw new ApiError('Invalid assessment response', 200, 'INVALID_RESPONSE')
  }
  const candidates = payload.assessments.filter(record => typeof record.course_code === 'string' && record.course_code.toUpperCase() === section.courseCode?.toUpperCase())
  const assigned = []
  // Bound read concurrency; never interpret a failed lookup as no assignments.
  for (let offset = 0; offset < candidates.length; offset += 4) {
    signal?.throwIfAborted()
    const batch = await Promise.all(candidates.slice(offset, offset + 4).map(async assessment => {
      const response = await apiRequest(`/api/assessments/${assessment.assessment_id}/sections`, { token, signal })
      if (response.assessment_id !== assessment.assessment_id || !Array.isArray(response.sections) || response.total !== response.sections.length || response.sections.some(item => typeof item.section_id !== 'string')) {
        throw new ApiError('Invalid section assignment response', 200, 'INVALID_RESPONSE')
      }
      return response.sections.some(item => item.section_id === section.id) ? assessment : null
    }))
    assigned.push(...batch.filter(Boolean))
  }
  return summarizeSectionAssessments(assigned)
}
