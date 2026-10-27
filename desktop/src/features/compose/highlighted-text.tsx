import { cn } from '@/lib/utils'

interface Span { start: number; end: number }

/** Plain text with one highlighted range: the sentence the local model wrote. Whitespace is preserved. */
export function HighlightedText({ text, span, className }: { text: string; span: Span | null | undefined; className?: string }) {
  const ok = span && span.start >= 0 && span.end > span.start && span.end <= text.length
  return (
    <p className={cn('text-sm leading-relaxed break-words whitespace-pre-wrap', className)} data-testid="highlighted-text">
      {ok ? (
        <>
          {text.slice(0, span.start)}
          <mark
            className="bg-primary/15 text-foreground decoration-primary/60 rounded-sm px-0.5 underline decoration-dotted underline-offset-4"
            data-testid="opener-mark"
            title="Written by the local model from the lead's source text"
          >
            {text.slice(span.start, span.end)}
          </mark>
          {text.slice(span.end)}
        </>
      ) : text}
    </p>
  )
}
