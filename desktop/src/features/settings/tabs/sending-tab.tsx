import { zodResolver } from '@hookform/resolvers/zod'
import { Controller, useForm } from 'react-hook-form'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import type { SettingsResponse } from '@/lib/api/types'
import { SaveBar, Section, Warnings } from '../form-bits'
import { useSectionSave } from '../use-section-save'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const DEFAULT_DAYS = [0, 1, 2, 3, 4, 5]
const time = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, { error: 'Use HH:MM' })
const whole = (min: number, max: number) =>
  z.number({ error: 'Enter a number' }).int({ error: 'Whole numbers only' })
    .min(min, { error: `At least ${min}` }).max(max, { error: `At most ${max}` })

export const sendingSchema = z.object({
  daily_cap: whole(1, 100),
  delay_min_s: whole(0, 3600),
  delay_max_s: whole(0, 3600),
  window_start: time,
  window_end: time,
  send_days: z.array(z.number()).min(1, { error: 'Pick at least one day' }),
  allow_guessed_emails: z.boolean(),
  max_per_domain_per_day: whole(1, 10),
  min_minutes_between_same_domain: whole(0, 1440),
  use_recipient_timezone: z.boolean(),
}).refine((v) => v.delay_max_s >= v.delay_min_s, { path: ['delay_max_s'], error: 'Must be at least the minimum delay' })
  .refine((v) => v.window_end > v.window_start, { path: ['window_end'], error: 'Must be after the start' })
type Values = z.infer<typeof sendingSchema>

