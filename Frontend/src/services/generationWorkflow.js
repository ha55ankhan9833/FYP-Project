import { apiRequest, ApiError } from './api'
import { getToken } from './auth'
import { localDeadlineAt, plannedLatePolicy, validateRules } from './assessmentRules'
import { validateBasicDetails } from './assessmentBasicDetails'
import { normalizeTestCases } from './testCaseReview'
import { normalizeAllocation, markUnits } from './rubricWorkflow'

export const workflowEnvironment=import.meta.env.FYP_WORKFLOW_TARGET==='e2e'?'FYP_E2E_Test (Flask :5000)':'FYP_Database (Flask :5002)'
export const isE2EWorkflow=import.meta.env.FYP_WORKFLOW_TARGET==='e2e'
export function sessionFailure(error){return error.status===401 || (error.status===422 && error.code==='INVALID_TOKEN')}
export async function workflowRequest(path,options={}) {
  const token=getToken()
  if(!token)throw new ApiError('Authentication required',401,'AUTH_REQUIRED')
  return apiRequest(path,{...options,token})
}

export function reviewedLines(text,required=false){
  const lines=text.split(/\r?\n/).filter(line=>line.trim())
  if((required&&!lines.length)||lines.length>50||lines.some(line=>line.length>500)||new Set(lines.map(line=>line.trim())).size!==lines.length)throw new Error('Use one distinct constraint per line, at most 50 lines of 500 characters each.')
  return lines
}
export function makeGenerationSnapshot(basic,rules,flow){
  if(Object.keys(validateBasicDetails(basic.values)).length||Object.keys(validateRules(rules,basic.values.totalMarks)).length)throw new Error('Complete Basic Details and Rules & Policy first.')
  if(!rules.backendDraftId)throw new Error('A real constraint draft is required. Regenerate suggestions to create one before proceeding.')
  if(!basic.course?.code)throw new Error('A verified course is required before preparing an assessment.')
  if(!flow?.sectionIds?.length || basic.ownerId!==rules.ownerId || flow.ownerId!==basic.ownerId)throw new Error('Instructor and selected sections must match this draft.')
  return {ownerId:basic.ownerId,draftId:rules.backendDraftId,values:{...basic.values},course:{...basic.course},sectionIds:[...flow.sectionIds],rules:{dueDate:rules.dueDate,cutoffTime:rules.cutoffTime,allowLate:rules.allowLate,latePenalty:rules.latePenalty,additionalConstraints:rules.additionalConstraints},valueConstraints:reviewedLines(rules.valueConstraints,true),codeConstraints:reviewedLines(rules.codeConstraints)}
}

export function assessmentPayload(snapshot,config){
  const policy=plannedLatePolicy(snapshot.rules)
  return {course_code:snapshot.course.code,assessment_type:snapshot.values.type==='QUIZ'?'Quiz':'Assignment',title:snapshot.values.title,problem_statement:snapshot.values.problemStatement,input_format:snapshot.values.inputFormat,output_format:snapshot.values.outputFormat,total_marks:Number(snapshot.values.totalMarks),deadline_at:localDeadlineAt(snapshot.rules),validation_percentage:config.validationPercentage,allow_late:policy.allow_late,late_penalty_marks:Number(policy.late_penalty_marks),additional_constraints:snapshot.rules.additionalConstraints,draft_id:snapshot.draftId}
}

export function normalizePreview(data,readiness,allocation){
  normalizeAllocation(allocation)
  const required=readiness.checks?.required_test_case_count
  if(data?.execution_mode!=='verified_python_oracle'||data.oracle_id!==readiness.checks?.expected_output_source?.oracle_id||!Array.isArray(data.test_cases)||data.generated_count!==data.test_cases.length||data.saved_count!==0||!Number.isInteger(required)||required<=0)throw new Error('Unexpected oracle preview response; no successful preview is claimed.')
  if(data.test_cases.length!==required)throw new Error(`Partial preview: ${data.test_cases.length} of ${required} cases. Saving is blocked.`)
  if(new Set(data.test_cases.map(item=>item.input_data)).size!==data.test_cases.length)throw new Error('Preview contains duplicate inputs.')
  if(new Set(data.test_cases.map(item=>item.test_number)).size!==data.test_cases.length)throw new Error('Preview contains duplicate case numbers.')
  if(required!==allocation.test_case_count)throw new Error('Case count does not match the approved marks allocation.')
  const marks=Number(allocation.per_case_marks)
  return data.test_cases.map(item=>{
    if(!Number.isInteger(item.test_number)||typeof item.input_data!=='string'||typeof item.expected_output!=='string')throw new Error('Invalid preview case.')
    return {key:`preview-${item.test_number}`,id:null,number:item.test_number,description:item.test_name||'',input:item.input_data,output:item.expected_output,marks,origin:'oracle-preview',verification:'verified',changed:false}
  })
}

