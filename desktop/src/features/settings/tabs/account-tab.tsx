import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { GmailForm } from '@/features/account/gmail-form'
import type { SettingsResponse } from '@/lib/api/types'
import { SaveBar, Section, Warnings } from '../form-bits'
import { useSectionSave } from '../use-section-save'

const schema = z.object({
  display_name: z.string().trim(),
  footer_text: z.string().trim().min(1, { error: 'The opt-out footer cannot be empty' }),
})
type Values = z.infer<typeof schema>

export function AccountTab({ data }: { data: SettingsResponse }) {
  const s = data.settings
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: { display_name: s.display_name, footer_text: s.footer_text },
  })
  const { submit, warnings, saving } = useSectionSave(form)
  const errors = form.formState.errors

  return (
    <div className="space-y-8">
      <Section
        title="Gmail"
        description="Use a separate Gmail account for cold outreach, not your primary one."
      >
        <GmailForm address={s.gmail_address} passwordSet={data.gmail_password_set} />
      </Section>
      <Separator />
      <form onSubmit={submit} noValidate>
        <Section title="Sender" description="How you appear to recipients.">
          <FieldGroup>
            <Field data-invalid={!!errors.display_name}>
              <FieldLabel htmlFor="display-name">Display name</FieldLabel>
              <Input id="display-name" autoComplete="name" placeholder="Your real name" {...form.register('display_name')} />
              <FieldError errors={[errors.display_name]} />
            </Field>
            <Field data-invalid={!!errors.footer_text}>
              <FieldLabel htmlFor="footer-text">Opt-out footer</FieldLabel>
              <Textarea id="footer-text" rows={3} aria-invalid={!!errors.footer_text} {...form.register('footer_text')} />
              <FieldDescription>
                Added to every email. It cannot be turned off in service and investor modes.
              </FieldDescription>
              <FieldError errors={[errors.footer_text]} />
            </Field>
          </FieldGroup>
          <Warnings items={warnings} />
          <SaveBar saving={saving} dirty={form.formState.isDirty} />
        </Section>
      </form>
    </div>
  )
}
