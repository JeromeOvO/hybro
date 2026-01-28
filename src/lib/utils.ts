import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function getApiUrl(endpoint: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
  const apiPrefix = process.env.NEXT_PUBLIC_API_PREFIX || '/api/v1';
  return `${baseUrl}${apiPrefix}/${endpoint}`;
}

// Waitlist configuration
export function isWaitlistEnabled(): boolean {
  return process.env.NEXT_PUBLIC_ENABLE_WAITLIST === 'true';
}

export function getInspectionTimeoutMs(): number {
  return parseInt(process.env.NEXT_PUBLIC_INSPECTION_TIMEOUT_MS || '300000');
}