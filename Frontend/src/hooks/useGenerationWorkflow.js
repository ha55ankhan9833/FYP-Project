import { useCallback, useEffect, useRef, useState } from 'react'
import { clearSession } from '../services/auth'
import { workflowRequest, makeGenerationSnapshot, assessmentPayload, normalizeVerifiedPreview, validateVerifiedSave, verifiedProblemSource, savedReview, sessionFailure } from '../services/generationWorkflow'
import { normalizeAllocation, normalizeRubricSuggestion, validateRubric, describeInputSchema, friendlyGenerationError, readinessMessage, verificationRequiredMessage, markUnits } from '../services/rubricWorkflow'

const fresh=()=>({phase:'idle',busy:false,error:null,snapshot:null,assessmentId:null,approval:null,rubricApproval:null,readiness:null,preview:[],cases:[],receipt:null,notes:[],frozenConfig:null,schemaKey:null,schemaProposal:null,inputSummary:null,schemaStatus:'idle',allocation:null,allocationStatus:'idle',allocationError:null,allocationKey:null,rubricStatus:'idle',rubricKey:null,suggestion:null,pendingSuggestion:null,criteriaDirty:false,percentageLocked:false,config:{schemaConfirmed:false,validationPercentage:'50',criteria:[],additionalInputs:[],policyConfirmed:false}})

