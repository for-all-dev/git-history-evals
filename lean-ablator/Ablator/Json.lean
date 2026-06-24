/-
A tiny JSON encoder — just enough to emit dataset records, with no dependency
on `Lean.Data.Json` (keeps the core importable without `import Lean` and small
for WASM). Insertion order is preserved (records are ordered objects), matching
the field order of the dataset records.
-/

namespace Ablator

inductive Json where
  | null
  | bool (b : Bool)
  | num (n : Int)
  | float (f : Float)
  | str (s : String)
  | arr (xs : List Json)
  | obj (kvs : List (String × Json))
  deriving Inhabited

namespace Json

/-- Escape a string per JSON (\", \\, control chars, newlines, tabs). -/
def escape (s : String) : String := Id.run do
  let mut acc := "\""
  for c in s.toList do
    acc := acc ++
      (if c == '"' then "\\\""
       else if c == '\\' then "\\\\"
       else if c == '\n' then "\\n"
       else if c == '\r' then "\\r"
       else if c == '\t' then "\\t"
       else if c.toNat < 0x20 then
         let hex := String.mk (Nat.toDigits 16 c.toNat)
         "\\u" ++ (String.mk (List.replicate (4 - hex.length) '0')) ++ hex
       else String.singleton c)
  return acc ++ "\""

private def numStr (n : Int) : String := toString n

/-- Compact (one-line) serialization. -/
partial def compact : Json → String
  | null => "null"
  | bool b => if b then "true" else "false"
  | num n => numStr n
  | float f => toString f
  | str s => escape s
  | arr xs => "[" ++ String.intercalate "," (xs.map compact) ++ "]"
  | obj kvs =>
    "{" ++ String.intercalate "," (kvs.map (fun (k, v) => escape k ++ ":" ++ compact v)) ++ "}"

/-- Indented (pretty) serialization, 2-space indent. -/
partial def pretty (j : Json) : String :=
  let rec go (ind : Nat) : Json → String
    | null => "null"
    | bool b => if b then "true" else "false"
    | num n => numStr n
    | float f => toString f
    | str s => escape s
    | arr [] => "[]"
    | arr xs =>
      let pad := String.mk (List.replicate ((ind + 1) * 2) ' ')
      let close := String.mk (List.replicate (ind * 2) ' ')
      "[\n" ++ String.intercalate ",\n" (xs.map (fun x => pad ++ go (ind + 1) x))
        ++ "\n" ++ close ++ "]"
    | obj [] => "{}"
    | obj kvs =>
      let pad := String.mk (List.replicate ((ind + 1) * 2) ' ')
      let close := String.mk (List.replicate (ind * 2) ' ')
      "{\n" ++ String.intercalate ",\n"
        (kvs.map (fun (k, v) => pad ++ escape k ++ ": " ++ go (ind + 1) v))
        ++ "\n" ++ close ++ "}"
  go 0 j

end Json
end Ablator
