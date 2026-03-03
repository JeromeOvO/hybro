# Settings Dialog — User Account Management

> **Status: Implemented**

---

## 1. Overview

The Settings Dialog is a modal-based account management system accessible from the user
menu in the sidebar. It provides four sections: Profile, Password, Sessions, and Danger
Zone. All sections leverage Clerk's client-side APIs for data operations.

---

## 2. Architecture

### Dialog Provider Pattern

The settings dialog uses a React context provider (`SettingsDialogProvider`) so any
component in the tree can open the dialog via `useSettingsDialog().openSettings()` without
prop drilling or nested Radix Dialog conflicts.

```
┌────────────────────────────────────────────┐
│  SettingsDialogProvider (context)          │
│    ├── children (app)                      │
│    └── SettingsDialog (sibling)            │
└────────────────────────────────────────────┘
```

The dialog is rendered as a sibling of `children`, not inside it, to avoid z-index and
focus-trap issues with other dialogs on the page.

### Section Architecture

Each section is a self-contained component that receives the Clerk `user` object and
manages its own state, validation, and API calls:

| Section | Component | Clerk APIs Used |
|---|---|---|
| Profile | `ProfileSection` | `user.setProfileImage()`, `user.update()`, `user.reload()` |
| Password | `PasswordSection` | `user.updatePassword()`, `user.passwordEnabled` |
| Sessions | `SessionsSection` | `useSession()`, `user.getSessions()`, `session.revoke()` |
| Danger Zone | `DangerZoneSection` | `useClerk().signOut()`, `user.delete()`, `user.deleteSelfEnabled` |

Shared presentational utilities:
- `SettingsCard` — card layout with title, description, and content slot.
- `FormGroup` — label + input + hint/error layout.
- `LoadingButton` — button with spinner state.
- `PasswordInput` — input with show/hide toggle.

---

## 3. Section Details

### Profile Section

- **Avatar upload**: Click avatar to open file picker. Validates MIME type (JPEG, PNG, GIF, WebP) and size (max 10 MB). Calls `user.setProfileImage({ file })`.
- **Name fields**: First name and last name inputs with dirty-checking against current Clerk values.
- **Email**: Read-only, disabled input showing the primary email address.
- **Username**: Editable input with contextual hint based on whether a username already exists.
- **Save**: `LoadingButton` disabled when no fields are dirty. Calls `user.update({ firstName, lastName, username })`.

### Password Section

- **Current password**: Shown only when `user.passwordEnabled` is true (skipped for users who only use social login).
- **New password**: Inline validation (min 8 characters).
- **Confirm password**: Cross-field validation (must match new password).
- **Sign out other sessions**: Switch toggle (defaults to `true`), maps to `signOutOfOtherSessions` parameter.
- **Submit**: Calls `user.updatePassword({ currentPassword?, newPassword, signOutOfOtherSessions })`.
- Hidden username field for password-manager autofill accessibility.

### Sessions Section

- **Session list**: Fetches all active sessions via `user.getSessions()`, filtered to `status === "active"`.
- **Device info**: Shows device icon (desktop/mobile/globe), browser name and version, IP address, and relative last-active time.
- **Current session badge**: The active session is marked with "Current" and cannot be revoked.
- **Revoke single**: "Revoke" button on each non-current session row, calls `session.revoke()`.
- **Revoke all others**: "Sign out all other sessions" button appears when 2+ other sessions exist.
- **Loading state**: Skeleton UI while sessions load.

### Danger Zone Section

- **Conditional rendering**: Entire section hidden when `user.deleteSelfEnabled` is false (configured in Clerk Dashboard).
- **Delete flow**: Opens an `AlertDialog` confirmation modal.
- **Email confirmation**: User must type their exact email address (case-insensitive) to enable the delete button.
- **Delete action**: Calls `user.delete()` then `signOut({ redirectUrl: "/" })`.
- **Visual treatment**: Card has a destructive border (`border-destructive/30`).

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Dialog wrapper | `src/components/settings/settings-dialog.tsx` | Renders all 4 sections, guards on `isLoaded` |
| Context provider | `src/components/settings/settings-dialog-provider.tsx` | `useSettingsDialog()` hook for external opening |
| Profile section | `src/components/settings/profile-section.tsx` | Avatar upload, name/username editing |
| Password section | `src/components/settings/password-section.tsx` | Change/set password with sign-out toggle |
| Sessions section | `src/components/settings/sessions-section.tsx` | Active session list, revoke |
| Danger zone | `src/components/settings/danger-zone-section.tsx` | Account deletion with email confirmation |
| Card layout | `src/components/settings/settings-card.tsx` | Shared presentational wrapper |
| Form layout | `src/components/settings/form-group.tsx` | Label + input + hint/error |
| Loading button | `src/components/settings/loading-button.tsx` | Button with spinner |
| Password input | `src/components/settings/password-input.tsx` | Show/hide toggle |

---

## 5. Known Limitations

- **No email change**: Users cannot change their primary email through the settings dialog. Email management requires direct Clerk Dashboard configuration.
- **No 2FA management**: Two-factor authentication setup/management is not exposed in the UI.
- **No notification preferences**: There are no notification or communication preferences in the settings.
- **Clerk-dependent**: All operations depend on Clerk's client-side SDK. If Clerk is unavailable, the entire settings system is non-functional.
- **No optimistic updates**: Profile changes wait for the Clerk API to respond before updating the UI. There is no optimistic update for profile fields.
