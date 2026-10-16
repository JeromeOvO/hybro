// How an agent wants one question shown: a short header, whether it may be
// skipped, whether a choice question also takes a typed "Other" answer, and
// labels or descriptions for its choices. Choice values stay in `choices`.

export interface HitlChoiceOption {
  value: string
  label?: string
  description?: string
}

export interface HitlQuestionPresentation {
  header?: string
  required: boolean
  allowOther: boolean
  choiceOptions?: HitlChoiceOption[]
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function choiceOption(value: unknown): HitlChoiceOption | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const optionValue = text(raw.value)
  if (!optionValue) return null
  const label = text(raw.label)
  const description = text(raw.description)
  return {
    value: optionValue,
    ...(label ? { label } : {}),
    ...(description ? { description } : {}),
  }
}

/** Parse the presentation fields of a `hitl_request` frame or pending row. */
export function parseHitlPresentation(payload: object): HitlQuestionPresentation {
  const raw = payload as Record<string, unknown>
  const header = text(raw.header)
  const options = Array.isArray(raw.choice_options)
    ? raw.choice_options.map(choiceOption).filter(option => option !== null)
    : []
  return {
    ...(header ? { header } : {}),
    required: raw.required !== false,
    allowOther: raw.allow_other === true,
    ...(options.length ? { choiceOptions: options } : {}),
  }
}

export function presentationEqual(
  left: HitlQuestionPresentation | undefined,
  right: HitlQuestionPresentation | undefined,
): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}
