(* Context shaping (truncate / shrink_context), ported from the Isabelle
   ablator's [ablate.rs]. [segs] are (byte-offset end in the output, is_goal,
   had_admit) triples, one per top-level segment. *)

(* collapse runs of >=2 blank lines into a single blank line *)
let collapse_blank_lines (s : string) : string =
  let n = String.length s in
  let out = Buffer.create n in
  let i = ref 0 in
  while !i < n do
    if s.[!i] = '\n' then begin
      let j = ref (!i + 1) in
      let groups = ref 0 in
      let cont = ref true in
      while !cont do
        let k = ref !j in
        while !k < n && (s.[!k] = ' ' || s.[!k] = '\t') do
          incr k
        done;
        if !k < n && s.[!k] = '\n' then begin
          j := !k + 1;
          incr groups
        end
        else cont := false
      done;
      if !groups >= 2 then begin
        Buffer.add_string out "\n\n";
        i := !j
      end
      else begin
        Buffer.add_char out '\n';
        incr i
      end
    end
    else begin
      Buffer.add_char out s.[!i];
      incr i
    end
  done;
  Buffer.contents out

(* drop top-level goal segments after the last ablated one, then tidy blanks *)
let shrink_context (full : string) (segs : (int * bool * bool) array) : string =
  let last = ref (-1) in
  Array.iteri (fun idx (_, is_goal, had) -> if is_goal && had then last := idx) segs;
  if !last < 0 then full
  else begin
    let out = Buffer.create (String.length full) in
    let prev = ref 0 in
    Array.iteri
      (fun idx (endo, is_goal, _) ->
        if idx <= !last || not is_goal then
          Buffer.add_substring out full !prev (endo - !prev);
        prev := endo)
      segs;
    collapse_blank_lines (Buffer.contents out)
  end
