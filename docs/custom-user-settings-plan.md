# Custom User Settings — Implementation Status

## Background

Clerk's `<UserButton>` component renders a popover that portals to `<body>`. On mobile, the sidebar renders inside a Radix `Sheet` (Dialog), which traps focus and blocks pointer events on anything outside the dialog content. This creates an irreconcilable conflict: the Clerk popover is outside the Sheet's DOM tree, so its buttons (Sign Out, Manage Account) cannot be clicked.

### Solution implemented

Instead of a standalone page at `/c/settings`, settings are rendered in a **Dialog** (`SettingsDialog`) managed by a context provider (`SettingsDialogProvider`). This avoids all route-related issues (hardcoded `/c/` vs `/d/` paths, auth guards on a new route, mobile sidebar not closing on navigation) while still eliminating the Clerk popover entirely.

---

## Architecture

### Entry point: NavUser dropdown

**File:** `src/components/nav-user.tsx`

- Replaced Clerk's `<UserButton>` with a custom `DropdownMenu` using `Avatar` + Clerk's `user.imageUrl`
- On mobile, the dropdown renders **without a portal** (stays inside the Sheet content tree) via `UserDropdownContent` helper using `DropdownMenuPrimitive.Content` directly
- On desktop, the dropdown uses the standard portaled `DropdownMenuContent`
- `DropdownMenuTrigger` wraps the **entire user bar** (avatar + name + email) so the full row is clickable — not just the avatar
- Menu items: "Settings" (opens settings dialog), theme submenu (Light / Dark / System via radio group), "Sign out"
- Theme submenu uses `DropdownMenuSub` → `DropdownMenuRadioGroup` with `onSelect={(e) => e.preventDefault()}` to keep the menu open while switching themes

### Settings dialog

**File:** `src/components/settings/settings-dialog.tsx`

- Renders as a `Dialog` (not a page route), max-width `sm:max-w-2xl`, max-height `85vh` with scrollable content
- Shows a loading spinner while `useUser()` loads
- Stacks four sections vertically with `Separator` between header and content:
  1. `ProfileSection`
  2. `PasswordSection`
  3. `SessionsSection`
  4. `DangerZoneSection`

### Settings dialog provider

**File:** `src/components/settings/settings-dialog-provider.tsx`

- Context provider that renders `<SettingsDialog />` as a **sibling** of its children — outside the Sidebar / Sheet tree — so that nested Radix Dialog conflicts are avoided on mobile
- Exposes `openSettings()` via `useSettingsDialog()` hook
- Mounted in both `src/app/c/layout.tsx` and `src/app/d/layout.tsx`, so it works in both consumer and developer portals

---

## Sections

### 1. Profile section

**File:** `src/components/settings/profile-section.tsx` (~150 lines)

Features:
- Avatar display with hover overlay (camera icon) for upload
- Hidden file input triggered on avatar click
- Client-side file validation: type (`image/jpeg`, `image/png`, `image/gif`, `image/webp`) and size (max 10 MB)
- First name + Last name input fields (pre-filled from user object)
- Email field (read-only, displayed with disabled input + hint text)
- Dirty checking — Save button disabled until fields change
- Toast notifications on success/error via `sonner`

Clerk APIs used:
- `user.update({ firstName, lastName })`
- `user.setProfileImage({ file })`
- `user.reload()` after both operations to refresh cached data
- `user.imageUrl`, `user.hasImage`, `user.firstName`, `user.lastName`
- `user.primaryEmailAddress?.emailAddress` (display only)

### 2. Password section

**File:** `src/components/settings/password-section.tsx` (~140 lines)

Features:
- Conditional form based on `user.passwordEnabled`:
  - If `true`: current password + new password + confirm fields
  - If `false` (OAuth-only user): message explaining social sign-in, new password + confirm only
- Client-side validation: passwords match, minimum 8 characters, inline error messages
- "Sign out of other sessions" toggle (`Switch` component), default `true`
- Uses `PasswordInput` component with show/hide toggle

Clerk APIs used:
- `user.updatePassword({ currentPassword?, newPassword, signOutOfOtherSessions })`
- `user.passwordEnabled`

### 3. Active sessions section

**File:** `src/components/settings/sessions-section.tsx` (~216 lines)

Features:
- Loads sessions on mount via `user.getSessions()`
- Filters to active sessions only (`session.status === "active"`)
- Each session row shows: device icon (mobile/desktop/globe), browser name + version, IP address, relative last-active time
- "Current" badge on the active session (detected via `useSession()`)
- "Revoke" button on non-current sessions → `session.revoke()`
- "Sign out all other sessions" button when >1 other sessions exist → `Promise.allSettled` for parallel revocation with partial success/failure toast
- Skeleton loading state (2 placeholder rows)
- After revoke: calls `user.reload()` then `user.getSessions()` to bust Clerk's internal cache (skips `reload()` on initial mount to avoid unnecessary API call)

Clerk APIs used:
- `user.getSessions()` → `SessionWithActivitiesResource[]`
- `session.revoke()` on individual sessions
- `user.reload()` to bust session cache after revocation
- `useSession()` to identify current session

### 4. Danger zone section

**File:** `src/components/settings/danger-zone-section.tsx` (~128 lines)

