(* WASM / JS entry point. Compiled with wasm_of_ocaml (see build-wasm.sh), this
   exposes a single function so a browser playground can ablate a Coq theory
   client-side. It is pure (no IO), computing centrality over a corpus-of-one.

   The difficulty knobs arrive as a JSON object (same contract as the Isabelle
   ablator's wasm.rs ablate_theory(text, opts_json, seed)). *)

open Js_of_ocaml
open Rocq_ablator
module U = Yojson.Safe.Util

let as_int_inf (v : Yojson.Safe.t) (default : int) : int =
  match v with
  | `Int n -> n
  | `Float f -> int_of_float f
  | `Intlit s | `String s ->
      if s = "inf" || s = "infinity" then Ablate.inf
      else ( match int_of_string_opt s with Some n -> n | None -> default )
  | _ -> default

let as_bool (v : Yojson.Safe.t) (default : bool) : bool =
  match v with `Bool b -> b | _ -> default

let spec_from (opts : Yojson.Safe.t) : Ablate.spec =
  let g k = try U.member k opts with _ -> `Null in
  let difficulty = match g "difficulty" with `String s -> Some s | _ -> None in
  let preset = match difficulty with Some d -> Ablate.preset_of d | None -> None in
  let pget f d = match preset with Some p -> f p | None -> d in
  let depth_knob key pf d =
    let v = g key in
    if v = `Null then pget pf d else as_int_inf v d
  in
  let count = match g "count" with `Int n -> Some n | `Float f -> Some (int_of_float f) | _ -> None in
  let prob =
    match g "prob" with
    | `Float f -> f
    | `Int n -> float_of_int n
    | _ -> pget (fun p -> p.Ablate.p_prob) 0.5
  in
  let leaves =
    let v = g "leaves_only" in
    if v = `Null then pget (fun p -> p.Ablate.p_leaves) false else as_bool v false
  in
  Ablate.
    {
      prob;
      count;
      by_centrality = as_bool (g "by_centrality") false;
      min_depth = depth_knob "min_depth" (fun p -> p.p_min) 1;
      max_depth = depth_knob "max_depth" (fun p -> p.p_max) 1;
      leaves_only = leaves;
      min_size = as_int_inf (g "min_size") 0;
      max_size = as_int_inf (g "max_size") inf;
      min_centrality = as_int_inf (g "min_centrality") 0;
      max_centrality = as_int_inf (g "max_centrality") inf;
      truncate = as_bool (g "truncate") false;
      shrink_challenge = as_bool (g "shrink_challenge") false;
      shrink_solution = as_bool (g "shrink_solution") false;
      shrink_challenge_minimal = as_bool (g "shrink_challenge_minimal") false;
      shrink_solution_minimal = as_bool (g "shrink_solution_minimal") false;
      allow_defined = as_bool (g "allow_defined") false;
      delete_lemmas = as_bool (g "delete_lemmas") false;
      delete_count = (match g "delete_count" with `Int k when k >= 0 -> Some k | _ -> None);
      delete_uniform = as_bool (g "delete_uniform") false;
      delete_leaves = as_bool (g "delete_leaves") false;
      aggressive = false (* the prover-backed path is never exposed to the browser *);
      corollary = as_bool (g "corollary") false;
      corollary_all = as_bool (g "corollary_all") false;
      forced_corollary = None;
    }

(* Ablate one theory. Returns a single {text,...,holes,deleted_lemmas,corollaries} JSON
   object, EXCEPT under [corollary_all] where it returns a JSON ARRAY (one per eligible
   corollary) so the playground can page through every corollary's ablation. *)
let ablate_theory (text : string) (opts_json : string) (seed : float) : string =
  let opts = try Yojson.Safe.from_string opts_json with _ -> `Null in
  let spec = spec_from opts in
  let spans = Span.parse_spans text in
  let fan = if Ablate.uses_centrality spec then Centrality.fan_in [ text ] else Hashtbl.create 1 in
  let centrality name = match Hashtbl.find_opt fan name with Some c -> c | None -> 0 in
  let rng = Ablate.Rng.make (Int64.bits_of_float seed) in
  if spec.Ablate.corollary_all then
    let results = Ablate.ablate_all spans spec rng centrality in
    Yojson.Safe.to_string (`List (List.map Record.result_json results))
  else
    Yojson.Safe.to_string (Record.result_json (Ablate.ablate spans spec rng centrality))

let () =
  (* Take args as explicit JS types and convert: under wasm_of_ocaml (WasmGC)
     OCaml [float] is a distinct boxed type, so a JS number must be converted
     with [Js.float_of_number] rather than crossing the FFI as a bare float. *)
  let f =
    Js.wrap_callback
      (fun (text : Js.js_string Js.t) (opts : Js.js_string Js.t)
           (seed : Js.number Js.t) ->
        Js.string
          (ablate_theory (Js.to_string text) (Js.to_string opts)
             (Js.float_of_number seed)))
  in
  Js.export "rocqAblate" f;
  (* also publish on globalThis so the page can poll for readiness *)
  Js.Unsafe.set (Js.Unsafe.pure_js_expr "globalThis") (Js.string "rocqAblate") f
