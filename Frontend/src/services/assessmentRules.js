import { apiRequest, ApiError } from './api'
import { getToken } from './auth'

export function emptyRules(ownerId) {
  return {
    ownerId, dueDate: '', cutoffTime: '', allowLate: false, latePenalty: '',
    valueConstraints: '', codeConstraints: '', additionalConstraints: '', editedConstraints: false,
    backendDraftId: null, inputSchema: null, approval: { value: false, code: false, finalized: false },
    ai: { status: 'idle', attemptedKey: null, appliedKey: null, result: null, pending: null, error: null },
  }
}

export function rulesSourceKey(values) {
  return JSON.stringify([values.problemStatement, values.inputFormat, values.outputFormat])
}

export function normalizeConstraintAnalysis(data) {
  if (!data || !Array.isArray(data.value_constraints) || (data.code_constraints != null && !Array.isArray(data.code_constraints))) throw new ApiError('Invalid analysis response', 200, 'INVALID_RESPONSE')
  const warnings = []
  function collect(items) {
    for (const item of Array.isArray(items) ? items : []) {
      if (typeof item?.message === 'string') warnings.push({ code: typeof item.code === 'string' ? item.code : '', message: item.message })
    }
  }
  collect(data.warnings)
  const valueItems = data.value_constraints.map(item => {
    if (typeof item?.text !== 'string') throw new ApiError('Invalid value suggestion', 200, 'INVALID_RESPONSE')
    collect(item.warnings)
    return { text: item.text, status: typeof item.status === 'string' ? item.status : null }
  })
  const codeItems = (data.code_constraints || []).map(item => {
    if (typeof item?.type !== 'string') throw new ApiError('Invalid code suggestion', 200, 'INVALID_RESPONSE')
    collect(item.warnings)
    const rule = ['REQUIRED', 'PROHIBITED'].includes(item.rule) ? item.rule : null
    const enforcement = ['STATIC_CHECK', 'TEACHER_REVIEW_ONLY'].includes(item.enforcement) ? item.enforcement : null
    return {
      text: rule ? `${rule}: ${item.type}` : item.type,
      type: item.type, rule, enforcement,
      evidence: typeof item.evidence === 'string' ? item.evidence : null,
      status: typeof item.status === 'string' ? item.status : null,
    }
  })
  const model = { status: typeof data.model?.status === 'string' ? data.model.status : null, outputComplete: typeof data.model?.output_complete === 'boolean' ? data.model.output_complete : null }
  if (model.outputComplete === false && !warnings.some(item => item.code === 'OUTPUT_TRUNCATED')) warnings.push({ code:'INCOMPLETE_OUTPUT', message:'The backend reported incomplete model output. Review all suggestions.' })
  return {
    valueText: valueItems.map(item => item.text).filter(text => text.trim()).join('\n'),
    codeText: codeItems.map(item => item.text).filter(text => text.trim()).join('\n'),
    valueItems, codeItems, codeSupported: Array.isArray(data.code_constraints), warnings, model,
  }
}

export async function analyzeBasicDetails(values, signal) {
  const token = getToken()
  if (!token) throw new ApiError('Authentication required', 401, 'AUTH_REQUIRED')
  const payload = await apiRequest('/api/ai/drafts', {
    token, signal, method: 'POST', timeoutMs: 130000,
    body: { problem: values.problemStatement, input_format: values.inputFormat, output_format: values.outputFormat },
  })
  const draft=payload.draft
  if(typeof draft?.draft_id!=='string'||!Array.isArray(draft.constraints))throw new ApiError('Invalid draft response',200,'INVALID_RESPONSE')
  const result=normalizeConstraintAnalysis({
    value_constraints:draft.constraints.filter(item=>item.category==='value').map(item=>({text:item.text,status:item.evidence_status,warnings:item.warnings})),
    code_constraints:draft.constraints.filter(item=>item.category==='code').map(item=>({type:item.text,status:item.evidence_status})),
    warnings:draft.ai_warnings||[],
  })
  // Drafts contain canonical code keys, not the analyze endpoint's additive metadata.
  // Display their literal required/prohibited key semantics without inventing evidence/enforcement.
  result.codeItems=result.codeItems.map(item=>({...item,rule:/(?:_prohibited)(?::|$)/.test(item.type)?'PROHIBITED':/_required$/.test(item.type)?'REQUIRED':null}))
  return {...result,draftId:draft.draft_id,draft}
}

export function receiveSuggestions(current, result, key) {
  const populate = !current.editedConstraints && !current.valueConstraints && !current.codeConstraints
  return {
    ...current,
    backendDraftId:result.draftId||current.backendDraftId,
    ...(populate ? { valueConstraints: result.valueText, codeConstraints: result.codeText } : {}),
    approval: { value:false, code:false, finalized:false },
    ai: { ...current.ai, status:'ready', attemptedKey:key, result, error:null, pending:populate ? null : {...result,sourceKey:key}, appliedKey:populate ? key : current.ai.appliedKey },
  }
}

export function localDeadlineAt(rules) {
  return /^\d{4}-\d{2}-\d{2}$/.test(rules.dueDate) && /^\d{2}:\d{2}$/.test(rules.cutoffTime) ? `${rules.dueDate}T${rules.cutoffTime}:00` : null
}

export function validateRules(rules, totalMarks, now = new Date()) {
  const errors = {}
  const iso = localDeadlineAt(rules)
  const parsed = iso ? new Date(iso) : null
  const validDate = parsed && Number.isFinite(parsed.getTime()) &&
    `${parsed.getFullYear()}-${String(parsed.getMonth()+1).padStart(2,'0')}-${String(parsed.getDate()).padStart(2,'0')}` === rules.dueDate &&
    `${String(parsed.getHours()).padStart(2,'0')}:${String(parsed.getMinutes()).padStart(2,'0')}` === rules.cutoffTime
  if (!rules.dueDate) errors.dueDate = 'Choose a due date.'
  if (!rules.cutoffTime) errors.cutoffTime = 'Choose a cutoff time.'
  if (rules.dueDate && rules.cutoffTime && !validDate) errors.dueDate = 'Choose a valid date and 24-hour time.'
  else if (validDate && parsed <= now) errors.dueDate = 'The deadline must be in the future.'
  if (rules.allowLate) {
    const penalty = rules.latePenalty.trim()
    if (!penalty || !/^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(penalty) || !Number.isFinite(Number(penalty)) || Number(penalty) < 0 || Number(penalty) > Number(totalMarks)) errors.latePenalty = `Enter a non-negative amount no greater than ${totalMarks} marks.`
  }
  if (!rules.valueConstraints.trim()) errors.valueConstraints = 'Review or enter value constraints before proceeding.'
  return errors
}

export function plannedLatePolicy(rules) {
  return { allow_late: rules.allowLate, late_penalty_marks: rules.allowLate ? rules.latePenalty : '0' }
}

export function analysisErrorMessage(error) {
  if (error.status === 504 || error.code === 'TIMEOUT') return 'AI analysis timed out. Your policy and constraint edits are retained.'
  if (error.status === 502) return 'The AI service is unavailable or returned an invalid response. Your edits are retained.'
  if (error.code === 'NETWORK') return 'Could not connect to the AI service. Check your connection and retry.'
  return 'AI suggestions could not be loaded. Your edits are retained; you can retry or edit constraints manually.'
}
