import { useState } from 'react'
import type { FieldValues, Path, UseFormReturn } from 'react-hook-form'
import { toast } from 'sonner'
import { ApiError } from '@/lib/api/client'
import { useUpdateSettings } from '@/lib/api/hooks'
import type { SettingsUpdate } from '@/lib/api/types'

/** Submit handler shared by the settings tabs: PUT /settings, then show notices, warnings, field errors. */
export function useSectionSave<T extends FieldValues>(
  form: UseFormReturn<T>,
  /** Convert form values to the API shape when they differ (e.g. a textarea that holds a list). */
  transform?: (values: T) => SettingsUpdate,
) {
  const update = useUpdateSettings()
  const [warnings, setWarnings] = useState<string[]>([])

  const submit = form.handleSubmit(async (values) => {
    try {
      const res = await update.mutateAsync(transform ? transform(values) : (values as unknown as SettingsUpdate))
      form.reset(values)
      setWarnings(res.warnings)
      for (const n of res.notices) toast.info(n)
      toast.success('Settings saved')
    } catch (e) {
      if (e instanceof ApiError) {
        for (const [field, message] of Object.entries(e.fieldErrors)) {
          form.setError(field as Path<T>, { message })
        }
        toast.error(Object.keys(e.fieldErrors).length > 0 ? 'Some values are not valid' : e.detail)
      } else {
        toast.error('Could not save settings')
      }
    }
  })

  return { submit, warnings, saving: update.isPending }
}
