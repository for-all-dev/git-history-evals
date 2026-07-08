(* Group the token stream into spans. A Coq sentence ends at a "." token (the
   fullstop). But proof structure — focus braces [{] [}] and bullets [-] [+] [*]
   — is NOT "."-terminated, so we make each of those its own span (like
   Isabelle's prf_open / prf_close commands). The ablation depth-walk then reads
   exactly like the Isabelle one.

   Braces and bullets are only treated as structural when they occur in *tactic
   position* — i.e. the previous proper token was a "." / [{] / [}] / bullet (or
   the file start). This keeps term-level braces ([{ x | P x }], records
   [{| ... |}]) and operator uses of [- + *] from being mistaken for focusing. *)

type kind =
  | Sentence of { head : string; cmd_kind : string option }
  | Open (* a focusing { *)
  | Close (* a focusing } *)
  | Bullet of string (* a focusing bullet, e.g. "-", "--", "+", "*" *)
  | Ignored (* whitespace / comments only *)

type t = { kind : kind; content : Token.t list }

let source (s : t) : string = Token.implode s.content

let proper (s : t) : Token.t list = List.filter Token.is_proper s.content

(* a Symbol token that is a run of a single repeated bullet char *)
let is_bullet_source (src : string) : bool =
  String.length src >= 1
  && (let c0 = src.[0] in
      (c0 = '-' || c0 = '+' || c0 = '*')
      && String.for_all (fun c -> c = c0) src)

let is_bullet_token (t : Token.t) : bool =
  Token.is_symbol t && is_bullet_source t.source

(* classify a "."-terminated sentence by its first proper token(s) *)
let classify_sentence (content : Token.t list) : kind =
  let props = List.filter Token.is_proper content in
  match props with
  | [] -> Ignored
  | first :: rest ->
      let head = first.source in
      let cmd_kind =
        if not (Token.is_ident first) then None
        else if Keyword.is_proof head then Some Keyword.k_proof
        else if Keyword.is_terminator head then Keyword.terminator_kind head
        else if Keyword.is_goal_opener head then Some Keyword.k_goal
        else if
          head = "Next"
          && (match rest with t :: _ -> Token.is_ident_named t "Obligation" | _ -> false)
        then Some Keyword.k_goal
        else if head = "Obligation" then Some Keyword.k_goal
        else None (* conditional openers resolved in a post-pass *)
      in
      Sentence { head; cmd_kind }

(* the declared name of a goal sentence (best-effort), or "" *)
let name (s : t) : string =
  match s.kind with
  | Sentence { head; _ } ->
      if head = "Goal" || head = "Next" || head = "Obligation" then ""
      else begin
        (* first Ident after the head token *)
        let rec after_head = function
          | t :: rest when Token.is_proper t ->
              if t.Token.source = head then rest else after_head rest
          | _ :: rest -> after_head rest
          | [] -> []
        in
        let rec first_ident = function
          | (t : Token.t) :: rest ->
              if Token.is_space t || Token.is_comment t then first_ident rest
              else if Token.is_ident t then t.source
              else ""
          | [] -> ""
        in
        first_ident (after_head s.content)
      end
  | _ -> ""

let has_assign (s : t) : bool =
  List.exists (fun t -> Token.is_symbol_named t ":=") s.content

(* build spans from a token list *)
let build (toks : Token.t list) : t list =
  let spans = ref [] in
  let cur = ref [] in
  (* reversed accumulator *)
  let tactic_pos = ref true in
  let emit kind content = spans := { kind; content } :: !spans in
  let flush_sentence () =
    if !cur <> [] then begin
      let content = List.rev !cur in
      emit (classify_sentence content) content;
      cur := []
    end
  in
  let emit_boundary kind tok =
    (* leading ignored tokens (in [cur]) belong with the boundary token *)
    let content = List.rev (tok :: !cur) in
    emit kind content;
    cur := [];
    tactic_pos := true
  in
  List.iter
    (fun (t : Token.t) ->
      if Token.is_space t || Token.is_comment t then cur := t :: !cur
      else if Token.is_dot t then begin
        cur := t :: !cur;
        flush_sentence ();
        tactic_pos := true
      end
      else if !tactic_pos && Token.is_brace_named t "{" then emit_boundary Open t
      else if !tactic_pos && Token.is_brace_named t "}" then emit_boundary Close t
      else if !tactic_pos && is_bullet_token t then
        emit_boundary (Bullet t.source) t
      else begin
        cur := t :: !cur;
        tactic_pos := false
      end)
    toks;
  flush_sentence ();
  List.rev !spans

(* resolve conditional openers: a Definition/Fixpoint/Instance/... with no [:=]
   body whose next non-ignored span is [Proof] is an interactive goal. *)
let resolve_conditional (spans : t array) : unit =
  let n = Array.length spans in
  let next_proof i =
    let j = ref (i + 1) in
    while !j < n && spans.(!j).kind = Ignored do
      incr j
    done;
    !j < n
    && match spans.(!j).kind with
       | Sentence { cmd_kind = Some k; _ } -> k = Keyword.k_proof
       | _ -> false
  in
  for i = 0 to n - 1 do
    match spans.(i).kind with
    | Sentence { head; cmd_kind = None } when Keyword.is_conditional_opener head ->
        if (not (has_assign spans.(i))) && next_proof i then
          spans.(i) <- { spans.(i) with kind = Sentence { head; cmd_kind = Some Keyword.k_goal } }
    | _ -> ()
  done

let parse_spans (text : string) : t array =
  let spans = Array.of_list (build (Tokenize.tokenize text)) in
  resolve_conditional spans;
  spans

(* predicates used by the walk *)
let cmd_kind (s : t) : string option =
  match s.kind with Sentence { cmd_kind; _ } -> cmd_kind | _ -> None

let head (s : t) : string =
  match s.kind with Sentence { head; _ } -> head | _ -> ""

let is_goal s = cmd_kind s = Some Keyword.k_goal
let is_proof s = cmd_kind s = Some Keyword.k_proof
let is_qed s = cmd_kind s = Some Keyword.k_qed
let is_admitted s = cmd_kind s = Some Keyword.k_admitted
let is_abort s = cmd_kind s = Some Keyword.k_abort
let is_terminator s = is_qed s || is_admitted s || is_abort s
(* a structural block-closer that shrink must keep so the file stays balanced:
   [End <name>.] closes a Section / Module / Module Type. *)
let is_closer s = head s = "End"

(* a structural block-OPENER that shrink must likewise keep: [Section X.] /
   [Module X.] / [Module Type X.] open a block that an [End X.] later closes.
   If shrink keeps the [End] but drops the opener, the file is unbalanced
   ("There is nothing to end"). Keeping all openers + all closers preserves
   balance regardless of what content between them is trimmed. (A self-contained
   [Module X := Y.] also matches but is harmless to keep verbatim.) *)
let is_opener s =
  match head s with "Section" | "Module" -> true | _ -> false
let is_open s = s.kind = Open
let is_close s = s.kind = Close
let is_bullet s = match s.kind with Bullet _ -> true | _ -> false
let bullet_sig s = match s.kind with Bullet b -> b | _ -> ""
