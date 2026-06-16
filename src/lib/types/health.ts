// Health check related type definitions

export interface HealthCheckResponse {
  status: 'healthy' | 'unhealthy' | 'degraded'
  timestamp: string
  version: string
  uptime: number
  services: ServiceHealth[]
}

export interface ServiceHealth {
  name: string
  status: 'up' | 'down' | 'warning'
  response_time: number
  last_check: string
  details?: string
} 