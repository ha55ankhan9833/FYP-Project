export function markUnits(value){
  const text=String(value)
  if(!/^\d+(?:\.\d{1,2})?$/.test(text))throw new Error('Use marks with at most two decimal places.')
  const [whole,fraction='']=text.split('.')
  return BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'))
}

export function normalizeAllocation(allocation){
  if(!allocation||allocation.test_case_count!==10)throw new Error('The backend did not provide the required equal allocation for 10 test cases.')
  const total=markUnits(allocation.total_marks),test=markUnits(allocation.test_marks),rubric=markUnits(allocation.rubric_marks),per=markUnits(allocation.per_case_marks),pct=markUnits(allocation.validation_percentage)
  if(total<=0n||pct>10000n||test+rubric!==total||per*10n!==test||total*pct!==test*10000n)throw new Error('The backend returned an inconsistent marks allocation. Generation is blocked.')
  if(allocation.rubric_required!==(rubric>0n))throw new Error('The backend rubric requirement does not match its marks allocation.')
  return allocation
}

export function validateRubric(criteria,allocation){
  normalizeAllocation(allocation)
  if(!Array.isArray(criteria)||criteria.length>20)throw new Error('Use at most 20 rubric criteria.')
  const expected=markUnits(allocation.rubric_marks)
  if(expected===0n){if(criteria.length)throw new Error('No rubric criteria are needed when test cases receive 100%.');return []}
  if(!criteria.length)throw new Error('Add or review rubric criteria before approving.')
  let total=0n;const names=new Set()
  const payload=criteria.map(item=>{
    const name=item.criterion_name?.trim(),description=item.description??'',marks=markUnits(item.max_marks)
    if(!name||name.length>150||names.has(name.toLowerCase()))throw new Error('Criterion titles must be distinct and between 1 and 150 characters.')
    if(typeof description!=='string'||description.length>1000)throw new Error('Keep each description within 1,000 characters.')
    if(marks<=0n||marks>99999n)throw new Error('Each criterion must have between 0.01 and 999.99 marks.')
    names.add(name.toLowerCase());total+=marks
    return {criterion_name:item.criterion_name,max_marks:String(item.max_marks),description:description||null}
  })
  if(total!==expected)throw new Error(`Criterion marks must total exactly ${allocation.rubric_marks}.`)
  return payload
}

export function normalizeRubricSuggestion(suggestion,allocation){
  if(!suggestion||!['GENERATED','UNAVAILABLE','NOT_REQUIRED'].includes(suggestion.status)||suggestion.approved!==false||!Array.isArray(suggestion.criteria))throw new Error('The AI returned an invalid rubric suggestion. Your edits are retained.')
  const received=normalizeAllocation(suggestion.allocation)
  if(markUnits(received.total_marks)!==markUnits(allocation.total_marks)||markUnits(received.validation_percentage)!==markUnits(allocation.validation_percentage))throw new Error('The suggestion belongs to a different marks split. Request it again.')
  const criteria=suggestion.criteria.map(item=>({criterion_name:item.criterion_name,description:item.description||'',max_marks:item.suggested_marks}))
  if(suggestion.status==='GENERATED')validateRubric(criteria,received)
  else if(criteria.length)throw new Error('Unexpected criteria in an unavailable rubric response.')
  if(suggestion.status==='NOT_REQUIRED'&&markUnits(received.rubric_marks)!==0n)throw new Error('The rubric allocation still needs teacher-approved criteria.')
  return {...suggestion,criteria,warnings:(suggestion.warnings||[]).filter(item=>typeof item.code==='string'&&typeof item.message==='string')}
}

// Descriptions come only from the inferred schema. Unknown structures are blocked,
// never hidden behind a confirmation for an incomplete description.
export function describeInputSchema(schema){
  if(!schema||schema.version!==1||!schema.variables||Array.isArray(schema.variables)||!Array.isArray(schema.items)||!schema.items.length||Object.keys(schema).some(key=>!['version','variables','items','relations','legacy'].includes(key)))return null
  const lines=[]
  for(const [name,range] of Object.entries(schema.variables)){
    if(!range||!Number.isFinite(range.min)||!Number.isFinite(range.max)||Object.keys(range).some(key=>!['min','max'].includes(key)))return null
    lines.push(`${name}: an integer from ${range.min} to ${range.max}.`)
  }
  const keys={ints:['type','vars'],array:['type','name','length','min','max','distinct','order'],string:['type','name','length','min_length','max_length','charset','chars'],grid:['type','name','rows','cols','cell','charset','chars','min','max'],edges:['type','count','vertices','index_base','directed','self_loops','multi_edges','connected','tree','weight'],repeat:['type','count','items']}
  function items(records,depth=0){
    if(depth>1||!Array.isArray(records))return false
    for(const item of records){
      if(!item||!keys[item.type]||Object.keys(item).some(key=>!keys[item.type].includes(key)))return false
      if(item.type==='ints'){if(!Array.isArray(item.vars)||item.vars.some(name=>!schema.variables[name]))return false;lines.push(`Read ${item.vars.join(', ')} on one line.`)}
      if(item.type==='array'){
        if(!item.name||item.length==null||!Number.isFinite(item.min)||!Number.isFinite(item.max)||![undefined,null,'asc','desc'].includes(item.order))return false
        lines.push(`${item.name}: ${item.length} integers on one line, each from ${item.min} to ${item.max}; ${item.distinct?'all values distinct':'repeated values allowed'}; ${item.order==='asc'?'ascending order':item.order==='desc'?'descending order':'any order'}.`)
      }
      if(item.type==='string')lines.push(`${item.name}: a string, ${item.length!=null?`length ${item.length}`:`length ${item.min_length} to ${item.max_length}`}; allowed characters: ${item.chars??item.charset}.`)
      if(item.type==='grid')lines.push(`${item.name}: ${item.rows} rows by ${item.cols} columns of ${item.cell==='int'?`integers from ${item.min} to ${item.max}`:`characters from ${item.chars??item.charset}`}.`)
      if(item.type==='edges'){
        if(['directed','self_loops','multi_edges','connected'].some(key=>typeof item[key]!=='boolean'))return false
        lines.push(`${item.count} edges over ${item.vertices} vertices, indexed from ${item.index_base??1}; ${item.directed?'directed':'undirected'}; self-loops ${item.self_loops?'allowed':'not allowed'}; repeated edges ${item.multi_edges?'allowed':'not allowed'}; ${item.connected?'must be connected':'not required to be connected'}; ${item.tree?'must be a tree':'not required to be a tree'}; ${item.weight?`weights ${item.weight.min} to ${item.weight.max}`:'unweighted'}.`)
      }
      if(item.type==='repeat'){lines.push(`Repeat the following input group ${item.count} times:`);if(!items(item.items,depth+1))return false}
    }
    return true
  }
  if(!items(schema.items))return null
  for(const relation of schema.relations||[]){if(!relation.left||!relation.op||!relation.right||Object.keys(relation).some(key=>!['left','op','right'].includes(key)))return null;lines.push(`Required relationship: ${relation.left} ${relation.op} ${relation.right}.`)}
  return lines
}

