(* The (challenge, solution) JSON record. Its schema and field order match the
   git-history datasets in [../artifacts] and the sibling Isabelle/Lean
   ablators, with [proof_assistant: "coq"], so a single dump lines up
   field-for-field with the existing data. JSON via yojson ([`Assoc] preserves
   insertion order). *)

let depth_json (d : int) : Yojson.Safe.t =
  if d = Ablate.inf then `String "inf" else `Int d

let task_id (file_path : string) (variant : int option) : string =
  let h = Sha1.hex file_path in
  let base = "ablate_" ^ String.sub h 0 12 in
  match variant with Some v -> base ^ "_" ^ string_of_int v | None -> base

let theory_name (file_path : string) : string =
  let base =
    match String.rindex_opt file_path '/' with
    | Some i -> String.sub file_path (i + 1) (String.length file_path - i - 1)
    | None -> file_path
  in
  if Filename.check_suffix base ".v" then Filename.chop_suffix base ".v" else base

let hole_json (h : Ablate.hole) : Yojson.Safe.t =
  `Assoc
    [
      ("theorem_name", `String h.theorem_name);
      ("depth", `Int h.depth);
      ("n_commands", `Int h.n_commands);
      ("n_lines", `Int h.n_lines);
      ("is_leaf", `Bool h.is_leaf);
      ("centrality", `Int h.centrality);
      ("method", `String h.method_);
      ("proof_text", `String h.proof_text);
    ]

(* lightweight {text,total,ablated,holes} shape returned to the browser demo *)
let result_json (r : Ablate.result) : Yojson.Safe.t =
  `Assoc
    [
      ("text", `String r.text);
      ("solution_diff", `String (Diff.unified r.text r.solution));
      ("total", `Int r.total);
      ("ablated", `Int r.ablated);
      ("holes", `List (List.map hole_json r.holes));
      ( "deleted_lemmas",
        `List (List.map (fun (nm, txt) -> `Assoc [ ("name", `String nm); ("text", `String txt) ]) r.deleted) );
    ]

let record ~(file_path : string) ~(session : string) ~(spec : Ablate.spec)
    ~(seed : int) ~(variant : int option) ~(difficulty : string option)
    ~(result : Ablate.result) : Yojson.Safe.t =
  let opt_int = function Some n -> `Int n | None -> `Null in
  let opt_str = function Some s -> `String s | None -> `Null in
  `Assoc
    [
      ("task_id", `String (task_id file_path variant));
      ("proof_assistant", `String "coq");
      ("session", `String session);
      ("file_path", `String file_path);
      ("theory", `String (theory_name file_path));
      ("variant", opt_int variant);
      ("challenge_type", `String (if result.deleted <> [] then "lemma_delete" else "proof_ablate"));
      ("difficulty", opt_str difficulty);
      ("count", opt_int spec.count);
      ("by_centrality", `Bool spec.by_centrality);
      ( "ablation_prob",
        if spec.count <> None then `Null else `Float spec.prob );
      ("min_depth", `Int spec.min_depth);
      ("max_depth", depth_json spec.max_depth);
      ("leaves_only", `Bool spec.leaves_only);
      ("min_size", `Int spec.min_size);
      ("max_size", depth_json spec.max_size);
      ("min_centrality", `Int spec.min_centrality);
      ("max_centrality", depth_json spec.max_centrality);
      ("seed", `Int seed);
      ("n_proofs", `Int result.total);
      ("n_ablated", `Int result.ablated);
      ("holes_filled", `List (List.map hole_json result.holes));
      ( "deleted_lemmas",
        `List
          (List.map
             (fun (nm, txt) -> `Assoc [ ("name", `String nm); ("text", `String txt) ])
             result.deleted) );
      ("challenge_file_content", `String result.text);
      (* the solution is stored as a diff against the challenge (apply to recover
         the full solution) — full files are huge for big theories (issue #107) *)
      ("solution_diff", `String (Diff.unified result.text result.solution));
      ("solution_file_content", `String result.solution);
    ]
