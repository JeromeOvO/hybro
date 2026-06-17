const clerkSetup = async () => {};

export default async function globalSetup() {
  try {
    await clerkSetup()
    process.env.CLERK_SETUP_OK = '1'
  } catch {
    delete process.env.CLERK_SETUP_OK
    console.warn(
      'clerkSetup() failed — authenticated E2E tests will be skipped. ' +
        'Set CLERK_SECRET_KEY to enable.',
    )
  }
}
