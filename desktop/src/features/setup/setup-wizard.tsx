import { zodResolver } from '@hookform/resolvers/zod'
import { Check, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { CheckRow } from '@/components/check-row'
import { PullProgress } from '@/components/pull-progress'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Field, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { GmailForm } from '@/features/account/gmail-form'
import { ApiError } from '@/lib/api/client'
import { useCompleteSetup, usePullModel, useSetupStatus } from '@/lib/api/hooks'
import type { Check as CheckT, SetupStatus } from '@/lib/api/types'
import { toJobEvent } from '@/lib/jobs-store'
import { LegalNotice } from './legal'

const STEPS = ['Ollama', 'Model', 'Gmail', 'You', 'Done'] as const

const youSchema = z.object({
  display_name: z.string().trim().min(1, { error: 'Enter your real name' }),
  footer_text: z.string().trim().min(1, { error: 'The opt-out footer cannot be empty' }),
  legal: z.boolean().refine((v) => v, { error: 'Please confirm to continue' }),
})
type YouValues = z.infer<typeof youSchema>

function byId(checks: CheckT[], id: string): CheckT | undefined {
  return checks.find((c) => c.id === id)
}

function firstIncomplete(s: SetupStatus): number {
  if (byId(s.checks, 'ollama')?.status !== 'ok') return 0
  if (byId(s.checks, 'model')?.status !== 'ok') return 1
  return 2
}

export function SetupWizard({ initial }: { initial: SetupStatus }) {
  // Poll quickly: the user is installing things in another window while we wait.
  const { data } = useSetupStatus(2000)
  const status = data ?? initial
  const [step, setStep] = useState(() => firstIncomplete(initial))
  const [you, setYou] = useState<YouValues | null>(null)
  const complete = useCompleteSetup()

  const ollama = byId(status.checks, 'ollama')
  const model = byId(status.checks, 'model')
  const rest = status.checks.filter((c) => c.id === 'data_dir' || c.id === 'memory')
  const canNext = step === 0 ? ollama?.status === 'ok' : step === 1 ? model?.status === 'ok' : true

  async function finish() {
    if (!you) return
    try {
      await complete.mutateAsync({
        display_name: you.display_name,
        footer_text: you.footer_text,
        legal_acknowledged: true,
      })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Could not finish setup')
    }
  }

  return (
    <div className="bg-background flex h-full flex-col">
      <div className="drag h-12 shrink-0" />
      <div className="mx-auto flex w-full max-w-xl flex-1 flex-col overflow-y-auto px-6 pb-10">
        <h1 className="text-2xl font-semibold tracking-tight">Welcome to Reachout</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          A few quick checks, then you are ready. Everything runs on this Mac.
        </p>

        <ol className="mt-6 flex items-center gap-2" aria-label="Setup progress">
          {STEPS.map((name, i) => (
            <li key={name} className="flex items-center gap-2" aria-current={i === step ? 'step' : undefined}>
              <span
                className={
                  'flex size-6 items-center justify-center rounded-full text-xs font-medium ' +
                  (i < step
                    ? 'bg-primary text-primary-foreground'
                    : i === step
                      ? 'border-primary text-primary border-2'
                      : 'border text-muted-foreground')
                }
              >
                {i < step ? <Check className="size-3.5" aria-label="done" /> : i + 1}
              </span>
              <span className={'text-xs ' + (i === step ? 'font-medium' : 'text-muted-foreground')}>
                {name}
              </span>
              {i < STEPS.length - 1 ? <span className="bg-border h-px w-4" aria-hidden /> : null}
            </li>
          ))}
        </ol>

        <section className="mt-8 flex-1" aria-live="polite">
          {step === 0 && (
            <div className="space-y-4">
              <h2 className="text-lg font-medium">Ollama</h2>
              <p className="text-muted-foreground text-sm">
                Ollama runs the small language model that writes the one-line openers.
              </p>
              <div className="divide-y rounded-lg border px-3">
                {ollama ? <CheckRow check={ollama} /> : null}
                {rest.map((c) => <CheckRow key={c.id} check={c} />)}
              </div>
              {ollama?.status !== 'ok' ? (
                <p className="text-muted-foreground text-sm">
                  Waiting for Ollama… this page rechecks every couple of seconds.
                </p>
              ) : null}
            </div>
          )}

          {step === 1 && <ModelStep status={status} model={model} />}

          {step === 2 && (
            <div className="space-y-4">
              <h2 className="text-lg font-medium">Connect Gmail</h2>
              <p className="text-muted-foreground text-sm">
                Needed for sending and tracking replies. You can skip this and add it later in
                Settings.
              </p>
              <GmailForm address={status.gmail.address} passwordSet={status.gmail.password_set} />
            </div>
          )}

          {step === 3 && (
            <YouStep
              defaults={you ?? { display_name: status.display_name, footer_text: status.footer_text, legal: false }}
              onSubmit={(v) => { setYou(v); setStep(4) }}
              onBack={() => setStep(2)}
            />
          )}

          {step === 4 && (
            <div className="space-y-4">
              <h2 className="text-lg font-medium">You are all set</h2>
              <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
                <li>Sending as <strong className="text-foreground">{you?.display_name}</strong></li>
                <li>Model <strong className="text-foreground">{status.model}</strong> is ready</li>
                <li>
                  Gmail:{' '}
                  <strong className="text-foreground">
                    {status.gmail.password_set ? status.gmail.address : 'not connected yet'}
                  </strong>
                </li>
              </ul>
              <Button onClick={() => void finish()} disabled={complete.isPending}>
                {complete.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
                Open Reachout
              </Button>
            </div>
          )}
        </section>

        {step < 3 && (
          <div className="mt-8 flex justify-between">
            <Button variant="ghost" onClick={() => setStep(step - 1)} disabled={step === 0}>
              Back
            </Button>
            <Button onClick={() => setStep(step + 1)} disabled={!canNext}>
              {step === 2 && !status.gmail.password_set ? 'Skip for now' : 'Continue'}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

function ModelStep({ status, model }: { status: SetupStatus; model: CheckT | undefined }) {
  const pull = usePullModel()
  const [startedId, setStartedId] = useState<number | null>(null)
  const jobId = startedId ?? status.pull_job?.id ?? null
  const ready = model?.status === 'ok'

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Language model</h2>
      <p className="text-muted-foreground text-sm">
        Reachout uses <code className="font-mono">{status.model}</code>, a small model that fits
        comfortably in 8 GB of memory. It downloads once; the default model is about 2 GB.
      </p>
      {model && (ready || jobId === null) ? <div className="rounded-lg border px-3"><CheckRow check={model} /></div> : null}
      {!ready && jobId === null ? (
        <Button
          onClick={() => pull.mutate(undefined, { onSuccess: (j) => setStartedId(j.id), onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Could not start the download') })}
          disabled={pull.isPending || model?.detail === 'Ollama is not running.'}
        >
          {pull.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
          Download {status.model}
        </Button>
      ) : null}
      {!ready && jobId !== null ? <PullProgress jobId={jobId} seed={status.pull_job ? toJobEvent(status.pull_job) : null} /> : null}
      {!ready && jobId !== null && status.pull_job === null ? (
        <Button variant="outline" size="sm" onClick={() => setStartedId(null)}>
          Try again
        </Button>
      ) : null}
    </div>
  )
}

function YouStep(props: {
  defaults: YouValues
  onSubmit: (v: YouValues) => void
  onBack: () => void
}) {
  const form = useForm<YouValues>({
    resolver: zodResolver(youSchema),
    defaultValues: props.defaults,
  })
  const errors = form.formState.errors
  return (
    <form onSubmit={form.handleSubmit(props.onSubmit)} noValidate className="space-y-5">
      <h2 className="text-lg font-medium">About you</h2>
      <FieldGroup>
        <Field data-invalid={!!errors.display_name}>
          <FieldLabel htmlFor="w-name">Your name</FieldLabel>
          <Input id="w-name" autoComplete="name" aria-invalid={!!errors.display_name} {...form.register('display_name')} />
          <FieldError errors={[errors.display_name]} />
        </Field>
        <Field data-invalid={!!errors.footer_text}>
          <FieldLabel htmlFor="w-footer">Opt-out footer on every email</FieldLabel>
          <Textarea id="w-footer" rows={2} aria-invalid={!!errors.footer_text} {...form.register('footer_text')} />
          <FieldError errors={[errors.footer_text]} />
        </Field>
      </FieldGroup>
      <div className="max-h-48 overflow-y-auto rounded-lg border p-3">
        <LegalNotice />
      </div>
      <Field orientation="horizontal" data-invalid={!!errors.legal}>
        <Controller
          control={form.control}
          name="legal"
          render={({ field }) => (
            <Checkbox
              id="w-legal"
              checked={field.value === true}
              onCheckedChange={(c) => field.onChange(c === true)}
              aria-invalid={!!errors.legal}
            />
          )}
        />
        <FieldLabel htmlFor="w-legal" className="font-normal">
          I have read this and will follow these rules.
        </FieldLabel>
      </Field>
      <FieldError errors={[errors.legal]} />
      <div className="flex justify-between">
        <Button type="button" variant="ghost" onClick={props.onBack}>Back</Button>
        <Button type="submit">Continue</Button>
      </div>
    </form>
  )
}
