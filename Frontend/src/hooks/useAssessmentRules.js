import { useCallback, useEffect, useRef, useState } from 'react'
import { clearSession } from '../services/auth'
import { analyzeBasicDetails, emptyRules, receiveSuggestions, rulesSourceKey } from '../services/assessmentRules'

export default function useAssessmentRules() {
  const [rules, setRules] = useState(null)
  const current = useRef(null)
  const task = useRef(null)
  const commit = useCallback(updater => {
    const next = typeof updater === 'function' ? updater(current.current) : updater
    current.current = next
    setRules(next)
  }, [])
  useEffect(() => () => { task.current?.controller.abort(); task.current = null }, [])
  const reset = useCallback(() => {
    task.current?.controller.abort()
    task.current = null
    commit(null)
  }, [commit])
  const update = useCallback((ownerId, patch) => {
    const previous = current.current?.ownerId === ownerId ? current.current : emptyRules(ownerId)
    const editing = 'valueConstraints' in patch || 'codeConstraints' in patch
    commit({ ...previous, ...patch, ...(editing ? { editedConstraints:true, approval:{value:false,code:false,finalized:false} } : {}) })
  }, [commit])
  const ensureAnalysis = useCallback((basic, { force = false } = {}) => {
    const key = rulesSourceKey(basic.values)
    if (task.current?.key === key && task.current.ownerId === basic.ownerId) return task.current.promise
    const previous = current.current?.ownerId === basic.ownerId ? current.current : emptyRules(basic.ownerId)
    if (!force && previous.ai.attemptedKey === key && ['ready','error'].includes(previous.ai.status)) return Promise.resolve()
    task.current?.controller.abort()
    const controller = new AbortController()
    const request = { key, ownerId:basic.ownerId, controller, promise:null }
    task.current = request
    commit({ ...previous, ai:{ ...previous.ai, status:'loading', attemptedKey:key, error:null } })
    request.promise = analyzeBasicDetails(basic.values, controller.signal).then(result => {
      if (!controller.signal.aborted && task.current === request) commit(state => receiveSuggestions(state, result, key))
    }).catch(error => {
      if (controller.signal.aborted || task.current !== request) return
      if ([401,403,422].includes(error.status)) { clearSession(); window.location.replace('#/instructor/login?reason=expired'); return }
      commit(state => ({ ...state, ai:{ ...state.ai, status:'error', error:{status:error.status,code:error.code} } }))
    }).finally(() => { if (task.current === request) task.current = null })
    return request.promise
  }, [commit])
  const applyPending = useCallback((ownerId, sourceKey) => {
    const state = current.current
    if (state?.ownerId !== ownerId || !state.ai.pending || state.ai.pending.sourceKey !== sourceKey) return
    commit({ ...state, valueConstraints:state.ai.pending.valueText, codeConstraints:state.ai.pending.codeText, editedConstraints:false,
      approval:{value:false,code:false,finalized:false}, ai:{...state.ai,pending:null,appliedKey:state.ai.pending.sourceKey} })
  }, [commit])
  const keepEdits = useCallback(ownerId => {
    if (current.current?.ownerId === ownerId) commit(state => ({...state,ai:{...state.ai,pending:null}}))
  }, [commit])
  return { rules, update, ensureAnalysis, applyPending, keepEdits, reset }
}