Features:
- Only renders if `user.deleteSelfEnabled` is `true`
- Red-bordered Card with warning icon
- "Delete account" button (destructive variant) opens an `AlertDialog`
- Confirmation requires user to type their email address exactly
- On confirm: `user.delete()` → `signOut({ redirectUrl: "/" })`
- Cancel resets the confirmation input

Clerk APIs used:
- `user.delete()`
- `user.deleteSelfEnabled`
- `signOut({ redirectUrl: "/" })` via `useClerk()`

---

## Shared utility components

| File | Purpose |
|---|---|
| `src/components/settings/form-group.tsx` | `<Label>` + children + optional hint text wrapper |
| `src/components/settings/loading-button.tsx` | `<Button>` with loading spinner and disabled state |
| `src/components/settings/password-input.tsx` | `<Input type="password">` with show/hide eye toggle |
| `src/lib/clerk-error.ts` | `getClerkErrorMessage()` — extracts user-friendly error messages from Clerk API errors |

---

## File summary

| File | Type | Purpose | Approx lines |
|---|---|---|---|
| `src/components/settings/settings-dialog.tsx` | New | Dialog shell, section layout | ~58 |
| `src/components/settings/settings-dialog-provider.tsx` | New | Context provider, renders dialog outside sidebar tree | ~36 |
| `src/components/settings/profile-section.tsx` | New | Name + avatar editing | ~150 |
| `src/components/settings/password-section.tsx` | New | Password change form | ~140 |
| `src/components/settings/sessions-section.tsx` | New | Active sessions list + revoke | ~216 |
| `src/components/settings/danger-zone-section.tsx` | New | Account deletion | ~128 |
| `src/components/settings/form-group.tsx` | New | Reusable label + input wrapper | ~19 |
| `src/components/settings/loading-button.tsx` | New | Button with loading state | ~21 |
| `src/components/settings/password-input.tsx` | New | Password input with visibility toggle | ~53 |
| `src/lib/clerk-error.ts` | New | Clerk error message helper | small |
| `src/components/nav-user.tsx` | Modified | Custom dropdown replacing Clerk's UserButton; full-row trigger | ~237 |
| `src/app/c/layout.tsx` | Modified | Wraps children with `SettingsDialogProvider` | ~2 lines |
| `src/app/d/layout.tsx` | Modified | Wraps children with `SettingsDialogProvider` | ~2 lines |

---

## Dependencies

- **No new packages were needed.** All functionality uses:
  - Clerk's `useUser()`, `useSession()`, `useClerk()` hooks (already in use)
  - Clerk's `User` object methods (client-side)
  - Existing shadcn/ui components: `Card`, `Button`, `Input`, `Label`, `Avatar`, `Dialog`, `AlertDialog`, `Separator`, `Skeleton`, `Switch`, `DropdownMenu`
  - `sonner` for toast notifications (already installed)

---

## What stays unchanged

- `<SignIn>` and `<SignUp>` pages — work fine as standalone Clerk components
- `clerkMiddleware` in `middleware.ts` — no changes
- `ClerkProvider` / `ClerkAuthProvider` — no changes
- All `useAuth()` / `getToken()` usage for API calls — no changes
- `useUser()` usage in other components — no changes

---

## Design decisions & resolved issues

### Decision: Dialog instead of standalone page route

The original plan proposed `/c/settings` (and optionally `/d/settings`) as a page route. The implementation uses a Dialog instead. This resolved several issues from the original plan at once:

| Original issue | How dialog approach resolves it |
|---|---|
| **Hardcoded `/c/settings` breaks developer portal** (HIGH) | Dialog is rendered by the provider in both `/c/` and `/d/` layouts — no route needed |
| **No auth protection on settings route** (MEDIUM) | Dialog only opens when user is logged in (triggered from NavUser which only renders for authenticated users); `useUser()` inside the dialog handles the loading state |
| **Mobile sidebar doesn't close on navigation** (LOW) | `setOpenMobile(false)` is called when opening settings; dialog opens independently of sidebar state |

### Toaster — resolved

The original plan noted `<Toaster>` was not mounted. It is now mounted in `src/app/layout.tsx` with `richColors` and `closeButton` props. All settings sections use `toast.success()` / `toast.error()` from `sonner`.

### Session list caching — resolved

After `session.revoke()`, the code calls `user.reload()` before `user.getSessions()` to bust Clerk's internal cache. The initial mount skips `user.reload()` (via `isInitialLoad` ref) to avoid an unnecessary API call.

### Avatar upload validation — resolved

Client-side validation before `setProfileImage()`:
- File type: `image/jpeg`, `image/png`, `image/gif`, `image/webp`
- File size: max 10 MB (Clerk's limit)
- Toast error on validation failure

### Password section conditional rendering — resolved

The section dynamically adapts based on `user.passwordEnabled`:
- If `true`: shows current + new + confirm password fields
- If `false`: shows explanatory message + new + confirm only (no current password required)

### `deleteSelfEnabled` — handled

Danger zone section conditionally renders: `if (!user.deleteSelfEnabled) return null`

---

## Remaining / deferred items

- **Email management** — `user.createEmailAddress()`, verification flows. Complex, rarely used.
- **Phone management** — same as email. Complex verification.
- **OAuth connections** — `user.createExternalAccount()`. Requires redirect flows.
- **2FA / TOTP** — `user.createTOTP()`, `user.verifyTOTP()`. Advanced feature.
