import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ApiError } from '@/lib/api/client'
import { useCampaignList } from '@/lib/api/hooks-compose'
import { CampaignBar } from './campaign-bar'
import { DraftsPanel } from './drafts-panel'
import { ProfilePanel } from './profile-panel'
import { TemplatesPanel } from './templates-panel'

const TABS = ['drafts', 'templates', 'profile'] as const
type TabId = (typeof TABS)[number]

export function ComposePage() {
  const [params, setParams] = useSearchParams()
  const campaigns = useCampaignList()
  const list = campaigns.data ?? []
  const fromUrl = Number(params.get('campaign'))
  const selected = list.find((c) => c.id === fromUrl) ?? list[0] ?? null
  const tab: TabId = TABS.find((t) => t === params.get('tab')) ?? 'drafts'

  const set = (key: string, value: string) => setParams((p) => { const n = new URLSearchParams(p); n.set(key, value); return n }, { replace: true })
  useEffect(() => { document.title = 'Compose' }, [])

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col gap-4 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Compose</h1>
        <p className="text-muted-foreground text-sm">Write templates, let the local model add a one-sentence opener per lead, then read and approve every draft. Nothing sends without your approval.</p>
      </header>
      {campaigns.isPending ? <Skeleton className="h-12 w-96" role="status" aria-label="Loading campaigns" /> : campaigns.isError ? (
        <Alert variant="destructive" role="alert"><AlertDescription className="flex items-center gap-3">
          {campaigns.error instanceof ApiError ? campaigns.error.detail : 'Could not load campaigns.'}
          <Button size="xs" variant="outline" onClick={() => void campaigns.refetch()}>Retry</Button>
        </AlertDescription></Alert>
      ) : (
        <CampaignBar campaigns={list} selectedId={selected?.id ?? null} onSelect={(id) => set('campaign', String(id))} />
      )}
      <Tabs value={tab} onValueChange={(v) => set('tab', v)} className="flex min-h-0 flex-1 flex-col gap-4">
        <TabsList>
          <TabsTrigger value="drafts">Drafts</TabsTrigger>
          <TabsTrigger value="templates">Templates</TabsTrigger>
          <TabsTrigger value="profile">Profile</TabsTrigger>
        </TabsList>
        <TabsContent value="drafts" className="flex min-h-0 flex-1 flex-col">
          {selected ? <DraftsPanel key={selected.id} campaign={selected} /> : !campaigns.isPending && !campaigns.isError ? (
            <div className="text-muted-foreground rounded-lg border border-dashed p-10 text-center text-sm" data-testid="no-campaign">Create a campaign to start writing drafts. Choose New campaign above and pick a template.</div>
          ) : null}
        </TabsContent>
        <TabsContent value="templates"><TemplatesPanel /></TabsContent>
        <TabsContent value="profile"><ProfilePanel /></TabsContent>
      </Tabs>
    </div>
  )
}
