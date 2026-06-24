(* Outer tokens for Coq/Rocq source. A token carries its exact source slice;
   concatenating every token's [source] reproduces the input byte-for-byte (the
   lossless round-trip invariant the self-test checks).

   This module is deliberately free of any prover dependency so the whole core
   is a pure [string -> string] library that compiles small (and to WASM). *)

type kind =
  | Space (* whitespace incl. newlines *)
  | Comment (* (* ... *) nested *)
  | String (* "..." with "" escape *)
  | Ident (* identifier / qualified name a.b.c *)
  | Number (* 3, 0x1f, 3.14 *)
  | Dot (* a sentence terminator: "." followed by blank/EOF *)
  | Brace (* a single { or } (focusing, resolved in Span) *)
  | Symbol (* operator/punctuation run, incl. bullet runs -, +, * *)
  | Error (* unterminated comment/string (bytes preserved) *)

type t = { kind : kind; source : string }

let make kind source = { kind; source }
let is_space t = t.kind = Space
let is_comment t = t.kind = Comment

(* "proper" = contributes to the grammar: not whitespace, not a comment. *)
let is_proper t = (not (is_space t)) && not (is_comment t)
let is_ident t = t.kind = Ident
let is_number t = t.kind = Number
let is_dot t = t.kind = Dot
let is_brace t = t.kind = Brace
let is_symbol t = t.kind = Symbol
let is_error t = t.kind = Error
let is_brace_named t s = t.kind = Brace && t.source = s
let is_ident_named t s = t.kind = Ident && t.source = s
let is_symbol_named t s = t.kind = Symbol && t.source = s

(* Concatenate token sources (lossless). *)
let implode (ts : t list) : string =
  let b = Buffer.create 256 in
  List.iter (fun t -> Buffer.add_string b t.source) ts;
  Buffer.contents b
