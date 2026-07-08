(* Corpus-wide proof-dependency centrality (fan-in by textual name citation),
   ported from the Isabelle ablator's [centrality.rs]. A declared name B is
   "cited" by goal A if A's proof body mentions the identifier B; fan-in of B is
   the number of distinct goals that cite it. Approximate (matched by name; we
   also project the last dotted component, so [Nat.add_comm] cites [add_comm]),
   and short names (< 3 chars) are dropped to avoid colliding with local
   variables. No prover required. *)

let min_name_len = 3

let cited_names (content : Token.t list) (tbl : (string, unit) Hashtbl.t) : unit =
  List.iter
    (fun (t : Token.t) ->
      if Token.is_ident t then begin
        Hashtbl.replace tbl t.source ();
        match String.rindex_opt t.source '.' with
        | Some i when i + 1 < String.length t.source ->
            Hashtbl.replace tbl (String.sub t.source (i + 1) (String.length t.source - i - 1)) ()
        | _ -> ()
      end)
    content

(* name -> fan-in over a corpus of theory texts *)
let fan_in (texts : string list) : (string, int) Hashtbl.t =
  let goals = ref [] in
  List.iter
    (fun text ->
      let spans = Span.parse_spans text in
      let n = Array.length spans in
      let i = ref 0 in
      while !i < n do
        if Span.is_goal spans.(!i) then begin
          let name = Span.name spans.(!i) in
          incr i;
          let depth = ref 1 in
          let tbl = Hashtbl.create 32 in
          let stop = ref false in
          while (not !stop) && !i < n do
            let s = spans.(!i) in
            if Span.is_goal s && !depth = 1 then stop := true (* leave for outer loop *)
            else begin
              cited_names s.content tbl;
              if Span.is_terminator s && !depth = 1 then begin
                incr i;
                stop := true
              end
              else begin
                (match s.kind with
                 | Span.Open -> incr depth
                 | Span.Close -> decr depth
                 | _ -> ());
                incr i
              end
            end
          done;
          let cited = Hashtbl.fold (fun k () acc -> k :: acc) tbl [] in
          goals := (name, cited) :: !goals
        end
        else incr i
      done)
    texts;
  let declared = Hashtbl.create 256 in
  List.iter
    (fun (name, _) ->
      if String.length name >= min_name_len then Hashtbl.replace declared name ())
    !goals;
  let fan = Hashtbl.create 256 in
  List.iter
    (fun (name, cited) ->
      List.iter
        (fun d ->
          if d <> name && Hashtbl.mem declared d then
            Hashtbl.replace fan d (1 + (try Hashtbl.find fan d with Not_found -> 0)))
        cited)
    !goals;
  fan
