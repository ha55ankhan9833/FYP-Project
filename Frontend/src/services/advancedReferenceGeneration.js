// Advanced, explicitly approved reference-based operations. The ordinary
// assessment creation flow does not import or call this module.
import { workflowRequest, verifyReferenceReceipt, savedReview } from './generationWorkflow'

export async function approveAdvancedReference({assessmentId,sourceCode,teacherConfirmed,signal}){
  if(teacherConfirmed!==true||!sourceCode?.trim())throw new Error('Explicit review of the reference source is required.')
  const root=`/api/assessments/${assessmentId}`
  try{await workflowRequest(`${root}/reference-solution`,{method:'POST',signal,body:{source_code:sourceCode}})}catch(error){if(error.status!==409)throw error}
  const reference=(await workflowRequest(`${root}/reference-solution`,{signal})).reference_solution
  if(reference?.source_code!==sourceCode||!reference.source_hash)throw new Error('The stored reference differs from the reviewed source. Review it before approving.')
  if(reference.approval_status!=='APPROVED'){
    try{await workflowRequest(`${root}/reference-solution/approve`,{method:'POST',signal,body:{source_hash:reference.source_hash}})}catch(error){if(error.status!==409)throw error;const current=(await workflowRequest(`${root}/reference-solution`,{signal})).reference_solution;if(current.source_hash!==reference.source_hash||current.approval_status!=='APPROVED')throw error}
  }
  return {...reference,approval_status:'APPROVED'}
}

export async function saveWithAdvancedReference({assessmentId,reference,allocation,additionalInputs=[],teacherConfirmedSave,signal}){
  if(teacherConfirmedSave!==true||reference?.approval_status!=='APPROVED')throw new Error('Explicit generation/save approval and an approved reference are required.')
  const root=`/api/assessments/${assessmentId}`,readiness=(await workflowRequest(`${root}/generation-readiness`,{signal})).data
  const source=readiness?.checks?.expected_output_source
  if(!readiness?.ready_to_generate||source?.endpoint!=='generate-test-cases'||!['SECURE_SANDBOX','TRUSTED_HOST_ALLOWLIST'].includes(source.source)||readiness.checks.reference_solution?.source_hash!==reference.source_hash)throw new Error('The advanced reference workflow is not ready.')
  const response=await workflowRequest(`${root}/generate-test-cases`,{method:'POST',signal,timeoutMs:130000,body:{additional_inputs:additionalInputs}})
  verifyReferenceReceipt(response.data,assessmentId,reference.source_hash,source.source,allocation)
  const cases=savedReview(await workflowRequest(`${root}/test-cases`,{signal}),[],readiness.checks.required_test_case_count,allocation)
  return {receipt:response.data,cases:cases.map(item=>({...item,verification:'verified'}))}
}
