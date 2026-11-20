import { ApiError, apiRequest } from './api'
import { getToken } from './auth'

// Retrieval has no verification/provenance field: never infer verification from an output.
export function normalizeTestCases(response) {
  if (!Array.isArray(response?.test_cases) || response.total !== response.test_cases.length) throw new ApiError('Invalid test-case response', 200, 'INVALID_RESPONSE')
  const ids = new Set()
  return response.test_cases.map(item => {
    if (item.test_case_id == null || ids.has(item.test_case_id) || typeof item.input_data !== 'string' || typeof item.expected_output !== 'string' || (item.marks != null && (!Number.isFinite(Number(item.marks)) || Number(item.marks)<0))) throw new ApiError('Invalid test case', 200, 'INVALID_RESPONSE')
    ids.add(item.test_case_id)
    return {key:`server-${item.test_case_id}`,id:item.test_case_id,number:item.test_number,description:item.test_name || '',input:item.input_data,output:item.expected_output,marks:item.marks == null ? null : Number(item.marks),origin:'server',verification:'not-reported',changed:false}
  })
}

export function generationBlockers(rules) {
  const missing=[]
  if (!rules?.backendDraftId || !rules.approval?.value || !rules.approval?.code || !rules.approval?.finalized) missing.push({key:'approval',title:'Teacher review is required',detail:'Review the input format and rubric, then approve test-case generation.'})
  if (!rules?.inputSchema) missing.push({key:'schema',title:'Input format needs confirmation',detail:'Confirm the inferred structure and limits before generating inputs.'})
  missing.push({key:'oracle',title:'Expected outputs need verification',detail:'A reviewed reference solution and a trusted output source are required.'})
  missing.push({key:'assessment',title:'Generation has not completed',detail:'Return to Rubric Setup to finish the review and generation workflow.'})
  return missing
}

export function allocatedMarks(cases) {
  return cases.reduce((sum,item)=>sum+(item.marks ?? 0),0)
}

// A local edit may be checked with this non-saving API, but its output remains unverified.
export async function validateReviewInput(inputSchema, input, signal) {
  if (!inputSchema) throw new ApiError('Approved schema required', 400, 'SCHEMA_REQUIRED')
  const token=getToken()
  if (!token) throw new ApiError('Authentication required',401,'AUTH_REQUIRED')
  const response=await apiRequest('/api/test-inputs/validate',{token,signal,method:'POST',body:{input_schema:inputSchema,inputs:[input]}})
  const result=response.data?.results?.[0]
  if (typeof result?.valid!=='boolean') throw new ApiError('Invalid validation response',200,'INVALID_RESPONSE')
  return result
}