export function friendlyGenerationError(error){
  const code=error.code
  if(code==='VERIFICATION_REQUIRED')return verificationRequiredMessage(error.data)
  if(code==='MARKS_ALLOCATION_INVALID')return 'This weightage cannot give all 10 cases exactly equal marks to two decimal places. Choose one of the supported percentages below.'
  if(['INPUT_SCHEMA_REQUIRED','INVALID_INPUT_SCHEMA','UNSUPPORTED_INPUT_FORMAT','SCHEMA_CONSTRAINT_MISMATCH'].includes(code))return 'The input format could not be confirmed safely. Check the Input Format, sample input and constraint ranges in the earlier steps, then try again.'
  if(code==='TEST_CASES_ALREADY_SAVED')return 'Test cases are already saved. Their weightage cannot be changed.'
  if(code==='RUBRIC_LOCKED')return 'Students have submitted this assessment, so its rubric is locked.'
  if(code==='RUBRIC_TOTAL_MISMATCH')return 'The criterion marks must match the rubric allocation exactly.'
  if(code==='INVALID_CRITERION')return 'Check criterion titles, descriptions and marks. Titles must be distinct and marks must use at most two decimal places.'
  if(code==='EXPECTED_OUTPUT_UNVERIFIABLE')return 'Expected outputs could not be verified safely. No successful generation is claimed. A trusted verifier for the reviewed reference is required.'
  if(['NETWORK','TIMEOUT'].includes(code))return 'The backend did not respond in time. Your work is retained; retry when it is available.'
  if(error.status>=500)return 'The backend service is unavailable. Your work is retained; please retry.'
  return error.data?.message||error.message||'The operation could not be completed. Your work is retained.'
}

export function verificationRequiredMessage(data){
  const message=data?.verification?.message||data?.message||'Expected outputs for this problem cannot be verified automatically yet. No test cases were generated.'
  const details=data?.details||data?.missing_for_generation?.find(item=>item.spec_match)?.spec_match
  const labels={problem_statement:'problem wording',input_format:'input format',output_format:'output format',value_constraints:'value constraints',input_schema:'input structure'}
  const differs=Array.isArray(details?.differs_in)?details.differs_in.map(key=>labels[key]).filter(Boolean):[]
  if(differs.length)return `${message} Items to review: ${differs.join(', ')}.`
  const reasons={REGISTRY_UNAVAILABLE:'The reviewed verification definitions are currently unavailable. Please retry later or ask your project administrator.',NO_REVIEWED_SPECS:'No reviewed verification definition is currently available.',AMBIGUOUS_SPEC:'More than one reviewed definition matches. An administrator must resolve this before generation.'}
  return reasons[details?.reason]?`${message} ${reasons[details.reason]}`:message
}

export function readinessMessage(missing){
  const messages={CONSTRAINTS_NOT_APPROVED:'The reviewed constraints still need approval.',INPUT_SCHEMA_REQUIRED:'The input format needs clarification before test cases can be generated.',REFERENCE_SOLUTION_MISSING:'A correct reference solution is required to verify expected outputs.',REFERENCE_SOLUTION_NOT_APPROVED:'Review and approve the reference solution before generating.',REFERENCE_SOLUTION_INVALID:'The stored reference solution could not be verified. Please ask your project administrator to check it.',EXPECTED_OUTPUT_SOURCE_UNAVAILABLE:'This problem has no trusted output verifier for the reviewed solution. Generation is blocked until a supported verifier is available.',RUBRIC_NOT_APPROVED:'Review and approve the rubric before generating.',MARKS_ALLOCATION_INVALID:'Choose a weightage that gives all 10 cases exactly equal marks.',RUBRIC_INCOMPLETE:'The rubric marks do not yet match the required allocation.'}
  return (missing||[]).map(item=>messages[item.code]||'This assessment is not ready for safe test-case generation.').join(' ')
}
