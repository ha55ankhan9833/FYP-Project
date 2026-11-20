// Token spans keep the original separators, line endings and input order intact.
export function inputFields(schema, input) {
  if (schema?.version !== 1 || !Array.isArray(schema.items)) return []
  const tokens = [...input.matchAll(/\S+/g)]
  const fields = []
  let cursor = 0
  const values = {}
  for (const item of schema.items) {
    if (item.type === 'ints' && Array.isArray(item.vars)) {
      for (const name of item.vars) {
        const token = tokens[cursor++]
        if (!token || !/^-?\d+$/.test(token[0])) return []
        values[name] = Number(token[0])
        fields.push({label: name, start: token.index, end: token.index + token[0].length, value: token[0], multiline: false})
      }
    } else if (item.type === 'array') {
      const length = typeof item.length === 'number' ? item.length : values[item.length]
      if (!Number.isSafeInteger(length) || length < 1 || cursor + length > tokens.length) return []
      const first = tokens[cursor], last = tokens[cursor + length - 1]
      fields.push({label: `${item.name || 'Array'}[i] — array values`, start: first.index, end: last.index + last[0].length, value: input.slice(first.index, last.index + last[0].length), multiline: true})
      cursor += length
    } else return []
  }
  return cursor === tokens.length ? fields : []
}

export function replaceInputField(input, field, value) {
  return input.slice(0, field.start) + value + input.slice(field.end)
}

export function editedPreviewCase(original, form) {
  const contentChanged = form.input !== original.input || form.output !== original.output
  return {...original, description: form.description, input: form.input, output: form.output,
    changed: original.changed || contentChanged || form.description !== original.description,
    verification: contentChanged ? 'unverified' : original.verification}
}

export function outputFormatError(output, format, schema) {
  if (!output.trim()) return 'Enter the expected output, preserving the required line breaks.'
  // Enforce only an explicit two-line max/min instruction for integer input.
  // Other teacher-authored formats remain exact text and require manual review.
  const instruction = (format || '').toLowerCase()
  const integerInput = schema?.items?.length > 0 && schema.items.every(item => ['ints', 'array'].includes(item.type))
  if (integerInput && /max(?:imum)?\b/.test(instruction) && /min(?:imum)?\b/.test(instruction) && /(?:line\s*1|first\s+line)/.test(instruction) && /(?:line\s*2|second\s+line)/.test(instruction)) {
    if (!/^\s*[+-]?\d+[ \t]*\r?\n[ \t]*[+-]?\d+\s*$/.test(output)) return 'Use two integer lines: maximum first, minimum second, as required by the output format.'
  }
  return ''
}
