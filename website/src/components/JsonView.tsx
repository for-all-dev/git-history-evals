import { useState } from 'react'
import { CopyButton } from './CopyButton'

// Collapsible JSON viewer. Per issue #112: by default only the challenges and
// the solutions are unfolded; everything else (holes, metadata) starts folded.

const UNFOLD = new Set([
  'challenge_file_content',
  'solution_diff',
  'solution',
  'challenge',
])

type Json = unknown

function isContainer(v: Json): v is object {
  return typeof v === 'object' && v !== null
}

function defaultCollapsed(key: string | undefined, value: Json, depth: number): boolean {
  if (depth === 0) return false // the record itself is open
  if (key && UNFOLD.has(key)) return false
  if (isContainer(value)) return true
  return false
}

function Leaf({ keyName, value }: { keyName?: string; value: Json }) {
  const unfold = keyName ? UNFOLD.has(keyName) : false
  const [open, setOpen] = useState(unfold)
  if (typeof value === 'string' && (unfold || value.includes('\n'))) {
    if (!open) {
      return (
        <span className="json-str-collapsed" onClick={() => setOpen(true)}>
          "{value.slice(0, 60).replace(/\n/g, '⏎')}
          {value.length > 60 ? '…' : ''}" <span className="json-hint">({value.length} chars)</span>
        </span>
      )
    }
    return (
      <div className="json-block">
        <button type="button" className="json-toggle" onClick={() => setOpen(false)}>
          ▾ string ({value.length} chars)
        </button>
        <pre className="json-strval">{value}</pre>
      </div>
    )
  }
  const cls =
    typeof value === 'string'
      ? 'json-string'
      : typeof value === 'number'
        ? 'json-number'
        : typeof value === 'boolean'
          ? 'json-bool'
          : 'json-null'
  return <span className={cls}>{JSON.stringify(value)}</span>
}

function Node({
  keyName,
  value,
  depth,
}: {
  keyName?: string
  value: Json
  depth: number
}) {
  const [collapsed, setCollapsed] = useState(() => defaultCollapsed(keyName, value, depth))

  if (!isContainer(value)) {
    return (
      <div className="json-row" style={{ paddingLeft: depth ? 16 : 0 }}>
        {keyName !== undefined && <span className="json-key">{keyName}: </span>}
        <Leaf keyName={keyName} value={value} />
      </div>
    )
  }

  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, Json>)
  const brackets = Array.isArray(value) ? ['[', ']'] : ['{', '}']

  return (
    <div className="json-row" style={{ paddingLeft: depth ? 16 : 0 }}>
      <button type="button" className="json-toggle" onClick={() => setCollapsed((c) => !c)}>
        {collapsed ? '▸' : '▾'} {keyName !== undefined ? <span className="json-key">{keyName}: </span> : null}
        {brackets[0]}
        {collapsed ? <span className="json-hint">…{entries.length}…</span> : ''}
        {collapsed ? brackets[1] : ''}
      </button>
      {!collapsed && (
        <div className="json-children">
          {entries.map(([k, v]) => (
            <Node key={k} keyName={Array.isArray(value) ? undefined : k} value={v} depth={depth + 1} />
          ))}
          <div className="json-row" style={{ paddingLeft: depth ? 16 : 0 }}>
            {brackets[1]}
          </div>
        </div>
      )}
    </div>
  )
}

export function JsonView({ data }: { data: Json }) {
  const text = JSON.stringify(data, null, 2)
  return (
    <div className="jsonview">
      <CopyButton text={text} label="copy JSON" />
      <div className="json-tree">
        <Node value={data} depth={0} />
      </div>
    </div>
  )
}
