// Minimal, dependency-free syntax highlighter for the three provers. Returns a
// flat list of {cls, text} tokens (rendered as <span>s by <CodeView>, so no
// HTML injection). Deliberately lightweight — comments, strings, keywords, and
// the ablation hole markers, which is what matters for reading a challenge.

import type { Lang } from '../ablators/types'

export type TokClass =
  | 'plain'
  | 'comment'
  | 'string'
  | 'keyword'
  | 'hole'
  | 'name'

export interface Tok {
  cls: TokClass
  text: string
}

/** token class -> CSS class (shared by CodeView + CodeEditor). */
export const TOK_CLASS: Record<TokClass, string> = {
  comment: 'tok-comment',
  string: 'tok-string',
  keyword: 'tok-keyword',
  hole: 'tok-hole',
  name: 'tok-name',
  plain: '',
}

const KEYWORDS: Record<Lang, Set<string>> = {
  lean: new Set([
    'theorem', 'lemma', 'def', 'abbrev', 'instance', 'example', 'namespace', 'end',
    'by', 'match', 'with', 'fun', 'have', 'let', 'show', 'from', 'calc', 'do',
    'if', 'then', 'else', 'structure', 'inductive', 'class', 'where', 'open',
    'import', 'variable', 'section', 'exact', 'apply', 'intro', 'simp', 'rw', 'omega',
  ]),
  isabelle: new Set([
    'theory', 'imports', 'begin', 'end', 'lemma', 'theorem', 'corollary',
    'proof', 'qed', 'done', 'by', 'apply', 'using', 'assumes', 'shows', 'fixes',
    'fun', 'definition', 'primrec', 'datatype', 'where', 'and', 'show', 'have',
    'obtain', 'then', 'thus', 'hence', 'next', 'case', 'unfolding',
  ]),
  rocq: new Set([
    'Require', 'Import', 'Export', 'Lemma', 'Theorem', 'Definition', 'Fixpoint',
    'Inductive', 'Proof', 'Qed', 'Admitted', 'Defined', 'forall', 'exists', 'fun',
    'match', 'with', 'end', 'let', 'in', 'if', 'then', 'else', 'intros', 'intro',
    'apply', 'rewrite', 'simpl', 'reflexivity', 'induction', 'destruct', 'split',
    'exact', 'assert', 'ring', 'auto', 'Notation', 'Section', 'Variable',
  ]),
}

const HOLE_WORDS: Record<Lang, RegExp> = {
  lean: /^(sorry|admit)\b/,
  isabelle: /^(sorry|oops)\b/,
  rocq: /^(Admitted|admit)\b/,
}

// per-language comment openers
function commentAt(lang: Lang, s: string, i: number): number {
  if (lang === 'rocq' || lang === 'isabelle') {
    // (* ... *) nested
    if (s.startsWith('(*', i)) {
      let depth = 1
      let j = i + 2
      while (j < s.length && depth > 0) {
        if (s.startsWith('(*', j)) {
          depth++
          j += 2
        } else if (s.startsWith('*)', j)) {
          depth--
          j += 2
        } else j++
      }
      return j
    }
  }
  if (lang === 'lean') {
    if (s.startsWith('--', i)) {
      let j = i + 2
      while (j < s.length && s[j] !== '\n') j++
      return j
    }
    if (s.startsWith('/-', i)) {
      let depth = 1
      let j = i + 2
      while (j < s.length && depth > 0) {
        if (s.startsWith('/-', j)) {
          depth++
          j += 2
        } else if (s.startsWith('-/', j)) {
          depth--
          j += 2
        } else j++
      }
      return j
    }
  }
  return -1
}

const IDENT = /^[A-Za-z_][A-Za-z0-9_'.]*/

export function highlight(lang: Lang, code: string): Tok[] {
  // guard: very large inputs render plain to stay responsive
  if (code.length > 60000) return [{ cls: 'plain', text: code }]
  const kw = KEYWORDS[lang]
  const holeRe = HOLE_WORDS[lang]
  const out: Tok[] = []
  let buf = ''
  const flush = () => {
    if (buf) {
      out.push({ cls: 'plain', text: buf })
      buf = ''
    }
  }
  let i = 0
  while (i < code.length) {
    const c = code[i]
    // comment
    const ce = commentAt(lang, code, i)
    if (ce > i) {
      flush()
      out.push({ cls: 'comment', text: code.slice(i, ce) })
      i = ce
      continue
    }
    // string (Isabelle inner-syntax uses "...", others use "..." for strings)
    if (c === '"') {
      flush()
      let j = i + 1
      while (j < code.length && code[j] !== '"') {
        if (code[j] === '\\') j++
        j++
      }
      j = Math.min(j + 1, code.length)
      out.push({ cls: 'string', text: code.slice(i, j) })
      i = j
      continue
    }
    // identifier / keyword / hole
    if (/[A-Za-z_]/.test(c)) {
      const rest = code.slice(i)
      const holeM = holeRe.exec(rest)
      if (holeM) {
        flush()
        out.push({ cls: 'hole', text: holeM[0] })
        i += holeM[0].length
        continue
      }
      const m = IDENT.exec(rest)
      if (m) {
        const word = m[0]
        flush()
        out.push({ cls: kw.has(word) ? 'keyword' : 'plain', text: word })
        i += word.length
        continue
      }
    }
    buf += c
    i++
  }
  flush()
  return out
}
