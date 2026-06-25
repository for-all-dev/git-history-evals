(* Native-only compile-test for the CLI's --check-build. NOT part of the library
   (and so never compiled into the WASM core): it shells out to `coqc`.

   Strategy — build only the *upward closure*. `coqc F.v` compiles F alone and
   never its dependents, so it is inherently the upward-closure check: dropping
   trailing decls via --shrink can't break it (declare-before-use), and any
   dependent theory that referenced a dropped name is simply never built. F's own
   dependencies must already be compiled (their .vo present) — the normal state
   after a project build.

   coqc flags come from the nearest enclosing `_CoqProject` (the `-R`/`-Q`/`-I`
   load-path mappings), found by walking up from the file. *)

let read_file p =
  let ic = open_in_bin p in
  Fun.protect ~finally:(fun () -> close_in ic) (fun () ->
      really_input_string ic (in_channel_length ic))

let write_file p s =
  let oc = open_out_bin p in
  Fun.protect ~finally:(fun () -> close_out oc) (fun () -> output_string oc s)

(* parse a _CoqProject: keep the load-path mappings and flags, drop .v listings
   and comments. Tokens are whitespace-separated (good enough for -R/-Q/-I). *)
let parse_coqproject (path : string) : string list =
  let text = read_file path in
  let lines = String.split_on_char '\n' text in
  let toks =
    List.concat_map
      (fun line ->
        let line = match String.index_opt line '#' with Some i -> String.sub line 0 i | None -> line in
        String.split_on_char ' ' line
        |> List.concat_map (String.split_on_char '\t')
        |> List.filter (fun s -> s <> ""))
      lines
  in
  (* keep -R DIR NAME / -Q DIR NAME / -I DIR / -arg FLAG; drop bare *.v files *)
  let rec go acc = function
    | "-R" :: d :: n :: rest -> go (n :: d :: "-R" :: acc) rest
    | "-Q" :: d :: n :: rest -> go (n :: d :: "-Q" :: acc) rest
    | "-I" :: d :: rest -> go (d :: "-I" :: acc) rest
    | "-arg" :: a :: rest -> go (a :: acc) rest
    | tok :: rest when String.length tok > 0 && tok.[0] = '-' -> go (tok :: acc) rest
    | _ :: rest -> go acc rest (* a .v file or stray token *)
    | [] -> List.rev acc
  in
  go [] toks

(* find the nearest _CoqProject walking up; return (project_dir, coqc_args). *)
let find_project (file : string) : string * string list =
  let rec up dir =
    let cand = Filename.concat dir "_CoqProject" in
    if Sys.file_exists cand then (dir, parse_coqproject cand)
    else
      let parent = Filename.dirname dir in
      if parent = dir then (Filename.dirname file, []) else up parent
  in
  up (Filename.dirname (if Filename.is_relative file then Filename.concat (Sys.getcwd ()) file else file))

let quote = Filename.quote

(* compile [file] with the project flags; true iff coqc exits 0. *)
let coqc proj_dir args file : bool =
  let cmd =
    String.concat " "
      ([ "cd"; quote proj_dir; "&&"; "coqc" ] @ List.map quote args @ [ quote file ])
  in
  Sys.command (cmd ^ " >/dev/null 2>&1") = 0

(* put [content] at [file], compile it, restore the original. Returns whether it
   compiled. The produced .vo reflects [content]; callers should re-build the
   project afterwards if they need the original artifacts back. *)
let check_compiles (file : string) (content : string) : bool =
  let abs = if Filename.is_relative file then Filename.concat (Sys.getcwd ()) file else file in
  let orig = read_file abs in
  Fun.protect
    ~finally:(fun () -> write_file abs orig)
    (fun () ->
      write_file abs content;
      let dir, args = find_project abs in
      coqc dir args abs)
