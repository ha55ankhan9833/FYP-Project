import { apiRequest, ApiError } from './api'
import { getToken } from './auth'

// Shared current course scope, supplied for this workflow. Never a teacher ID.
export const TEACHER_SECTION_FILTERS = Object.freeze({ course_code: 'CSC-101', semester_no: 1, academic_session: '2025SM' })

// Completeness here means the complete result for the requested course scope.
export function adaptAssignedSections(records, { complete = false } = {}) {
  if (!complete) return { status: 'unavailable', sections: null }
  if (!Array.isArray(records)) throw new Error('Invalid assigned-section list')
  const seen = new Set()
  const sections = records.map(record => {
    if (!record || typeof record.section_id !== 'string' || !record.section_id.trim()) {
      throw new Error('Missing section identifier')
    }
    const id = record.section_id.trim()
    if (seen.has(id.toLowerCase())) throw new Error('Duplicate section identifier')
    seen.add(id.toLowerCase())
    return {
      id,
      name: typeof record.section_name === 'string' && record.section_name.trim() ? record.section_name.trim() : id,
      program: typeof record.program === 'string' ? record.program.trim() : null,
      academicSession: typeof record.academic_session === 'string' ? record.academic_session.trim() : null,
      studentCount: Number.isInteger(record.enrolled_students) && record.enrolled_students >= 0 ? record.enrolled_students : null,
      semester: typeof record.semester_no === 'number' || typeof record.semester_no === 'string' ? String(record.semester_no).trim() : null,
      course: typeof record.course_name === 'string' ? record.course_name.trim() : typeof record.course_code === 'string' ? record.course_code.trim() : null,
      courseCode: typeof record.course_code === 'string' ? record.course_code.trim() : null,
    }
  })
  return { status: sections.length ? 'ready' : 'empty', sections }
}

export function adaptTeacherSectionsResponse(payload, expectedTeacherId, filters = TEACHER_SECTION_FILTERS) {
  const teacherId = payload?.teacher?.teacher_id
  if (!expectedTeacherId || typeof teacherId !== 'string' || teacherId !== String(expectedTeacherId)) {
    throw new ApiError('Teacher identity mismatch', 403, 'WRONG_ROLE')
  }
  if (!Array.isArray(payload.sections) || payload.total !== payload.sections.length ||
    String(payload.filters?.course_code).toUpperCase() !== filters.course_code.toUpperCase() ||
    Number(payload.filters?.semester_no) !== filters.semester_no || payload.filters?.academic_session !== filters.academic_session ||
    payload.sections.some(section => section.teacher_id !== teacherId ||
      String(section.course_code).toUpperCase() !== filters.course_code.toUpperCase() ||
      Number(section.semester_no) !== filters.semester_no || section.academic_session !== filters.academic_session)) {
    throw new ApiError('Unexpected teacher-section response', 200, 'INVALID_RESPONSE')
  }
  return {
    ...adaptAssignedSections(payload.sections, { complete: true }),
    teacher: { id: teacherId, name: typeof payload.teacher.teacher_name === 'string' ? payload.teacher.teacher_name.trim() : null, department: typeof payload.teacher.department === 'string' ? payload.teacher.department.trim() : null },
    filters: payload.filters,
  }
}

export async function loadAssignedSections({ signal, expectedTeacherId } = {}) {
  signal?.throwIfAborted()
  const token = getToken()
  if (!token) throw new ApiError('Authentication required', 401, 'AUTH_REQUIRED')
  const query = new URLSearchParams(TEACHER_SECTION_FILTERS)
  const payload = await apiRequest(`/api/teacher/sections?${query}`, { token, signal })
  return adaptTeacherSectionsResponse(payload, expectedTeacherId)
}
