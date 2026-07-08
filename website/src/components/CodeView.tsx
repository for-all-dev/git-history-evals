import { useMemo } from 'react'
import type { Lang } from '../ablators/types'
import { highlight } from '../lib/highlight'
import { CopyButton } from './CopyButton'

const CLS: Record<string, string> = {
  comment: 'tok-comment',
  string: 'tok-string',
  keyword: 'tok-keyword',
  hole: 'tok-hole',
  name: 'tok-name',
  plain: '',
}

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
