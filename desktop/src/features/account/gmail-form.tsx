import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { useForm } from 'react-hook-form'
import { toast } from 'sonner'
import { z } from 'zod'
import { CheckRow } from '@/components/check-row'
import { Button } from '@/components/ui/button'
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { ApiError } from '@/lib/api/client'
import { useRemoveGmailPassword, useSaveGmail, useTestGmail } from '@/lib/api/hooks'

const schema = z.object({
  address: z.email({ error: 'Enter a valid email address' }),
  app_password: z.string().optional(),
})
type Values = z.infer<typeof schema>

interface Props {
  address: string
  passwordSet: boolean
}

export function GmailForm({ address, passwordSet }: Props) {
  const save = useSaveGmail()
  const remove = useRemoveGmailPassword()
  const test = useTestGmail()
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    values: { address, app_password: '' },
  })

  async function onSubmit(v: Values) {
    try {
      const res = await save.mutateAsync({ address: v.address, app_password: v.app_password || null })
      form.reset({ address: res.address, app_password: '' })
      if (res.password_set) toast.success('Saved. The App Password is in your macOS Keychain.')
      else toast.warning('Address saved. Add the App Password to enable sending.')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail : 'Could not save')
    }
  }

  const canTest = passwordSet && address !== '' && !form.formState.isDirty

  return (
    <div className="space-y-4">
      <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="space-y-4">
        <FieldGroup>
          <Field data-invalid={!!form.formState.errors.address}>
            <FieldLabel htmlFor="gmail-address">Gmail address</FieldLabel>
            <Input
              id="gmail-address"
              type="email"
              autoComplete="off"
              placeholder="you@gmail.com"
              aria-invalid={!!form.formState.errors.address}
              {...form.register('address')}
            />
            <FieldError errors={[form.formState.errors.address]} />
          </Field>
          <Field>
            <FieldLabel htmlFor="gmail-password">App Password</FieldLabel>
            <Input
              id="gmail-password"
              type="password"
              autoComplete="off"
              placeholder={passwordSet ? 'Saved in Keychain. Leave blank to keep it.' : '16-character App Password'}
              {...form.register('app_password')}
            />
            <FieldDescription>
              Stored in the macOS Keychain. It is never written to a file or the database, and
              never shown again.
            </FieldDescription>
          </Field>
        </FieldGroup>

        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null} Save
          </Button>
          {passwordSet ? (
            <Button
              type="button"
              variant="outline"
              disabled={remove.isPending}
              onClick={() => remove.mutate(undefined, { onSuccess: () => toast.success('Removed from Keychain') })}
            >
              Remove saved password
            </Button>
          ) : null}
          <Button
            type="button"
            variant="secondary"
            disabled={!canTest || test.isPending}
            onClick={() => test.mutate()}
          >
            {test.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Test connection
          </Button>
        </div>
      </form>

      {test.isPending ? (
        <p className="text-muted-foreground text-sm">Logging in to Gmail (SMTP and IMAP)…</p>
      ) : null}
      {test.isError ? (
        <p className="text-destructive text-sm" role="alert">
          {test.error instanceof ApiError ? test.error.detail : 'Test failed'}
        </p>
      ) : null}
      {test.data ? (
        <div className="divide-y rounded-lg border px-3">
          <CheckRow check={test.data.smtp} />
          <CheckRow check={test.data.imap} />
        </div>
      ) : null}

      <details className="text-sm">
        <summary className="text-muted-foreground hover:text-foreground cursor-pointer">
          How do I get an App Password?
        </summary>
        <ol className="text-muted-foreground mt-2 list-decimal space-y-1 pl-5">
          <li>Use a separate Gmail account for cold outreach, not your primary one.</li>
          <li>Turn on 2-Step Verification (Google Account, Security).</li>
          <li>Open App passwords, create one named “Reachout”, and paste the 16 characters here.</li>
          <li>In Gmail settings, under Forwarding and POP/IMAP, enable IMAP.</li>
        </ol>
      </details>
    </div>
  )
}
