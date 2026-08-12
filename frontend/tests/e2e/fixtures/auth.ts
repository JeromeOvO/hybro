import { test as base, expect } from '@playwright/test'

export const test = base.extend<{ roomPath: string }>({
  roomPath: [
    async ({}, use, testInfo) => {
      const roomPath = process.env.E2E_TEST_ROOM_PATH
      if (!roomPath) {
        testInfo.skip(true, 'E2E_TEST_ROOM_PATH not set — skipping room-dependent test')
        return
      }
      await use(roomPath)
    },
    { auto: false },
  ],
})

export { expect }
