(* Context shaping, ported from the Isabelle ablator's [ablate.rs]. [segs] are
   (byte-offset end in the text, is_closer, had_ablation) triples, one per
   top-level segment. The same operation shrinks either the challenge or the
   solution — only the offsets differ. Everything after the last ablated segment
   is dropped EXCEPT structural closers ([End]/[end]), so the enclosing module /
   section / theory still ends and the result stays well-formed. *)

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

(* drop all top-level segments after the cut point (keeping structural closers so
   blocks still close), then tidy blanks. The cut is normally the last ablated
   segment; with [~count:n] it is the n-th ablated segment instead, so the result
   keeps exactly the first [n] holes (the rest of the file is dropped). *)
let shrink ?count (full : string) (segs : (int * bool * bool) array) : string =
  let last = ref (-1) and seen = ref 0 in
  Array.iteri
    (fun idx (_, _, had) ->
      if had then begin
        incr seen;
        match count with
        | Some n when !seen > n -> () (* past the n-th hole: don't extend the cut *)
        | _ -> last := idx
      end)
    segs;
  if !last < 0 then full
  else begin
    let out = Buffer.create (String.length full) in
    let prev = ref 0 in
    let gap = ref false in
    (* dropped a segment since the last kept one *)
    Array.iteri
      (fun idx (endo, is_closer, _) ->
        (if idx <= !last || is_closer then begin
           (* a kept closer after a gap (e.g. `End M.`) must not glue onto the
              previous token — ensure a newline separates them. *)
           if !gap && Buffer.length out > 0 && Buffer.nth out (Buffer.length out - 1) <> '\n'
           then Buffer.add_char out '\n';
           Buffer.add_substring out full !prev (endo - !prev);
           gap := false
         end
         else gap := true);
        prev := endo)
      segs;
    collapse_blank_lines (Buffer.contents out)
  end
