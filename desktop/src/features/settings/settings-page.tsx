import { useSearchParams } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ApiError } from '@/lib/api/client'
import { useSettings } from '@/lib/api/hooks'
import { AboutTab } from './tabs/about-tab'
import { AccountTab } from './tabs/account-tab'
import { AiTab } from './tabs/ai-tab'
import { CrawlingTab } from './tabs/crawling-tab'
import { DataTab } from './tabs/data-tab'
import { SendingTab } from './tabs/sending-tab'
import { SuppressionTab } from './tabs/suppression-tab'

export const SETTINGS_TABS = [
  { value: 'account', label: 'Account' },
  { value: 'sending', label: 'Sending' },
  { value: 'ai', label: 'AI' },
  { value: 'crawling', label: 'Crawling' },
  { value: 'suppression', label: 'Suppression' },
  { value: 'data', label: 'Data' },
  { value: 'about', label: 'About' },
] as const

export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const settings = useSettings()
  const tab = SETTINGS_TABS.find((t) => t.value === params.get('tab'))?.value ?? 'account'

  return (
    <div className="mx-auto w-full max-w-3xl px-8 pt-2 pb-16">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Settings</h1>
      {settings.isPending ? (
        <div className="space-y-3" aria-label="Loading settings"><Skeleton className="h-9 w-2/3" /><Skeleton className="h-40" /></div>
      ) : settings.isError ? (
        <div className="space-y-3" role="alert">
          <p className="text-destructive text-sm">
            {settings.error instanceof ApiError ? settings.error.detail : 'Could not load settings.'}
          </p>
          <Button variant="outline" onClick={() => void settings.refetch()}>Retry</Button>
        </div>
      ) : (
        <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
          <TabsList className="mb-6">
            {SETTINGS_TABS.map((t) => <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>)}
          </TabsList>
          <TabsContent value="account"><AccountTab data={settings.data} /></TabsContent>
          <TabsContent value="sending"><SendingTab data={settings.data} /></TabsContent>
          <TabsContent value="ai"><AiTab data={settings.data} /></TabsContent>
          <TabsContent value="crawling"><CrawlingTab data={settings.data} /></TabsContent>
          <TabsContent value="suppression"><SuppressionTab /></TabsContent>
          <TabsContent value="data"><DataTab /></TabsContent>
          <TabsContent value="about"><AboutTab /></TabsContent>
        </Tabs>
      )}
    </div>
  )
}
