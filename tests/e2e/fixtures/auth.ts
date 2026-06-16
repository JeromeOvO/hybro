import { test as base, expect } from '@playwright/test'
import { clerk, setupClerkTestingToken } from '@clerk/testing/playwright'

function clerkReady(): boolean {
  return (
    process.env.CLERK_SETUP_OK === '1' &&
    !!process.env.E2E_CLERK_USER_EMAIL &&
    !!process.env.E2E_CLERK_USER_PASSWORD
  )
}

export const test = base.extend<{ clerkAuth: void; roomPath: string }>({
  roomPath: [
    async ({}, use, testInfo) => {
      const rp = process.env.E2E_TEST_ROOM_PATH
      if (!rp) {
        testInfo.skip(true, 'E2E_TEST_ROOM_PATH not set — skipping room-dependent test')
        return
      }
      await use(rp)
    },
    { auto: false },
  ],

  clerkAuth: [
    async ({ page }, use, testInfo) => {
      if (!clerkReady()) {
        testInfo.skip(true, 'Clerk auth not configured — skipping authenticated test')
        return
      }
      await setupClerkTestingToken({ page })
      await page.goto('/')
      await clerk.loaded({ page })
      await clerk.signIn({
        page,
        signInParams: {
          strategy: 'password',
          identifier: process.env.E2E_CLERK_USER_EMAIL!,
          password: process.env.E2E_CLERK_USER_PASSWORD!,
        },
      })
      await use()
    },
    { auto: false },
  ],
})

export { expect }
