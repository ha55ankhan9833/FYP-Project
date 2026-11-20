export function emptyBasicDetails() {
  return { type: 'ASSIGNMENT', title: '', totalMarks: '', problemStatement: '', inputFormat: '', outputFormat: '', sampleInput: '', sampleOutput: '' }
}

export function validateBasicDetails(values) {
  const errors = {}
  if (!['ASSIGNMENT', 'QUIZ'].includes(values.type)) errors.type = 'Choose Assignment or Quiz.'
  if (!values.title.trim()) errors.title = 'Enter a title.'
  else if (values.title.length > 60) errors.title = 'Use no more than 60 characters.'
  const marks = values.totalMarks.trim()
  if (!marks) errors.totalMarks = 'Enter total marks.'
  else if (!/^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(marks) || !Number.isFinite(Number(marks)) || Number(marks) <= 0) errors.totalMarks = 'Enter a finite number greater than zero.'
  for (const [key, label] of [['problemStatement', 'problem statement'], ['inputFormat', 'input format'], ['outputFormat', 'output format']]) {
    if (!values[key].trim()) errors[key] = `Enter the ${label}.`
    else if (values[key].length > 10000) errors[key] = 'Use no more than 10,000 characters for the constraint analysis workflow.'
  }
  return errors
}

export function basicDetailsForOwner(draft, ownerId) {
  return ownerId != null && draft?.ownerId === ownerId ? draft.values : emptyBasicDetails()
}
