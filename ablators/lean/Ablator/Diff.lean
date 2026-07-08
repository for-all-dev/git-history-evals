/-
A tiny self-rolled line differ (no `import Lean`, no IO, so it compiles to WASM).
We store `solution_diff` — a unified diff turning the challenge into the solution
— instead of the whole solution file, which is huge for big theories (issue #107).

Convention: lines are `split (· == '\n')` (a trailing newline yields a final ""
element); `apply` joins them back with '\n', so the round-trip is byte-exact.
Hunks are `@@ -oldStart,oldLen +newStart,newLen @@` with ` `/`-`/`+` prefixes; an
empty diff means challenge = solution.

Algorithm: Myers O(ND) greedy diff capped at `dMax` edits; beyond the cap we fall
back to a single hunk replacing the differing middle.
-/

namespace Ablator
namespace Diff

def dMax : Nat := 2000
def context : Nat := 3

inductive Op where
  | eq (s : String)
  | del (s : String)
  | ins (s : String)
  deriving Inhabited

@[inline] def blt (x y : Int) : Bool := decide (x < y)
@[inline] def ble (x y : Int) : Bool := decide (x ≤ y)

def isChange : Op → Bool
  | Op.eq _ => false
  | _ => true

/-- Myers greedy diff; `none` if the edit distance exceeds `dMax`. -/
def myers (a b : Array String) : Option (Array Op) := Id.run do
  let n : Int := a.size
  let m : Int := b.size
  let maxD : Nat := a.size + b.size
  if maxD == 0 then return some #[]
  let off : Int := maxD
  let idx : Int → Nat := fun k => (off + k).toNat
  let mut v : Array Int := Array.mkArray (2 * maxD + 1) 0
  let mut trace : Array (Array Int) := #[]
  let mut found : Option Int := none
  let mut d : Int := 0
  while ble d maxD && ble d dMax && found.isNone do
    trace := trace.push v
    let mut k : Int := -d
    while ble k d && found.isNone do
      let mut x : Int :=
        if k == -d || (k != d && blt (v[idx (k - 1)]!) (v[idx (k + 1)]!)) then v[idx (k + 1)]!
        else v[idx (k - 1)]! + 1
      let mut y : Int := x - k
      while blt x n && blt y m && a[x.toNat]! == b[y.toNat]! do
        x := x + 1
        y := y + 1
      v := v.set! (idx k) x
      if ble n x && ble m y then found := some d
      k := k + 2
    d := d + 1
  match found with
  | none => return none
  | some fd =>
    let mut ops : Array Op := #[]
    let mut x : Int := n
    let mut y : Int := m
    let mut dd : Int := fd
    while ble 0 dd do
      let vprev := trace[dd.toNat]!
      let k : Int := x - y
      let prevK : Int :=
        if k == -dd || (k != dd && blt (vprev[idx (k - 1)]!) (vprev[idx (k + 1)]!)) then k + 1
        else k - 1
      let prevX := vprev[idx prevK]!
      let prevY := prevX - prevK
      while blt prevX x && blt prevY y do
        ops := ops.push (Op.eq a[(x - 1).toNat]!)
        x := x - 1
        y := y - 1
      if blt 0 dd then
        if x == prevX then
          ops := ops.push (Op.ins b[(y - 1).toNat]!)
          y := y - 1
        else
          ops := ops.push (Op.del a[(x - 1).toNat]!)
          x := x - 1
      dd := dd - 1
    return some ops.reverse

def formatHunks (ops : Array Op) : String := Id.run do
  let len := ops.size
  let mut oldNo : Array Nat := Array.mkArray (len + 1) 1
  let mut newNo : Array Nat := Array.mkArray (len + 1) 1
  for i in [0:len] do
    let (o, nw) := match ops[i]! with
      | Op.eq _ => (1, 1)
      | Op.del _ => (1, 0)
      | Op.ins _ => (0, 1)
    oldNo := oldNo.set! (i + 1) (oldNo[i]! + o)
    newNo := newNo.set! (i + 1) (newNo[i]! + nw)
  let mut out := ""
  let mut i := 0
  while i < len do
    if isChange ops[i]! then
      let mut s := i
      let mut back := context
      while s > 0 && !(isChange ops[s - 1]!) && back > 0 do
        s := s - 1; back := back - 1
      let mut e := i
      let mut lastChange := i
      let mut cont := true
      while cont do
        if e + 1 ≥ len then
          cont := false
        else if isChange ops[e + 1]! then
          e := e + 1
          lastChange := e
        else
          let mut run := 0
          while e + 1 + run < len && !(isChange ops[e + 1 + run]!) do run := run + 1
          let nextChange := e + 1 + run
          if nextChange < len && run ≤ 2 * context then
            e := nextChange
            lastChange := nextChange
          else
            cont := false
      let mut stop := lastChange
      let mut fwd := context
      while stop + 1 < len && !(isChange ops[stop + 1]!) && fwd > 0 do
        stop := stop + 1; fwd := fwd - 1
      let ol := oldNo[stop + 1]! - oldNo[s]!
      let nl := newNo[stop + 1]! - newNo[s]!
      out := out ++ s!"@@ -{oldNo[s]!},{ol} +{newNo[s]!},{nl} @@\n"
      for j in [s:stop + 1] do
        out := out ++ (match ops[j]! with
          | Op.eq l => " " ++ l
          | Op.del l => "-" ++ l
          | Op.ins l => "+" ++ l) ++ "\n"
      i := stop + 1
    else i := i + 1
  return out

def fallback (a b : Array String) : String := Id.run do
  let n := a.size
  let m := b.size
  let mut p := 0
  while p < n && p < m && a[p]! == b[p]! do p := p + 1
  let mut s := 0
  while s < n - p && s < m - p && a[n - 1 - s]! == b[m - 1 - s]! do s := s + 1
  let ol := n - p - s
  let nl := m - p - s
  if ol == 0 && nl == 0 then return ""
  let mut out := s!"@@ -{p + 1},{ol} +{p + 1},{nl} @@\n"
  for j in [p:n - s] do out := out ++ "-" ++ a[j]! ++ "\n"
  for j in [p:m - s] do out := out ++ "+" ++ b[j]! ++ "\n"
  return out

end Diff

/-- Unified diff turning `challenge` into `solution`; "" if identical. -/
def unifiedDiff (challenge solution : String) : String :=
  if challenge == solution then ""
  else
    let a := (challenge.split (· == '\n')).toArray
    let b := (solution.split (· == '\n')).toArray
    match Diff.myers a b with
    | some ops => Diff.formatHunks ops
    | none => Diff.fallback a b

/-- Apply a `unifiedDiff` to `challenge`, recovering the solution. -/
def applyDiff (challenge diff : String) : String := Id.run do
  if diff == "" then return challenge
  let a := (challenge.split (· == '\n')).toArray
  let mut out : Array String := #[]
  let mut oi := 0
  for line in diff.split (· == '\n') do
    if line.startsWith "@@" then
      let os := ((((line.splitOn "-").getD 1 "").splitOn ",").getD 0 "").toNat!
      while oi < os - 1 do
        out := out.push a[oi]!; oi := oi + 1
    else if line == "" then pure ()
    else
      let c := line.front
      if c == ' ' then
        out := out.push (line.drop 1)
        oi := oi + 1
      else if c == '-' then
        oi := oi + 1
      else if c == '+' then
        out := out.push (line.drop 1)
      else
        pure ()
  while oi < a.size do
    out := out.push a[oi]!; oi := oi + 1
  return String.intercalate "\n" out.toList

end Ablator
