"use client"

import { PropsWithChildren, useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"

let activeQueryClient: QueryClient | null = null

export function getActiveQueryClient(): QueryClient | null {
  return activeQueryClient
}

export function QueryProvider({ children }: PropsWithChildren) {
  // Create client per provider to avoid sharing mutable instance across renders
  const [client] = useState(
    () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1000 * 30, // 30s
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
          },
        },
      })
      activeQueryClient = queryClient
      return queryClient
    }
  )

  return (
    <QueryClientProvider client={client}>
      {children}
      <ReactQueryDevtools initialIsOpen={false} />
    </QueryClientProvider>
  )
}

