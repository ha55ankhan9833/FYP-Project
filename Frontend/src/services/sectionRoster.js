import { apiRequest, ApiError } from './api'
import { getToken } from './auth'
import { adaptAssignedSections, TEACHER_SECTION_FILTERS } from './instructorDashboard'

// Strip extra/private fields at the view-model boundary.
export function adaptSectionRoster(records, { sectionId, complete = false } = {}) {
  if (!complete) return { status: 'unavailable', students: null }
  if (!sectionId || !Array.isArray(records)) throw new Error('Invalid roster')
  const seen = new Set()
  const students = records.map(record => {
    if (!record || record.sectionId !== sectionId || typeof record.id !== 'string' || !record.id.trim() || (record.name != null && typeof record.name !== 'string') || seen.has(record.id)) {
      throw new Error('Invalid, duplicate or out-of-section student')
    }
    seen.add(record.id)
    return {
      id: record.id,
      name: typeof record.name === 'string' ? record.name.trim() || null : null,
      registrationNumber: typeof record.registrationNumber === 'string' ? record.registrationNumber.trim() : null,
      sectionId,
      enrollmentStatus: typeof record.enrollmentStatus === 'string' ? record.enrollmentStatus : null,
    }
  })
  return { status: students.length ? 'ready' : 'empty', students }
}

export function adaptRosterResponse(payload, sectionId) {
  const scope = payload?.section
  if (!scope || scope.section_id !== sectionId ||
    String(scope.course_code).toUpperCase() !== TEACHER_SECTION_FILTERS.course_code.toUpperCase() ||
    Number(scope.semester_no) !== TEACHER_SECTION_FILTERS.semester_no ||
    String(scope.academic_session).toUpperCase() !== TEACHER_SECTION_FILTERS.academic_session.toUpperCase() ||
    !Number.isInteger(payload.total) || payload.total < 0 || !Array.isArray(payload.students) || payload.total !== payload.students.length) {
    throw new ApiError('Invalid roster response', 200, 'INVALID_RESPONSE')
  }
  const rows = payload.students.map(student => {
    if (!student || typeof student.registration_no !== 'string' || !student.registration_no.trim()) throw new ApiError('Invalid roster response', 200, 'INVALID_RESPONSE')
    return { id: student.student_id, name: student.student_name, registrationNumber: student.registration_no, sectionId: student.section_id, enrollmentStatus: student.enrollment_status }
  })
  const roster = adaptSectionRoster(rows, { sectionId, complete: true })
  const section = adaptAssignedSections([{ ...scope, enrolled_students: payload.total }], { complete: true }).sections[0]
  return { ...roster, section, total: payload.total }
}

export async function loadSectionRoster({ sectionId, signal }) {
  signal?.throwIfAborted()
  const token = getToken()
  if (!token) throw new ApiError('Authentication required', 401, 'AUTH_REQUIRED')
  if (typeof sectionId !== 'string' || !sectionId.trim() || sectionId.length > 20) throw new ApiError('Section not found', 404, 'SECTION_NOT_FOUND')
  const query = new URLSearchParams(TEACHER_SECTION_FILTERS)
  const payload = await apiRequest(`/api/teacher/sections/${encodeURIComponent(sectionId)}/students?${query}`, { token, signal })
  return adaptRosterResponse(payload, sectionId)
}

export function searchRoster(students, query) {
  const normalize = value => String(value || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase()
  const term = normalize(query)
  const registrationTerm = term.replace(/\s+/g, '')
  return students.filter(student => normalize(student.name).includes(term) || normalize(student.registrationNumber).replace(/\s+/g, '').includes(registrationTerm))
}

export function studentInitials(name) {
  return String(name || '').trim().split(/\s+/).filter(Boolean).slice(0, 2).map(word => Array.from(word)[0].toLocaleUpperCase()).join('') || '?'
}
