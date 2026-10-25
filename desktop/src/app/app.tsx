import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Component, useState, type ErrorInfo, type ReactNode } from 'react'
import { HashRouter } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { SetupWizard } from '@/features/setup/setup-wizard'
import { ApiError } from '@/lib/api/client'
import { useSetupStatus } from '@/lib/api/hooks'
import { EventsProvider } from '@/lib/events'
import { AppShell } from './app-shell'
import { BackendBanner } from './backend-banner'

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        // Don't retry client errors; do ride out a backend restart.
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 3,
      },
    },
  })
}

function FullScreen({ children }: { children: ReactNode }) {
  return (
    <div className="bg-background flex h-full flex-col">
      <div className="drag h-12 shrink-0" />
      <BackendBanner />
      <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">{children}</div>
    </div>
  )
}

/** Decides between the first-run wizard and the app. */
function Gate() {
  const status = useSetupStatus()

  if (status.isPending) {
    return (
      <FullScreen>
        <Skeleton className="h-4 w-48" />
        <p className="text-muted-foreground text-sm" role="status">Connecting to the backend…</p>
      </FullScreen>
    )
  }
  if (status.isError) {
    return (
      <FullScreen>
        <p className="text-sm font-medium">Cannot reach the backend</p>
        <p className="text-muted-foreground max-w-sm text-sm" role="alert">
          {status.error instanceof ApiError ? status.error.detail : status.error.message}
        </p>
        <div className="flex gap-2">
          <Button onClick={() => void status.refetch()}>Retry</Button>
          {window.reachout ? <Button variant="outline" onClick={() => void window.reachout?.openLogs()}>View logs</Button> : null}
        </div>
      </FullScreen>
    )
  }
  if (!status.data.first_run_complete) return <SetupWizard initial={status.data} />
  return <AppShell />
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null }
  static getDerivedStateFromError(error: Error) { return { error } }
  override componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info.componentStack) }
  override render() {
    if (!this.state.error) return this.props.children
    return (
      <FullScreen>
        <p className="text-sm font-medium">Something went wrong</p>
        <p className="text-muted-foreground max-w-sm text-sm">{this.state.error.message}</p>
        <Button onClick={() => this.setState({ error: null })}>Try again</Button>
      </FullScreen>
    )
  }
}

export function App() {
  const [client] = useState(makeClient)
  return (
    <ErrorBoundary>
      <QueryClientProvider client={client}>
        <TooltipProvider delayDuration={300}>
          <EventsProvider>
            <HashRouter>
              <Gate />
            </HashRouter>
            <Toaster position="bottom-right" />
          </EventsProvider>
        </TooltipProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