export default function useGenerationWorkflow(){
  const [state,setState]=useState(fresh)
  const current=useRef(state),active=useRef(false),alive=useRef(true),epoch=useRef(0)
  const schemaTask=useRef(null),allocationTask=useRef(null),rubricTask=useRef(null)
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;schemaTask.current?.abort();allocationTask.current?.abort();rubricTask.current?.abort()}},[])
  const commit=useCallback(patch=>{if(alive.current){current.current={...current.current,...patch};setState(current.current)}},[])
  const handleError=useCallback(error=>{
    if(sessionFailure(error)){clearSession();window.location.replace('#/instructor/login?reason=expired');return}
    commit({...(error.code==='TEST_CASES_ALREADY_SAVED'?{percentageLocked:true}:{}),error:{message:friendlyGenerationError(error),alternatives:error.data?.details?.nearest_valid_percentages||[],canLoadSaved:error.code==='TEST_CASES_ALREADY_SAVED'}})
  },[commit])
  const run=useCallback(async action=>{
    if(active.current)return
    const version=epoch.current;active.current=true;commit({busy:true,error:null})
    const request=async(path,options)=>{
      if(!alive.current||epoch.current!==version)throw new Error('Session closed')
      const result=await workflowRequest(path,options)
      if(!alive.current||epoch.current!==version)throw new Error('Session closed')
      return result
    }
    try{await action(request)}catch(error){if(alive.current&&epoch.current===version)handleError(error)}
    finally{active.current=false;if(alive.current&&epoch.current===version)commit({busy:false})}
  },[commit,handleError])
  const configure=useCallback(patch=>{
    const s=current.current
    if(active.current||['preview','saved'].includes(s.phase))return
    if('validationPercentage' in patch&&s.percentageLocked)return
    if('schemaConfirmed' in patch&&s.approval)return
    if('additionalInputs' in patch&&s.approval)return
    const changed='validationPercentage' in patch&&patch.validationPercentage!==s.config.validationPercentage
    if(changed){allocationTask.current?.abort();rubricTask.current?.abort()}
    commit({config:{...s.config,...patch},error:null,
      ...('criteria' in patch?{criteriaDirty:true,rubricApproval:null}:{}),
      ...(changed?{allocation:null,allocationStatus:'idle',allocationKey:null,allocationError:null,rubricApproval:null,rubricKey:null,pendingSuggestion:null,rubricStatus:'idle'}:{})})
  },[commit])
  const loadAllocation=useCallback((total,pct,{force=false}={})=>{
    const key=JSON.stringify([total,pct])
    if(current.current.allocationKey===key&&!force)return
    allocationTask.current?.abort();const controller=new AbortController();allocationTask.current=controller
    commit({allocationKey:key,allocationStatus:'loading',allocation:null,allocationError:null})
    workflowRequest(`/api/marks-allocation?${new URLSearchParams({total_marks:total,validation_percentage:pct})}`,{signal:controller.signal}).then(response=>{
      if(controller.signal.aborted||!alive.current)return
      const allocation=normalizeAllocation(response.allocation)
      if(markUnits(allocation.total_marks)!==markUnits(total)||markUnits(allocation.validation_percentage)!==markUnits(pct))throw new Error('The marks preview did not match your selection. Try again.')
      commit({allocation,allocationStatus:'ready'})
    }).catch(error=>{if(controller.signal.aborted||!alive.current)return;if(sessionFailure(error)){handleError(error);return}commit({allocationStatus:'error',allocationError:{message:friendlyGenerationError(error),alternatives:error.data?.details?.nearest_valid_percentages||[]}})})
  },[commit,handleError])
  const suggestSchema=useCallback((basic,rules,{force=false}={})=>{
    const key=JSON.stringify([basic.values.problemStatement,basic.values.inputFormat,basic.values.outputFormat,rules.valueConstraints,basic.values.sampleInput])
    if((current.current.schemaKey===key&&!force)||current.current.approval)return
    schemaTask.current?.abort();const controller=new AbortController();schemaTask.current=controller
    commit({schemaKey:key,schemaStatus:'loading',schemaProposal:null,inputSummary:null,config:{...current.current.config,schemaConfirmed:false}})
    workflowRequest('/api/test-inputs/suggestions',{signal:controller.signal,method:'POST',body:{problem:{problem_statement:basic.values.problemStatement,input_format:basic.values.inputFormat,output_format:basic.values.outputFormat,value_constraints:rules.valueConstraints,sample_input:basic.values.sampleInput}}}).then(response=>{
      if(controller.signal.aborted||!alive.current)return
      const schema=response.data?.input_schema,summary=describeInputSchema(schema)
      commit({schemaProposal:schema,inputSummary:summary,schemaStatus:summary?'ready':'unsupported'})
    }).catch(error=>{if(controller.signal.aborted||!alive.current)return;if(sessionFailure(error))handleError(error);else commit({schemaStatus:'error'})})
  },[commit,handleError])
  const open=useCallback((basic,rules,flow)=>{
    try{
      const snapshot=makeGenerationSnapshot(basic,rules,flow),previous=current.current
      if(previous.snapshot&&JSON.stringify(previous.snapshot)!==JSON.stringify(snapshot)&&previous.approval)throw new Error('This assessment already uses the previously reviewed details. Start a new assessment to use different details.')
      const phase=['preview','saved'].includes(previous.phase)?previous.phase:previous.assessmentId?'rubric':'review'
      commit({snapshot,phase,error:null});return phase
    }catch(error){handleError(error)}
  },[commit,handleError])
  const prepare=useCallback(()=>run(async request=>{
    const s=current.current
    if(!s.snapshot)throw new Error('Return to Rules & Policy and choose Generate Test Cases.')
    if(!s.schemaProposal||!s.inputSummary)throw new Error('The input format could not be inferred safely. Clarify Input Format and sample input in Basic Details, then try again.')
    if(!s.config.schemaConfirmed)throw new Error('Review and confirm the input format before continuing.')
    if(!s.config.policyConfirmed)throw new Error('Confirm the storage and late-policy information before continuing.')
    const allocation=normalizeAllocation((await request(`/api/marks-allocation?${new URLSearchParams({total_marks:s.snapshot.values.totalMarks,validation_percentage:s.config.validationPercentage})}`)).allocation)
    const config=s.frozenConfig||{schema:s.schemaProposal,validationPercentage:Number(allocation.validation_percentage),additionalInputs:[...s.config.additionalInputs]}
    if(config.additionalInputs.length){const result=await request('/api/test-inputs/validate',{method:'POST',body:{input_schema:config.schema,inputs:config.additionalInputs}});if(result.data?.results?.length!==config.additionalInputs.length||result.data.results.some(item=>!item.valid))throw new Error('One or more custom inputs do not match the reviewed input format.')}
    const draftPath=`/api/ai/drafts/${encodeURIComponent(s.snapshot.draftId)}`
    if(!current.current.approval){
      const body={value_constraints:s.snapshot.valueConstraints,code_constraints:s.snapshot.codeConstraints,input_schema:config.schema}
      let response
      try{response=await request(`${draftPath}/approve-reviewed`,{method:'POST',body})}
      catch(error){if(error.code!=='DRAFT_CHANGED')throw error;await request(draftPath);response=await request(`${draftPath}/approve-reviewed`,{method:'POST',body})}
      if(!response.draft?.finalized||!response.draft.approval?.value||!response.draft.approval?.code)throw new Error('The backend did not confirm constraint approval. Please retry.')
      commit({approval:response,frozenConfig:config,notes:response.notes||[]})
    }
    if(!current.current.assessmentId){
      let id
      try{id=(await request('/api/assessments',{method:'POST',body:assessmentPayload(s.snapshot,config)})).assessment_id}
      catch(error){if(error.code!=='DRAFT_ALREADY_USED'||!error.data?.assessment_id)throw error;id=error.data.assessment_id;const recovered=(await request(`/api/assessments/${id}`)).assessment;const expected=assessmentPayload(s.snapshot,config);const fields=['course_code','assessment_type','title','problem_statement','input_format','output_format','total_marks','deadline_at','allow_late','late_penalty_marks','additional_constraints'];if(!recovered||fields.some(key=>String(recovered[key]??'')!==String(expected[key]??'')))throw new Error('The existing assessment has different details. No duplicate assessment was created.',{cause:error})}
      if(!Number.isInteger(id)||id<=0)throw new Error('The backend did not return an assessment. Please retry.')
      commit({assessmentId:id})
    }
    commit({phase:'rubric',allocation,allocationStatus:'ready'})
  }),[commit,run])
  const suggestRubric=useCallback(({force=false}={})=>{
    const s=current.current,key=JSON.stringify([s.assessmentId,s.config.validationPercentage])
    if(!s.assessmentId||s.allocationStatus!=='ready'||(s.rubricKey===key&&!force)||s.phase==='saved')return
    rubricTask.current?.abort();const controller=new AbortController();rubricTask.current=controller
    commit({rubricStatus:'loading',rubricKey:key,rubricError:null})
    workflowRequest(`/api/assessments/${s.assessmentId}/rubric/suggest`,{signal:controller.signal,method:'POST',timeoutMs:180000,body:{validation_percentage:Number(s.config.validationPercentage)}}).then(response=>{
      if(controller.signal.aborted||!alive.current)return
      const suggestion=response.suggestion
      const sameLines=(text,expected)=>{const lines=typeof text==='string'?text.split(/\r?\n/).map(line=>line.trim()).filter(Boolean):[];return lines.length===expected.length&&expected.every(line=>lines.includes(line.trim()))}
      if(suggestion?.assessment_id!==s.assessmentId||suggestion.based_on?.problem_statement!==true||!sameLines(suggestion.based_on.value_constraints,s.snapshot.valueConstraints)||!sameLines(suggestion.based_on.code_constraints,s.snapshot.codeConstraints))throw new Error('The AI suggestion could not be matched to this problem and its approved constraints. Your edits are retained; retry the request.')
      const result=normalizeRubricSuggestion(suggestion,s.allocation),latest=current.current,replace=!latest.criteriaDirty&&!latest.config.criteria.length
      commit({rubricStatus:result.status,suggestion:result,pendingSuggestion:replace||result.status==='UNAVAILABLE'?null:result,...(replace?{config:{...latest.config,criteria:result.criteria},rubricApproval:null}:{})})
    }).catch(error=>{if(controller.signal.aborted||!alive.current)return;if(sessionFailure(error))handleError(error);else commit({rubricStatus:'error',rubricError:friendlyGenerationError(error)})})
  },[commit,handleError])
  const applySuggestion=useCallback(()=>{const s=current.current;if(s.pendingSuggestion&&!s.busy)commit({config:{...s.config,criteria:s.pendingSuggestion.criteria},criteriaDirty:false,pendingSuggestion:null,rubricApproval:null})},[commit])
  const keepEdits=useCallback(()=>commit({pendingSuggestion:null}),[commit])
  const approveAndGenerate=useCallback(()=>run(async request=>{
    const s=current.current
    if(!s.assessmentId)throw new Error('Confirm the input format first so the AI can use this assessment.')
    const allocation=normalizeAllocation((await request(`/api/marks-allocation?${new URLSearchParams({total_marks:s.snapshot.values.totalMarks,validation_percentage:s.config.validationPercentage})}`)).allocation)
    const criteria=validateRubric(s.config.criteria,allocation)
    rubricTask.current?.abort()
    const root=`/api/assessments/${s.assessmentId}`
    const approved=(await request(`${root}/rubric/approve`,{method:'POST',body:{validation_percentage:Number(allocation.validation_percentage),criteria}})).rubric
    if(approved?.approved!==true)throw new Error('The backend did not confirm rubric approval.')
    const approvedAllocation=normalizeAllocation(approved.allocation)
    if(markUnits(approvedAllocation.validation_percentage)!==markUnits(allocation.validation_percentage))throw new Error('The approved weightage differs from your selection. Generation is blocked.')
    commit({rubricApproval:approved,allocation:approvedAllocation})
    const readiness=(await request(`${root}/generation-readiness`)).data
    commit({readiness})
    if(readiness?.checks?.test_case_count>0){const cases=savedReview(await request(`${root}/test-cases`),s.preview,readiness.checks.required_test_case_count,approvedAllocation);commit({cases,phase:'saved',percentageLocked:true});return}
    if(readiness?.verification?.status==='VERIFICATION_REQUIRED')throw new Error(verificationRequiredMessage(readiness))
    if(!readiness?.ready_to_generate)throw new Error(readinessMessage(readiness?.missing_for_generation)||'This assessment is not ready to generate safely.')
    if(!verifiedProblemSource(readiness))throw new Error(readiness?.verification?.message||'This problem requires the separate advanced verification workflow. No test cases were generated in this reference-free flow.')
    const response=await request(`${root}/generate-test-cases/verified`,{method:'POST',timeoutMs:130000,body:{additional_inputs:s.frozenConfig.additionalInputs,dry_run:true}})
    commit({preview:normalizeVerifiedPreview(response.data,readiness,approvedAllocation,s.assessmentId),phase:'preview',previewReceipt:response.data})
  }),[commit,run])
  const addCustomPreview=useCallback(async form=>{
    let success=false
    await run(async request=>{
      const s=current.current
      if(s.phase!=='preview'||!s.rubricApproval?.approved||!s.frozenConfig)throw new Error('Custom cases can only refresh an unsaved, approved preview. Saved cases cannot be changed.')
      const inputs=s.frozenConfig.additionalInputs
      const key=text=>text.replace(/\n+$/,'')
      if(inputs.length>=10)throw new Error('All 10 custom-input slots are occupied. No case has been removed.')
      if([...inputs,...s.preview.map(item=>item.input)].some(text=>key(text)===key(form.input)))throw new Error('This input is already present. No duplicate case was added.')
      const additional=[...inputs,form.input],root=`/api/assessments/${s.assessmentId}`
      const readiness=(await request(`${root}/generation-readiness`)).data
      if(readiness?.checks?.test_case_count>0)throw new Error('Test cases have already been saved. Adding or replacing saved cases is not supported.')
      if(!readiness?.ready_to_generate||!verifiedProblemSource(readiness))throw new Error(verificationRequiredMessage(readiness))
      const response=await request(`${root}/generate-test-cases/verified`,{method:'POST',timeoutMs:130000,body:{additional_inputs:additional,dry_run:true}})
      const preview=normalizeVerifiedPreview(response.data,readiness,s.allocation,s.assessmentId)
      const added=preview.find(item=>key(item.input)===key(form.input))
      if(!added)throw new Error('The backend did not include the requested custom input. Your previous preview is retained.')
      const notes=[...(s.customNotes||[]),{input:form.input,description:form.description,proposedOutput:form.output}]
      commit({preview:preview.map(item=>{const note=notes.find(n=>key(n.input)===key(item.input));return note?{...item,description:note.description,teacherOutput:note.proposedOutput,custom:true}:item}),previewReceipt:response.data,readiness,customNotes:notes,frozenConfig:{...s.frozenConfig,additionalInputs:additional},config:{...s.config,additionalInputs:additional}})
      success=true
    })
    return success
  },[commit,run])
  const save=useCallback(()=>run(async request=>{
    const s=current.current
    if(s.phase==='saved')return
    if(s.phase!=='preview'||!s.preview.length||!s.rubricApproval?.approved)throw new Error('Review and approve a genuine preview before saving.')
    const root=`/api/assessments/${s.assessmentId}`
    let readiness=(await request(`${root}/generation-readiness`)).data
    if(!readiness?.checks?.test_case_count){
      if(readiness?.verification?.status==='VERIFICATION_REQUIRED')throw new Error(verificationRequiredMessage(readiness))
      const source=verifiedProblemSource(readiness)
      if(!readiness?.ready_to_generate||!source||source.spec_id!==s.previewReceipt.spec_id||source.oracle_id!==s.previewReceipt.oracle_id)throw new Error('The verified problem definition or readiness changed since preview. Regenerate and review before saving.')
      try{const response=await request(`${root}/generate-test-cases/verified`,{method:'POST',timeoutMs:130000,body:{additional_inputs:s.frozenConfig.additionalInputs,dry_run:false}});commit({receipt:validateVerifiedSave(response.data,readiness,s.previewReceipt,s.allocation,s.assessmentId)})}
      catch(error){if(error.status!==409||error.code!=='TEST_CASES_ALREADY_EXIST')throw error;readiness=(await request(`${root}/generation-readiness`)).data;if(!readiness?.checks?.test_case_count)throw error}
    }
    const cases=savedReview(await request(`${root}/test-cases`),s.preview,readiness.checks.required_test_case_count,s.allocation)
    readiness=(await request(`${root}/generation-readiness`)).data
    commit({cases,readiness,phase:'saved',percentageLocked:true})
  }),[commit,run])
  const reset=useCallback(()=>{epoch.current++;schemaTask.current?.abort();allocationTask.current?.abort();rubricTask.current?.abort();const next=fresh();current.current=next;setState(next)},[])
  const loadSavedCases=useCallback(()=>run(async request=>{
    const s=current.current
    if(!s.assessmentId)throw new Error('There is no saved assessment to load.')
    const root=`/api/assessments/${s.assessmentId}`,readiness=(await request(`${root}/generation-readiness`)).data
    if(!readiness?.checks?.test_case_count||readiness.checks.test_case_count!==readiness.checks.required_test_case_count)throw new Error('A complete saved test-case set is not available yet.')
    const allocation=normalizeAllocation(readiness.checks.marks_allocation),rubric=(await request(`${root}/rubric`)).rubric
    const cases=savedReview(await request(`${root}/test-cases`),s.preview,readiness.checks.required_test_case_count,allocation)
    commit({cases,readiness,allocation,allocationStatus:'ready',phase:'saved',percentageLocked:true,config:{...s.config,validationPercentage:allocation.validation_percentage,criteria:(rubric?.criteria||[]).map(item=>({criterion_name:item.criterion_name,description:item.description||'',max_marks:String(item.max_marks)}))}})
  }),[commit,run])
  const revisePreview=useCallback(()=>run(async request=>{
    const s=current.current
    if(s.phase!=='preview')return
    const root=`/api/assessments/${s.assessmentId}`,readiness=(await request(`${root}/generation-readiness`)).data
    if(readiness?.checks?.test_case_count>0){const cases=savedReview(await request(`${root}/test-cases`),s.preview,readiness.checks.required_test_case_count,s.allocation);commit({cases,readiness,phase:'saved',percentageLocked:true});return}
    commit({phase:'rubric',preview:[],previewReceipt:null,rubricApproval:null,criteriaDirty:true,error:null})
  }),[commit,run])
  const invalidateReview=useCallback(()=>{if(!current.current.approval)commit({snapshot:null,phase:'idle',error:null})},[commit])
  return {state,configure,loadAllocation,suggestSchema,open,prepare,suggestRubric,applySuggestion,keepEdits,approveAndGenerate,addCustomPreview,save,loadSavedCases,revisePreview,reset,invalidateReview}
}

