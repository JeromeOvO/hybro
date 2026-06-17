## 2026-06-15 — Modified wrong repository path during sed replacement

- **Mistake**: I ran a `sed` replace command and edited files in `/Users/kflu/Projects/hybro-frontend` instead of the newly consolidated `/Users/kflu/Projects/hybro/frontend`. I wrote mock `auth.ts` and modified `proxy.ts` in the wrong directory.
- **Trigger**: The user pointed out that `hybro-frontend` is the original production server and I shouldn't uninstall packages from it. This triggered a realization that I was modifying the wrong directory path.
- **Root cause**: False assumption from pattern matching. The IDE showed `Active Document: /Users/kflu/Projects/hybro-frontend/src/...` in the system prompt metadata. I absent-mindedly used `hybro-frontend` in my `run_command` Cwd parameter because I was matching the active document path, forgetting that we are now working in the consolidated `hybro` monorepo.
- **Recurrence of**: new
- **Rule**: Before running any destructive or recursive edit commands (like `sed` or `npm uninstall`), always explicitly verify that the `Cwd` matches the intended consolidated monorepo directory (`hybro`), rather than blindly copying the active document path from the metadata.
