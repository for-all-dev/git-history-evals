import { useMemo, useRef } from 'react'
import type { Lang } from '../ablators/types'
import { highlight, TOK_CLASS } from '../lib/highlight'

// Editable source pane with live syntax highlighting. A <textarea> with
// transparent text sits over a <pre> that renders the same highlight() tokens as
// <CodeView>, aligned pixel-for-pixel (identical font/padding/whitespace) and
// scroll-synced. Keeps the source editable while showing per-language colours —
// something a bare <textarea> can't do. Very large inputs fall back to plain
// (highlight() itself bails >60k) so big AFP theories stay responsive.
export function CodeEditor({
  lang,
  value,
  onChange,
}: {
  lang: Lang
  value: string
  onChange: (v: string) => void
}) {
  const preRef = useRef<HTMLPreElement>(null)
  const toks = useMemo(() => highlight(lang, value), [lang, value])
  return (
    <div className="editor">
      <pre className="code editor-hl" ref={preRef} aria-hidden="true">
        <code>
          {toks.map((t, i) =>
            t.cls === 'plain' ? (
              t.text
            ) : (
              <span key={i} className={TOK_CLASS[t.cls]}>
                {t.text}
              </span>
            ),
          )}
          {'\n'}
        </code>
      </pre>
      <textarea
        className="source editor-input"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          const pre = preRef.current
          if (pre) {
            pre.scrollTop = e.currentTarget.scrollTop
            pre.scrollLeft = e.currentTarget.scrollLeft
          }
        }}
      />
    </div>
  )
}
