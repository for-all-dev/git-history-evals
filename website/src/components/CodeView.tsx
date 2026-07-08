import { useMemo } from 'react'
import type { Lang } from '../ablators/types'
import { highlight, TOK_CLASS as CLS } from '../lib/highlight'
import { CopyButton } from './CopyButton'

export function CodeView({ lang, code }: { lang: Lang; code: string }) {
  const toks = useMemo(() => highlight(lang, code), [lang, code])
  return (
    <div className="codeview">
      <CopyButton text={code} />
      <pre className="code">
        <code>
          {toks.map((t, i) =>
            t.cls === 'plain' ? (
              t.text
            ) : (
              <span key={i} className={CLS[t.cls]}>
                {t.text}
              </span>
            ),
          )}
        </code>
      </pre>
    </div>
  )
}
