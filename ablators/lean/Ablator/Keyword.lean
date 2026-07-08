/-
Lean 4 keyword classification. Lean's command vocabulary is fixed by the
language, so we enumerate it here rather than loading a table.

Lean's command
vocabulary is fixed by the language, so we enumerate it here. Three sets matter
for ablation:

  * `commandStarters` — tokens that, at column 0, begin a new top-level command
    (so we can split a file into command spans without a full parser).
  * `declKinds` — the subset whose body is a *term/proof* introduced by `:=`,
    i.e. the things we can replace with `sorry`.
  * `binders` — nested binding sites (`have`/`let`/`suffices`/`show`) that
    introduce a deeper `:=` proof obligation inside a body.
-/

import Std.Data.HashSet
import Ablator.Token

namespace Ablator
namespace Keyword

open Std (HashSet)

/-- Declaration commands whose body after `:=` is a term/proof we can ablate to
    `sorry`. Includes non-`Prop` definitions (`def`/`abbrev`/`instance`):
    `sorry` inhabits any type, so ablating a `def` body is meaningful too. -/
def declKindList : List String :=
  ["theorem", "lemma", "example", "def", "abbrev", "instance", "definition"]

/-- Modifiers that may precede a declaration on the command-leading line. -/
def modifierList : List String :=
  ["private", "protected", "public", "noncomputable", "partial", "unsafe", "nonrec",
   "scoped", "local", "mutual"]

/-- Other top-level commands (not ablated, but they delimit command spans). -/
def otherCommandList : List String :=
  ["structure", "inductive", "class", "opaque", "axiom", "constant",
   "module", "prelude", "import",
   "namespace", "section", "end", "open", "variable", "variables",
   "universe", "universes", "macro", "macro_rules", "notation", "infixl",
   "infixr", "infix", "prefix", "postfix", "syntax", "elab", "elab_rules",
   "attribute", "export", "deriving", "set_option", "initialize", "register_option",
   "declare_syntax_cat", "binder_predicate", "run_cmd", "run_elab", "#check",
   "#eval", "#print", "#reduce", "#synth", "#help", "builtin_initialize"]

/-- Nested binding keywords that open a deeper `:=`-bound goal in a body. -/
def binderList : List String :=
  ["have", "let", "suffices", "show", "replace"]

def declKinds : HashSet String := HashSet.ofList declKindList
def modifiers : HashSet String := HashSet.ofList modifierList
def otherCommands : HashSet String := HashSet.ofList otherCommandList
def binders : HashSet String := HashSet.ofList binderList

/-- Is `s` a declaration command whose `:=` body is ablatable? -/
def isDeclKind (s : String) : Bool := declKinds.contains s

/-- Does a column-0 occurrence of `s` (or `@[`) begin a new top-level command?
    Used to split the token stream into command spans. -/
def isCommandStarter (s : String) : Bool :=
  declKinds.contains s || modifiers.contains s || otherCommands.contains s

/-- Nested binding keyword (`have`/`let`/`suffices`/`show`/`replace`). -/
def isBinder (s : String) : Bool := binders.contains s

end Keyword
end Ablator
