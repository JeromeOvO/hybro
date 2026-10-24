import { describe, expect, it } from 'vitest'
import { ApiError } from '../api-client'
import { registrationConflictMessage } from './agent'

describe('registrationConflictMessage', () => {
  it('reads the reason from a typed 409 conflict', () => {
    const error = new ApiError(409, 'HTTP error', {
      detail: { code: 'owned_by_another_provider', message: 'Another provider has already registered an agent at this URL' },
    })
    expect(registrationConflictMessage(error)).toBe('Another provider has already registered an agent at this URL')
  })

  it('ignores other statuses and malformed bodies', () => {
    expect(registrationConflictMessage(new ApiError(400, 'bad', { detail: { message: 'x' } }))).toBeUndefined()
    expect(registrationConflictMessage(new ApiError(409, 'conflict', { detail: 'plain' }))).toBeUndefined()
    expect(registrationConflictMessage(new Error('x'))).toBeUndefined()
  })
})
