(* A self-contained lexer for Coq/Rocq surface syntax. It does NOT use Coq's
   real parser: we want a small, dependency-free core that compiles to a tiny
   binary and, ultimately, to WASM.

   Every byte is consumed by exactly one token, so concatenating sources
   round-trips byte-for-byte. Comments ([(* *)] nested), string literals,
   identifiers, numbers, and the all-important sentence-terminating "." are
   scanned by dedicated scanners that take priority, so a "." or keyword hiding
   inside a string/comment is never mistaken for code.

   Bytes >= 0x80 are treated as identifier characters: this keeps UTF-8 Unicode
   identifiers/operators glued into single tokens and guarantees losslessness.
   It can over-merge an exotic Unicode operator into an ident, but that never
   affects the ASCII-only signals the ablator relies on (the "." terminator,
   command keywords, braces, bullets). *)

let is_blank c =
  c = ' ' || c = '\t' || c = '\n' || c = '\r' || c = '\011' || c = '\012'

let is_digit c = c >= '0' && c <= '9'
let is_ascii_alpha c = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
let is_ident_start c = is_ascii_alpha c || c = '_' || Char.code c >= 0x80
let is_ident_cont c = is_ident_start c || is_digit c || c = '\''

(* operator characters that coalesce into one Symbol token (so [:=], [=>], [->]
   lex as single tokens). "." and the braces are handled separately. *)
let is_sym_char c = String.contains "!#$%&*+-/:<=>?@^~|" c

(* scanners return the index just past the token *)

let scan_blanks text n i =
  let j = ref i in
  while !j < n && is_blank text.[!j] do
    incr j
  done;
  !j

(* "..." with "" as an escaped quote. Returns (end, ok); ok=false if unterminated. *)
let scan_string text n i =
  let j = ref (i + 1) in
  let result = ref None in
  while !result = None do
    if !j >= n then result := Some (n, false)
    else if text.[!j] = '"' then
      if !j + 1 < n && text.[!j + 1] = '"' then j := !j + 2 (* escaped quote *)
      else result := Some (!j + 1, true)
    else incr j
  done;
  match !result with Some r -> r | None -> (n, false)

(* nested (* ... *) comment. String literals inside are skipped so an embedded
   "*)" cannot prematurely close the comment. Returns (end, ok). *)
let scan_comment text n i =
  let depth = ref 0 in
  let j = ref i in
  let result = ref None in
  while !result = None do
    if !j >= n then result := Some (n, false)
    else if !j + 1 < n && text.[!j] = '(' && text.[!j + 1] = '*' then begin
      incr depth;
      j := !j + 2
    end
    else if !j + 1 < n && text.[!j] = '*' && text.[!j + 1] = ')' then begin
      decr depth;
      j := !j + 2;
      if !depth = 0 then result := Some (!j, true)
    end
    else if text.[!j] = '"' then begin
      let e, _ = scan_string text n !j in
      j := e
    end
    else incr j
  done;
  match !result with Some r -> r | None -> (n, false)

(* identifier with qualified-name continuation: a "." continues the name only
   when immediately followed by an identifier-start char (no blank). So
   [Coq.Init.Nat.add] is one token; [add.] (dot then blank) stops before "." *)
let scan_ident text n i =
  let j = ref (i + 1) in
  while !j < n && is_ident_cont text.[!j] do
    incr j
  done;
  let continue = ref true in
  while !continue do
    if !j + 1 < n && text.[!j] = '.' && is_ident_start text.[!j + 1] then begin
      incr j;
      while !j < n && is_ident_cont text.[!j] do
        incr j
      done
    end
    else continue := false
  done;
  !j

(* a numeric literal: digits/hex body, then an optional fractional ".digits"
   (so [3.14] is one token and its "." is never a sentence boundary). *)
let scan_number text n i =
  let j = ref (i + 1) in
  while !j < n && is_ident_cont text.[!j] do
    incr j
  done;
  if !j + 1 < n && text.[!j] = '.' && is_digit text.[!j + 1] then begin
    incr j;
    while !j < n && is_digit text.[!j] do
      incr j
    done
  end;
  !j

let scan_symbol text n i =
  let j = ref (i + 1) in
  while !j < n && is_sym_char text.[!j] do
    incr j
  done;
  !j

(* Tokenize [text] into a lossless token stream (concatenated sources = text). *)
let tokenize (text : string) : Token.t list =
  let n = String.length text in
  let out = ref [] in
  let push kind a b = out := Token.make kind (String.sub text a (b - a)) :: !out in
  let i = ref 0 in
  while !i < n do
    let c = text.[!i] in
    if is_blank c then begin
      let e = scan_blanks text n !i in
      push Token.Space !i e;
      i := e
    end
    else if c = '(' && !i + 1 < n && text.[!i + 1] = '*' then begin
      let e, ok = scan_comment text n !i in
      push (if ok then Token.Comment else Token.Error) !i e;
      i := e
    end
    else if c = '"' then begin
      let e, ok = scan_string text n !i in
      push (if ok then Token.String else Token.Error) !i e;
      i := e
    end
    else if c = '{' && !i + 1 < n && text.[!i + 1] = '|' then begin
      push Token.Symbol !i (!i + 2); (* record {| ... — NOT a focus brace *)
      i := !i + 2
    end
    else if c = '|' && !i + 1 < n && text.[!i + 1] = '}' then begin
      push Token.Symbol !i (!i + 2); (* ... |} record close *)
      i := !i + 2
    end
    else if c = '{' || c = '}' then begin
      push Token.Brace !i (!i + 1);
      i := !i + 1
    end
    else if c = '.' then begin
      (* the fullstop rule: terminator iff followed by blank or EOF *)
      if !i + 1 >= n then begin
        push Token.Dot !i (!i + 1);
        i := !i + 1
      end
      else
        let d = text.[!i + 1] in
        if is_blank d then begin
          push Token.Dot !i (!i + 1);
          i := !i + 1
        end
        else if d = '.' then begin
          push Token.Symbol !i (!i + 2); (* ".." recursive-notation ellipsis *)
          i := !i + 2
        end
        else if d = '(' then begin
          push Token.Symbol !i (!i + 2); (* ".(" projection *)
          i := !i + 2
        end
        else begin
          push Token.Symbol !i (!i + 1);
          i := !i + 1
        end
    end
    else if is_ident_start c then begin
      let e = scan_ident text n !i in
      push Token.Ident !i e;
      i := e
    end
    else if is_digit c then begin
      let e = scan_number text n !i in
      push Token.Number !i e;
      i := e
    end
    else if c = '(' || c = ')' || c = '[' || c = ']' || c = ',' || c = ';' || c = '`'
    then begin
      push Token.Symbol !i (!i + 1); (* single-char delimiter *)
      i := !i + 1
    end
    else if is_sym_char c then begin
      let e = scan_symbol text n !i in
      push Token.Symbol !i e;
      i := e
    end
    else begin
      push Token.Symbol !i (!i + 1); (* lone unknown char: stands alone *)
      i := !i + 1
    end
  done;
  List.rev !out
