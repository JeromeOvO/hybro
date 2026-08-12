import type { ReactNode } from "react";

/** Local identity adapter used by the self-hosted frontend. */
const localGetToken = async () => "mock-jwt-token";

/**
 * Get authorization headers for client-side API requests
 * Uses provided getToken or falls back to default
 */
export async function getClientAuthHeaders(
  getToken?: () => Promise<string | null>
): Promise<Record<string, string>> {
  const baseHeaders = { 'Content-Type': 'application/json' }
  
  const tokenGetter = getToken || localGetToken

  try {
    const token = await tokenGetter()
    
    if (token) {
      return {
        ...baseHeaders,
        'Authorization': `Bearer ${token}`,
      }
    } else {
      console.warn('Token getter returned null - user may not be authenticated')
    }
  } catch (error) {
    console.warn('Failed to get auth token:', error)
  }
  
  return baseHeaders
}


// Local auth adapter exports
const mockUser = {
  id: "user_local_developer",
  firstName: "Developer",
  lastName: "Local",
  fullName: "Developer Local",
  username: "developer_local",
  primaryEmailAddress: { emailAddress: "developer@hybro.local" },
  hasImage: false,
  imageUrl: "",
  passwordEnabled: false,
  deleteSelfEnabled: false,
  delete: async () => console.log("Mock delete user"),
  setProfileImage: async (_args: unknown) => console.log("Mock set profile image"),
  reload: async () => console.log("Mock reload user"),
  update: async (_args: unknown) => console.log("Mock update user"),
  updatePassword: async (_args: unknown) => console.log("Mock update password"),
  getSessions: async () => [{
    id: "sess_local_dev",
    status: "active",
    latestActivity: { browserName: "MockBrowser", deviceType: "Desktop", ipAddress: "127.0.0.1" },
    lastActiveAt: new Date(),
    revoke: async () => console.log("Mock revoke session"),
  }],
};

export function useUser() {
  return {
    isLoaded: true,
    isSignedIn: true,
    user: mockUser,
  };
}

export function useAuth() {
  return {
    isLoaded: true,
    isSignedIn: true,
    userId: mockUser.id,
    sessionId: "sess_local_dev",
    getToken: localGetToken,
  };
}

export function useAuthClient() {
  return {
    signOut: (_opts?: unknown) => {
      console.log("Mock sign out");
      window.location.href = "/";
    },
    openUserProfile: () => console.log("Mock open user profile"),
    openSignIn: () => console.log("Mock open sign in"),
    openSignUp: () => console.log("Mock open sign up"),
  };
}

export function SignIn(_props: Record<string, unknown>) {
  return <div>Sign in disabled in Local Developer Mode</div>;
}

export function SignUp(_props: Record<string, unknown>) {
  return <div>Sign up disabled in Local Developer Mode</div>;
}

export function SignedIn({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function SignedOut({ children }: { children: ReactNode }) {
  return null;
}

export function UserButton() {
  return (
    <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center text-primary font-semibold text-xs border border-primary/30">
      DEV
    </div>
  );
}

export function useSession() {
  return {
    isLoaded: true,
    isSignedIn: true,
    session: {
      id: "sess_local_dev",
      lastActiveAt: new Date(),
    }
  };
}

export type AuthUser = typeof mockUser;
