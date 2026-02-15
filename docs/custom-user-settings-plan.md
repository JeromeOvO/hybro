# Custom User Settings Page — Research & Implementation Plan

## Background

Clerk's `<UserButton>` component renders a popover that portals to `<body>`. On mobile, the sidebar renders inside a Radix `Sheet` (Dialog), which traps focus and blocks pointer events on anything outside the dialog content. This creates an irreconcilable conflict: the Clerk popover is outside the Sheet's DOM tree, so its buttons (Sign Out, Manage Account) cannot be clicked.

### What we've done so far

1. Removed Clerk's `<UserButton>` entirely
2. Replaced it with a custom `DropdownMenu` using `Avatar` + Clerk's `user.imageUrl`
3. On mobile, the dropdown renders **without a portal** (stays inside the Sheet's content tree)
4. On desktop, the dropdown uses the standard portaled `DropdownMenuContent`
5. Menu items: "Manage account" (`openUserProfile()`), theme toggle, "Sign out" (`signOut()`)

### Remaining issues

- `openUserProfile()` opens Clerk's hosted modal, which causes **ghost click pass-through** on mobile (dropdown closes, touch event lands on sidebar nav items underneath)
- Fixing the ghost click with `setTimeout` or `e.stopPropagation()` is a workaround, not a proper fix

### The proper solution

Build a custom settings page at `/c/settings` (and optionally `/d/settings`) that replaces `openUserProfile()` with a standard `router.push()` navigation. This eliminates all modal/portal/ghost-click issues.

---

## Clerk API Reference (Client-Side)

All methods below are available on the `User` object from `useUser()`. No backend API calls needed.

### Profile

| Method | Signature | Notes |
|---|---|---|
| `user.update()` | `update({ firstName?, lastName?, username? })` | Updates profile fields |
| `user.setProfileImage()` | `setProfileImage({ file: Blob \| File })` | Upload/replace avatar |
| `user.imageUrl` | `string` | Current avatar URL |
| `user.hasImage` | `boolean` | `false` if using Clerk's default avatar |

### Password

| Method | Signature | Notes |
|---|---|---|
| `user.updatePassword()` | `updatePassword({ currentPassword?, newPassword, signOutOfOtherSessions? })` | Change password. `currentPassword` optional if user has no password yet |
| `user.passwordEnabled` | `boolean` | `false` for OAuth-only users (no password set) |

### Sessions

| Method | Signature | Notes |
|---|---|---|
| `user.getSessions()` | `getSessions(): Promise<SessionWithActivities[]>` | Returns all active sessions with device/browser/IP info. Cached after first call |
| `session.revoke()` | On each `SessionWithActivities` object | Signs out a specific session |

### Account Deletion

| Method | Signature | Notes |
|---|---|---|
| `user.delete()` | `delete(): Promise<void>` | Permanently deletes the account |
| `user.deleteSelfEnabled` | `boolean` | Whether self-deletion is allowed (configured in Clerk Dashboard) |

### Not in scope (defer)

- **Email management** — `user.createEmailAddress()`, verification flows. Complex, rarely used.
- **Phone management** — same as email. Complex verification.
- **OAuth connections** — `user.createExternalAccount()`. Requires redirect flows.
- **2FA / TOTP** — `user.createTOTP()`, `user.verifyTOTP()`. Advanced feature.

---

## Existing Components Available

Already in `src/components/ui/`:

- `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`
- `Button`
- `Input`
- `Label`
- `Avatar`, `AvatarImage`, `AvatarFallback`
- `Dialog`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription`
- `Separator`
- `Skeleton`

**Missing:** `Tabs` component (not installed). Options: install via shadcn, or use a vertical stacked sections layout instead.

---

## Implementation Plan

### 1. Settings page shell

**File:** `src/app/c/settings/page.tsx`

```
"use client"

- Import section components
- useUser() for loading state + redirect if not signed in
- Render page-container / page-content layout (matches existing pages)
- Stack sections vertically: Profile → Password → Sessions → Danger Zone
- Each section is a Card with a heading
```

**Approx:** ~60 lines

### 2. Profile section

**File:** `src/components/settings/profile-section.tsx`

```
- Avatar display (current user.imageUrl)
- Hidden file input for avatar upload
- Click avatar → trigger file input → user.setProfileImage({ file })
- Show upload progress/success feedback
- First name + Last name input fields (pre-filled from user object)
- Save button → user.update({ firstName, lastName })
- Loading/success/error states with toast or inline feedback
```

**Clerk APIs used:**
- `user.update({ firstName, lastName })`
- `user.setProfileImage({ file })`
- `user.imageUrl`, `user.hasImage`
- `user.firstName`, `user.lastName`
- `user.primaryEmailAddress?.emailAddress` (display only, not editable)

**Approx:** ~100 lines

### 3. Password section

**File:** `src/components/settings/password-section.tsx`

```
- If user.passwordEnabled:
    - Current password field
    - New password field
    - Confirm new password field
    - Checkbox: "Sign out of other sessions"
    - Submit → user.updatePassword({ currentPassword, newPassword, signOutOfOtherSessions })
- If !user.passwordEnabled (OAuth-only user):
    - Message: "You signed in with a social provider. Set a password to also sign in with email."
    - New password + confirm fields only (no current password needed)
    - Submit → user.updatePassword({ newPassword })
- Client-side validation: passwords match, min 8 chars
- Error handling: wrong current password, password too weak
```

**Clerk APIs used:**
- `user.updatePassword({ currentPassword?, newPassword, signOutOfOtherSessions? })`
- `user.passwordEnabled`

**Approx:** ~120 lines

### 4. Active sessions section

**File:** `src/components/settings/sessions-section.tsx`

```
- On mount: user.getSessions() → list of SessionWithActivities
- Each session card shows:
    - Browser + OS (from session.latestActivity)
    - IP address
    - Last active timestamp (relative, e.g. "2 hours ago")
    - "Current" badge if session.id matches current session
- "Revoke" button on non-current sessions → session.revoke()
- "Sign out all other sessions" button → revoke all except current
- Loading skeleton while sessions load
- useSession() from Clerk to identify current session ID
```

**Clerk APIs used:**
- `user.getSessions()` → `SessionWithActivities[]`
- `session.revoke()` on each session
- `useSession()` to identify current session

**Approx:** ~130 lines

### 5. Danger zone section

**File:** `src/components/settings/danger-zone-section.tsx`

```
- Only render if user.deleteSelfEnabled
- Red-bordered Card with warning text
- "Delete account" button (destructive variant)
- Confirmation Dialog:
    - Explain consequences (permanent, irreversible)
    - Require user to type their email to confirm
    - Confirm button → user.delete()
    - On success → redirect to home page
```

**Clerk APIs used:**
- `user.delete()`
- `user.deleteSelfEnabled`

**Approx:** ~80 lines

### 6. Navigation change in dropdown

**File:** `src/components/nav-user.tsx`

```diff
- import { useClerk } from "@clerk/nextjs"
+ import { useRouter } from "next/navigation"

  // In NavUser component:
+ const router = useRouter()

  // In dropdown menu item:
- <DropdownMenuItem onClick={() => openUserProfile()}>
+ <DropdownMenuItem onClick={() => router.push("/c/settings")}>
```

This eliminates `openUserProfile()` entirely. Standard page navigation — no modals, no portals, no ghost clicks.

### 7. (Optional) Developer portal settings

If needed for `/d/settings`, create `src/app/d/settings/page.tsx` that imports the same section components. The sections are reusable since they all use `useUser()` which works in both consumer and developer layouts.

---

## File Summary

| File | Type | Purpose | Approx lines |
|---|---|---|---|
| `src/app/c/settings/page.tsx` | New | Page shell, section layout | ~60 |
| `src/components/settings/profile-section.tsx` | New | Name + avatar editing | ~100 |
| `src/components/settings/password-section.tsx` | New | Password change form | ~120 |
| `src/components/settings/sessions-section.tsx` | New | Active sessions list + revoke | ~130 |
| `src/components/settings/danger-zone-section.tsx` | New | Account deletion | ~80 |
| `src/components/nav-user.tsx` | Modified | Replace `openUserProfile()` with `router.push()` | ~3 lines |

**Total: ~490 lines of new code across 5 new files + 1 small edit.**

---

## Dependencies

- **No new packages needed.** All functionality uses:
  - Clerk's `useUser()` hook (already in use)
  - Clerk's `User` object methods (client-side)
  - Clerk's `useSession()` hook (already available via `@clerk/nextjs`)
  - Existing shadcn/ui components

---

## What stays unchanged

- `<SignIn>` and `<SignUp>` pages — work fine as standalone Clerk components
- `clerkMiddleware` in `middleware.ts` — no changes
- `ClerkProvider` / `ClerkAuthProvider` — no changes
- All `useAuth()` / `getToken()` usage for API calls — no changes
- `useUser()` usage in other components — no changes

---

## Implementation order (recommended)

1. **Profile section** — most visible, simplest, immediate value
2. **Nav dropdown change** — swap `openUserProfile()` → `router.push()` (fixes mobile ghost click)
3. **Password section** — straightforward form
4. **Sessions section** — moderate complexity (async data loading)
5. **Danger zone** — lowest priority, smallest scope

---

## Known Issues & Risks

### Issue 1: Hardcoded `/c/settings` breaks developer portal — HIGH

`NavUser` is a **shared component** used in both `ConsumerSidebar` and `DeveloperSidebar`. The plan's step 6 hardcodes `router.push("/c/settings")`, but when the user is on the developer portal (`developer.hybro.ai`), they need `/d/settings`.

The subdomain middleware rewrites URLs based on hostname:
- `hybro.ai/settings` → `/c/settings`
- `developer.hybro.ai/settings` → `/d/settings`

**Resolution options:**
- Use `usePathname()` to detect if the current path starts with `/d/` or `/c/` and route accordingly (e.g. `router.push(pathname.startsWith("/d") ? "/d/settings" : "/c/settings")`).
- Or use `window.location.href = "/settings"` which triggers the middleware rewrite to the correct prefix. This causes a full page reload but is the simplest approach.

### Issue 2: `<Toaster>` is not mounted — MEDIUM

The `sonner` Toaster component exists at `src/components/ui/sonner.tsx` but is **never rendered** in any layout (`layout.tsx`, `c/layout.tsx`, `d/layout.tsx`). The plan suggests using toast notifications for success/error feedback on profile save, password change, etc. Without `<Toaster />` mounted, toast calls will silently do nothing.

**Resolution:** Mount `<Toaster />` in the root `src/app/layout.tsx` (inside `ThemeProvider`), or use inline status messages (e.g. green/red text below the save button) instead of toasts.

### Issue 3: No auth protection on the settings route — MEDIUM

The `clerkMiddleware` in `middleware.ts` does **not** call `auth.protect()` on any route — it only does subdomain rewrites. All routes are publicly accessible. If an unauthenticated user navigates directly to `/c/settings`, `useUser()` returns `null` and they'll see a broken or blank page.

**Resolution:** The settings page component **must** include a client-side guard:
```
if (!isLoaded) return <LoadingSkeleton />
if (!user) { redirect("/sign-in"); return null }
```
This is mentioned in the plan ("redirect if not signed in") but it is a hard requirement, not optional.

### Issue 4: Password section may not apply — LOW

The password section depends on whether **password authentication** is enabled in the Clerk Dashboard (User & Authentication → Email/Password). If the app only uses OAuth (Google, GitHub, etc.) and doesn't allow password sign-in:
- `user.passwordEnabled` will always be `false`
- `user.updatePassword()` may throw errors
- The section would be useless

**Resolution:** Check the Clerk Dashboard configuration before building. If password auth is disabled, skip the password section entirely or make it fully conditional (only show if password strategy is enabled).

### Issue 5: Session list caching after revoke — LOW

Clerk's `user.getSessions()` uses an internal cache. The first call makes a network request; subsequent calls return cached data. After revoking a session with `session.revoke()`, calling `getSessions()` again will return **stale data** from cache.

**Resolution:** After `session.revoke()`, call `await user.reload()` then `await user.getSessions()` to force a fresh fetch. The sessions section component should implement this refresh pattern.

### Issue 6: Avatar upload needs file validation — LOW

`user.setProfileImage({ file })` sends the image directly to Clerk's servers. Without client-side validation, users could select an oversized or invalid file and receive a cryptic Clerk API error.

**Resolution:** Before calling `setProfileImage()`, validate:
- **File size:** max 10MB (Clerk's limit)
- **File type:** accept only `image/jpeg`, `image/png`, `image/gif`, `image/webp`
- **Preview:** optionally show a preview before uploading

### Issue 7: Mobile sidebar doesn't close on navigation — LOW

When the user taps "Manage account" in the dropdown on mobile and navigates via `router.push()`, the sidebar `Sheet` will stay open. Other sidebar links may close the Sheet automatically via `onOpenChange`, but a `router.push()` triggered from inside the dropdown won't trigger the Sheet's close handler.

**Resolution:** After `router.push()`, also call `setOpenMobile(false)` from `useSidebar()` to close the sidebar Sheet on mobile. This means `NavUser` needs access to `useSidebar()` (or the navigation handler needs to be aware of the mobile state).

### Issue 8: `deleteSelfEnabled` depends on Clerk Dashboard config — LOW

Account deletion only works if "Allow users to delete their own account" is enabled in the Clerk Dashboard. If disabled, `user.deleteSelfEnabled` is `false` and `user.delete()` will fail. The plan already handles this conditionally (`Only render if user.deleteSelfEnabled`), but this dashboard setting should be verified before building the section.

### Risk summary

| Severity | Issue | Impact |
|---|---|---|
| **HIGH** | Hardcoded `/c/settings` breaks developer portal | Wrong route for developer portal users |
| **MEDIUM** | Toaster not mounted | No feedback on save/error actions |
| **MEDIUM** | No auth guard on settings page | Broken page for unauthenticated visitors |
| **LOW** | Password section may not apply | Useless section if password auth disabled |
| **LOW** | Session list caching after revoke | Stale data shown after revoking a session |
| **LOW** | No avatar file validation | Cryptic errors on oversized/invalid uploads |
| **LOW** | Sidebar doesn't close on mobile nav | Sheet stays open after navigating to settings |
| **LOW** | `deleteSelfEnabled` config dependency | Section may never render if not configured |
