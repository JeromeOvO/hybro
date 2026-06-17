import React from "react";

// --- Original auth.ts contents ---

/**
 * Client-side authentication utilities for Clerk integration
 * Safe to import in client components
 */

/**
 * Default token getter for client-side API calls
 * Set by ClerkAuthProvider wrapper
 */
let defaultGetToken: (() => Promise<string | null>) | null = null

/**
 * Set the default token getter (called automatically by ClerkAuthProvider)
 */
export function setDefaultGetToken(getToken: () => Promise<string | null>) {
  defaultGetToken = getToken
}

/**
 * Get authorization headers for client-side API requests
 * Uses provided getToken or falls back to default
 */
export async function getClientAuthHeaders(
  getToken?: () => Promise<string | null>
): Promise<Record<string, string>> {
  const baseHeaders = { 'Content-Type': 'application/json' }
  
  const tokenGetter = getToken || defaultGetToken
  
  if (!tokenGetter) {
    console.warn('No token getter available - API call will be made without authentication')
    return baseHeaders
  }

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


// --- Mock Clerk Exports ---

// Mock User Object
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
  setProfileImage: async (args: any) => console.log("Mock set profile image"),
  reload: async () => console.log("Mock reload user"),
  update: async (args: any) => console.log("Mock update user"),
  updatePassword: async (args: any) => console.log("Mock update password"),
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

const mockGetToken = async () => "mock-jwt-token";

export function useAuth() {
  return {
    isLoaded: true,
    isSignedIn: true,
    userId: mockUser.id,
    sessionId: "sess_local_dev",
    getToken: mockGetToken,
  };
}

export function useClerk() {
  return {
    signOut: (opts?: any) => {
      console.log("Mock sign out");
      window.location.href = "/";
    },
    openUserProfile: () => console.log("Mock open user profile"),
    openWaitlist: () => console.log("Mock open waitlist"),
    openSignIn: () => console.log("Mock open sign in"),
    openSignUp: () => console.log("Mock open sign up"),
  };
}

export function ClerkProvider({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export function SignIn(props: any) {
  return <div>Sign in disabled in Local Developer Mode</div>;
}

export function SignUp(props: any) {
  return <div>Sign up disabled in Local Developer Mode</div>;
}

export function Waitlist(props: any) {
  return <div>Waitlist disabled in Local Developer Mode</div>;
}

export function SignedIn({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export function SignedOut({ children }: { children: React.ReactNode }) {
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

// Ensure the mock user represents the expected properties
export type ClerkUser = typeof mockUser;
