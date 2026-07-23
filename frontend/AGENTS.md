# Repository Guidelines

## Project Structure & Module Organization

This is a Next.js 16 TypeScript app. Routes live in `src/app`, including groups such as `src/app/(auth)`, `src/app/c`, and `src/app/d`. Components are in `src/components`, grouped by feature plus shared primitives in `src/components/ui`. Hooks live in `src/hooks`, shared logic and API clients in `src/lib`, and Zustand stores in `src/stores`. Static assets are in `public`. Unit tests are colocated as `*.test.ts` when useful, or placed under `tests/unit`; Playwright tests live in `tests/e2e`.

## Build, Test, and Development Commands

Use Node `20.19` from `.nvmrc`; install with `npm install`.

- `npm run dev`: start Next.js with Turbopack.
- `npm run build`: create a production build.
- `npm run start`: serve the production build.
- `npm run lint`: run the Next.js ESLint configuration.
- `npm run test`: run Vitest once.
- `npm run test:watch`: run Vitest in watch mode.
- `npm run test:coverage`: generate coverage in `coverage`.
- `npm run test:e2e`: run Playwright tests; config starts `npm run dev` on port `3000`.
- `npm run test:all`: run unit tests followed by e2e tests.

## Coding Style & Naming Conventions

Use TypeScript strict mode and the `@/*` alias for imports from `src`. Follow the existing style: 2-space indentation, single quotes, no semicolons, and PascalCase React components. Name hooks with `use` prefixes, selectors with `select-*` or descriptive selector names, and tests as `*.test.ts` or `*.test.tsx`. Prefer feature folders unless code is genuinely reused.

## Testing Guidelines

Vitest is split into `stores`, `api`, and `components` projects in `vitest.config.ts`; component and hook tests use `jsdom` plus `tests/setup/vitest.setup.ts`. Put fixtures in `tests/fixtures` and utilities in `tests/utils`. Playwright e2e tests belong in `tests/e2e` and use Chromium against `http://localhost:3000`. Add focused tests for changed logic before broader runs.

## Commit & Pull Request Guidelines

Recent history uses concise imperative subjects and conventional prefixes such as `feat:`, `fix:`, `docs:`, and `chore:`. Keep commits scoped to one change. Pull requests should include a short summary, linked issue or task when available, test results, and screenshots or recordings for visible UI changes.

## Documentation Updates

After code changes, update the relevant architecture documentation before handoff. At minimum, review `docs/System-Architecture.md` whenever a change affects routes, data flow, API integrations, SSE or streaming behavior, state management, module boundaries, authentication, or major UI workflows. Document the current behavior after the change, not the implementation journey. If no architecture document needs an update, mention that in the handoff summary.

Do not add or commit `superpowers` planning docs in this repository. Any `superpowers/` or related folders should be removed before commit.

## Agent Workflow Preferences

For review-only requests, do not edit files. Focus findings on the requested diff, PR, plan, or files, and avoid unrelated pre-existing issues unless they directly affect the requested change.

When reviewing implementation plans, validate referenced paths, functions, types, tests, and architecture assumptions against the current repository. Prioritize execution blockers, design mismatches, and missing test coverage over wording.

For room, SSE, streaming, HITL, processing status, message sending, or agent response rendering changes, treat `client_request_id` as critical correlation data and review `docs/System-Architecture.md` for required updates.

For visible UI changes, verify the affected route in a browser when feasible and check layout, text overflow, and interaction behavior across relevant viewports.

Before editing, check `git status` and preserve user or other-agent changes. Do not commit `docs/superpowers/`, `superpowers/`, or generated review-loop planning artifacts unless explicitly requested.

In handoff summaries, include changed behavior, tests run, documentation updates, and any verification that could not be completed.

## Security & Configuration Tips

Copy required environment keys from `.env.example` into `.env.local`; do not commit local secrets. Keep generated output such as `.next`, `coverage`, and `playwright-report` out of commits.
