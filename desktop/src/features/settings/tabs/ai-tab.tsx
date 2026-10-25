import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { PullProgress } from '@/components/pull-progress'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { ApiError } from '@/lib/api/client'
import { useInstalledModels, usePullModel, useSetupStatus } from '@/lib/api/hooks'
import type { SettingsResponse } from '@/lib/api/types'
import { toJobEvent } from '@/lib/jobs-store'
import { SaveBar, Section, Warnings } from '../form-bits'
import { useSectionSave } from '../use-section-save'

const schema = z.object({
  ollama_model: z.string().trim().min(1, { error: 'Choose a model' }),
  num_ctx: z.number({ error: 'Enter a number' }).int().min(1024, { error: 'At least 1024' }).max(8192, { error: 'At most 8192' }),
  keep_alive: z.string().trim().regex(/^(-1|0|\d+[smh])$/, { error: 'Use a duration like 5m, 1h, or 0' }),
})
type Values = z.infer<typeof schema>

export function AiTab({ data }: { data: SettingsResponse }) {
  const s = data.settings
  const models = useInstalledModels()
  const status = useSetupStatus()
  const pull = usePullModel()
  const [pullName, setPullName] = useState('')
  const [jobId, setJobId] = useState<number | null>(null)

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: { ollama_model: s.ollama_model, num_ctx: s.num_ctx, keep_alive: s.keep_alive },
  })
  const { submit, warnings, saving } = useSectionSave(form)
  const e = form.formState.errors

  const installed = models.data?.installed ?? []
  const options = installed.includes(s.ollama_model) ? installed : [s.ollama_model, ...installed]
  const activeJob = jobId ?? status.data?.pull_job?.id ?? null

  return (
    <div className="space-y-8">
      <form onSubmit={submit} noValidate className="space-y-6">
        <Section title="Language model" description="Only one model runs at a time, to fit in 8 GB.">
          <FieldGroup>
            <Field data-invalid={!!e.ollama_model}>
              <FieldLabel htmlFor="model">Model</FieldLabel>
              <Controller control={form.control} name="ollama_model" render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="model" className="w-72" aria-invalid={!!e.ollama_model}>
                    <SelectValue placeholder="Choose a model" />
                  </SelectTrigger>
                  <SelectContent>
                    {options.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}{installed.includes(m) ? '' : ' (not installed)'}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )} />
              <FieldDescription>
                {models.data && !models.data.ollama_running
                  ? 'Ollama is not running, so installed models cannot be listed.'
                  : 'Installed models. Suggested: qwen2.5:3b-instruct, llama3.2:3b, qwen3:4b.'}
              </FieldDescription>
              <FieldError errors={[e.ollama_model]} />
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field data-invalid={!!e.num_ctx}>
                <FieldLabel htmlFor="num-ctx">Context window (num_ctx)</FieldLabel>
                <Input id="num-ctx" type="number" aria-invalid={!!e.num_ctx} {...form.register('num_ctx', { valueAsNumber: true })} />
                <FieldError errors={[e.num_ctx]} />
              </Field>
              <Field data-invalid={!!e.keep_alive}>
                <FieldLabel htmlFor="keep-alive">Keep model loaded (keep_alive)</FieldLabel>
                <Input id="keep-alive" aria-invalid={!!e.keep_alive} {...form.register('keep_alive')} />
                <FieldError errors={[e.keep_alive]} />
              </Field>
            </div>
          </FieldGroup>
          <Warnings items={warnings} />
          <SaveBar saving={saving} dirty={form.formState.isDirty} />
        </Section>
      </form>

      <Separator />

      <Section title="Download a model" description="Runs in the background; you can keep using the app.">
        <div className="flex gap-2">
          <Input
            aria-label="Model to download"
            className="max-w-72"
            placeholder={s.ollama_model}
            value={pullName}
            onChange={(ev) => setPullName(ev.target.value)}
          />
          <Button
            variant="secondary"
            disabled={pull.isPending}
            onClick={() =>
              pull.mutate(pullName.trim() || undefined, {
                onSuccess: (j) => setJobId(j.id),
                onError: (err) => toast.error(err instanceof ApiError ? err.detail : 'Could not start the download'),
              })
            }
          >
            {pull.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Download
          </Button>
        </div>
        {activeJob !== null ? (
          <PullProgress jobId={activeJob} seed={status.data?.pull_job ? toJobEvent(status.data.pull_job) : null} />
        ) : null}
      </Section>
    </div>
  )
}
