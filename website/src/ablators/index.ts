import type { Ablator, Lang } from './types'
import { leanAblator } from './lean'
import { isabelleAblator } from './isabelle'
import { rocqAblator } from './rocq'

export * from './types'

export const ABLATORS: Record<Lang, Ablator> = {
  lean: leanAblator,
  isabelle: isabelleAblator,
  rocq: rocqAblator,
}

export function getAblator(lang: Lang): Ablator {
  return ABLATORS[lang]
}

/**
 * Guess the proof assistant from source text. Returns the best-scoring language
 * (defaults to `lean` if nothing matches). The UI keeps a manual override.
 */
export function detectLanguage(src: string): Lang {
  const s = src.slice(0, 20000)
  const score: Record<Lang, number> = { lean: 0, isabelle: 0, rocq: 0 }

  // Isabelle: outer-syntax theory scaffolding + \<...> symbol escapes.
  if (/^\s*theory\s+\w/m.test(s)) score.isabelle += 5
  if (/\bimports\b/.test(s)) score.isabelle += 3
  if (/^\s*begin\s*$/m.test(s)) score.isabelle += 2
  if (/\b(qed|done)\b/.test(s)) score.isabelle += 2
  if (/\\<[a-zA-Z]+>/.test(s)) score.isabelle += 3
  if (/^\s*(lemma|theorem|corollary)\b.*"/m.test(s)) score.isabelle += 2

  // Rocq/Coq: command-oriented, '.'-terminated, Proof./Qed./Admitted.
  if (/^\s*Require\s+(Import|Export)\b/m.test(s)) score.rocq += 5
  if (/^\s*Proof\./m.test(s)) score.rocq += 4
  if (/\bQed\.|\bAdmitted\.|\bDefined\./.test(s)) score.rocq += 4
  if (/^\s*(Lemma|Theorem|Definition|Fixpoint|Inductive)\b.*:/m.test(s)) score.rocq += 2

  // Lean 4: `:= by`, namespace/def, unicode connectives, `sorry`.
  if (/:=\s*by\b/.test(s)) score.lean += 5
  if (/^\s*namespace\s+\w/m.test(s)) score.lean += 3
  if (/^\s*(theorem|lemma|def|abbrev|instance)\b[^"]*:=/m.test(s)) score.lean += 3
  if (/\bsorry\b/.test(s)) score.lean += 1
  if (/[∧∨¬→↔∀∃≤≥]/.test(s)) score.lean += 1
  if (/#(eval|check|print)\b/.test(s)) score.lean += 2

  let best: Lang = 'lean'
  let bestScore = -1
  for (const l of ['isabelle', 'rocq', 'lean'] as Lang[]) {
    if (score[l] > bestScore) {
      best = l
      bestScore = score[l]
    }
  }
  return best
}