export function SendingTab({ data }: { data: SettingsResponse }) {
  const s = data.settings
  const ceiling = data.limits.hard_daily_ceiling
  const form = useForm<Values>({
    resolver: zodResolver(sendingSchema),
    values: {
      daily_cap: s.daily_cap, delay_min_s: s.delay_min_s, delay_max_s: s.delay_max_s,
      window_start: s.window_start, window_end: s.window_end, send_days: s.send_days ?? DEFAULT_DAYS,
      allow_guessed_emails: s.allow_guessed_emails,
      max_per_domain_per_day: s.max_per_domain_per_day ?? 2, min_minutes_between_same_domain: s.min_minutes_between_same_domain ?? 60,
      use_recipient_timezone: s.use_recipient_timezone ?? true,
    },
  })
  const { submit, warnings, saving } = useSectionSave(form)
  const e = form.formState.errors
  const cap = form.watch('daily_cap')

  return (
    <form onSubmit={submit} noValidate className="space-y-6">
      <Section title="Sending limits" description="Enforced by the backend; the app can never exceed them.">
        <FieldGroup>
          <Field data-invalid={!!e.daily_cap}>
            <FieldLabel htmlFor="daily-cap">Daily cap</FieldLabel>
            <Input id="daily-cap" type="number" inputMode="numeric" className="w-32" aria-invalid={!!e.daily_cap}
              {...form.register('daily_cap', { valueAsNumber: true })} />
            <FieldDescription>
              Emails per day. Hard ceiling {ceiling}. Suggested ramp: 10, 15, 20, 25, 30 over your
              first five sending days.
            </FieldDescription>
            {typeof cap === 'number' && cap > 25 ? (
              <p className="text-sm text-amber-700 dark:text-amber-400" role="status">
                Above the safe default of 25. New Gmail accounts can be throttled.
              </p>
            ) : null}
            <FieldError errors={[e.daily_cap]} />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.delay_min_s}>
              <FieldLabel htmlFor="delay-min">Minimum delay between sends (s)</FieldLabel>
              <Input id="delay-min" type="number" aria-invalid={!!e.delay_min_s} {...form.register('delay_min_s', { valueAsNumber: true })} />
              <FieldError errors={[e.delay_min_s]} />
            </Field>
            <Field data-invalid={!!e.delay_max_s}>
              <FieldLabel htmlFor="delay-max">Maximum delay (s)</FieldLabel>
              <Input id="delay-max" type="number" aria-invalid={!!e.delay_max_s} {...form.register('delay_max_s', { valueAsNumber: true })} />
              <FieldError errors={[e.delay_max_s]} />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.window_start}>
              <FieldLabel htmlFor="win-start">Send window starts</FieldLabel>
              <Input id="win-start" type="time" aria-invalid={!!e.window_start} {...form.register('window_start')} />
              <FieldError errors={[e.window_start]} />
            </Field>
            <Field data-invalid={!!e.window_end}>
              <FieldLabel htmlFor="win-end">Send window ends</FieldLabel>
              <Input id="win-end" type="time" aria-invalid={!!e.window_end} {...form.register('window_end')} />
              <FieldError errors={[e.window_end]} />
            </Field>
          </div>

          <Field data-invalid={!!e.send_days}>
            <FieldLabel id="days-label">Send days</FieldLabel>
            <Controller control={form.control} name="send_days" render={({ field }) => (
              <div role="group" aria-labelledby="days-label" className="flex flex-wrap gap-1.5">
                {DAYS.map((d, i) => {
                  const on = field.value.includes(i)
                  return (
                    <Button key={d} type="button" size="sm" variant={on ? 'default' : 'outline'}
                      aria-pressed={on}
                      onClick={() => field.onChange(on ? field.value.filter((x) => x !== i) : [...field.value, i].sort())}>
                      {d}
                    </Button>
                  )
                })}
              </div>
            )} />
            <FieldError errors={[e.send_days as { message?: string } | undefined]} />
          </Field>
        </FieldGroup>
      </Section>

      <Section title="Per company" description="Several people at one company compare notes. These limits apply across all campaigns.">
        <FieldGroup>
          <div className="grid grid-cols-2 gap-4">
            <Field data-invalid={!!e.max_per_domain_per_day}>
              <FieldLabel htmlFor="per-domain">Emails per company per day</FieldLabel>
              <Input id="per-domain" type="number" aria-invalid={!!e.max_per_domain_per_day} {...form.register('max_per_domain_per_day', { valueAsNumber: true })} />
              <FieldError errors={[e.max_per_domain_per_day]} />
            </Field>
            <Field data-invalid={!!e.min_minutes_between_same_domain}>
              <FieldLabel htmlFor="domain-gap">Minutes between people at one company</FieldLabel>
              <Input id="domain-gap" type="number" aria-invalid={!!e.min_minutes_between_same_domain} {...form.register('min_minutes_between_same_domain', { valueAsNumber: true })} />
              <FieldError errors={[e.min_minutes_between_same_domain]} />
            </Field>
          </div>
          <Field orientation="horizontal">
            <Controller control={form.control} name="use_recipient_timezone" render={({ field }) => (
              <Switch id="recipient-tz" checked={field.value} onCheckedChange={field.onChange} />
            )} />
            <div>
              <FieldLabel htmlFor="recipient-tz">Use the recipient's time zone for the send window</FieldLabel>
              <FieldDescription>
                Inferred from a country domain such as .in or .de, or from the search region. When it cannot be told, your own clock is used.
              </FieldDescription>
            </div>
          </Field>
        </FieldGroup>
      </Section>

      <Section title="Guessed addresses">
        <Field orientation="horizontal">
          <Controller control={form.control} name="allow_guessed_emails" render={({ field }) => (
            <Switch id="allow-guessed" checked={field.value} onCheckedChange={field.onChange} />
          )} />
          <div>
            <FieldLabel htmlFor="allow-guessed">Allow sending to guessed addresses</FieldLabel>
            <FieldDescription>
              Patterns like first.last@company are only MX-checked, never verified, and bounce often.
              Off by default.
            </FieldDescription>
          </div>
        </Field>
      </Section>

      <Warnings items={warnings} />
      <SaveBar saving={saving} dirty={form.formState.isDirty} />
    </form>
  )
}