export function verifiedProblemSource(readiness){
  const verification=readiness?.verification,source=readiness?.checks?.expected_output_source
  if(verification?.status!=='VERIFIED'||verification.source!=='VERIFIED_PROBLEM_SPEC'||verification.reference_required!==false||verification.generate_endpoint!=='generate-test-cases/verified'||source?.source!=='VERIFIED_PROBLEM_SPEC'||source.endpoint!=='generate-test-cases/verified'||source.reference_required!==false||typeof source.spec_id!=='string'||!source.spec_id||typeof source.oracle_id!=='string'||!source.oracle_id)return null
  return source
}

export function normalizeVerifiedPreview(data,readiness,allocation,assessmentId){
  const source=verifiedProblemSource(readiness)
  if(!source||data?.assessment_id!==assessmentId||data.output_source!=='VERIFIED_PROBLEM_SPEC'||data.reference_required!==false||data.reference_solution_id!==null||data.spec_id!==source.spec_id||data.oracle_id!==source.oracle_id||data.dry_run!==true)throw new Error('The backend did not confirm a reference-free, verified preview for this assessment. No successful generation is claimed.')
  return normalizePreview(data,readiness,allocation)
}

export function validateVerifiedSave(data,readiness,previewReceipt,allocation,assessmentId){
  normalizeAllocation(allocation)
  const source=verifiedProblemSource(readiness)
  if(!source||data?.assessment_id!==assessmentId||data.output_source!=='VERIFIED_PROBLEM_SPEC'||data.reference_required!==false||data.reference_solution_id!==null||data.execution_mode!=='verified_python_oracle'||data.dry_run!==false||data.spec_id!==source.spec_id||data.spec_id!==previewReceipt.spec_id||data.oracle_id!==source.oracle_id||data.oracle_id!==previewReceipt.oracle_id||data.generated_count!==allocation.test_case_count||data.saved_count!==allocation.test_case_count||data.weighting!=='EQUAL'||markUnits(data.marks_per_test)!==markUnits(allocation.per_case_marks)||markUnits(data.test_marks_allocated)!==markUnits(allocation.test_marks)||markUnits(data.rubric_marks_allocated)!==markUnits(allocation.rubric_marks)||markUnits(data.validation_percentage)!==markUnits(allocation.validation_percentage))throw new Error('The saved result did not confirm the reviewed verification source and exact equal marks. Reload the saved cases for review.')
  return data
}
export function savedReview(response,preview,required,allocation){
  const cases=normalizeTestCases(response)
  if(cases.length!==required || new Set(cases.map(item=>item.input)).size!==cases.length)throw new Error(`Saved response must contain ${required} unique cases.`)
  if(allocation){normalizeAllocation(allocation);if(required!==allocation.test_case_count||cases.some(item=>item.marks==null||markUnits(item.marks)!==markUnits(allocation.per_case_marks))||cases.reduce((sum,item)=>sum+markUnits(item.marks),0n)!==markUnits(allocation.test_marks))throw new Error('Saved marks do not match the approved equal allocation.')}
  return cases.map(item=>({...item,verification:preview.some(candidate=>candidate.verification==='verified'&&candidate.input===item.input&&candidate.output===item.output)?'verified':'not-reported'}))
}

export function verifyReferenceReceipt(data,assessmentId,referenceHash,source,allocation){
  normalizeAllocation(allocation)
  const mode=source==='SECURE_SANDBOX'?'sandbox':source==='TRUSTED_HOST_ALLOWLIST'?'trusted_host':null
  if(!mode||data?.execution_mode!==mode||data.assessment_id!==assessmentId||data.source_hash!==referenceHash||data.saved_count!==allocation.test_case_count||markUnits(data.marks_per_test)!==markUnits(allocation.per_case_marks)||markUnits(data.test_marks_allocated)!==markUnits(allocation.test_marks)||(data.weighting!=null&&data.weighting!=='EQUAL'))throw new Error('The backend did not confirm trusted reference execution and exact equal marks. Reload the saved cases for review.')
  return true
}

export function workflowError(error){
  if(error.code==='NETWORK'||error.code==='TIMEOUT')return 'The backend response was interrupted. Keep this page open and retry; known draft and assessment IDs will be reused.'
  return [error.code,error.data?.message||error.message].filter(Boolean).join(': ')
}

