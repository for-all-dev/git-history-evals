(* A tiny self-rolled line differ — no external dependency, so it compiles into
   the WASM core. We emit `solution_diff` (a unified-diff that turns the
   challenge into the solution) instead of the whole solution file, because
   storing the full file per row is huge (l4v theories are 100k+ chars). See
   issue #107.

   Convention (so producer and consumer agree exactly): lines are
   `String.split_on_char '\n'` (a trailing newline yields a final "" element),
   and [apply] joins them back with '\n', so the round-trip is byte-exact. The
   hunk syntax is the usual `@@ -oldStart,oldLen +newStart,newLen @@` with ` `/
   `-`/`+` line prefixes; an empty diff (challenge = solution) is "".

   Algorithm: Myers O(ND) greedy diff, capped at [d_max] edits; beyond the cap
   (challenge and solution wildly different) we fall back to a single hunk
   replacing the differing middle — always correct, never larger than the file. *)

let d_max = 2000
let context = 3

type op = Eq of string | Del of string | Ins of string

let split_lines (s : string) : string array = Array.of_list (String.split_on_char '\n' s)

(* Myers greedy diff; returns the op list, or None if the edit distance exceeds
   [d_max]. *)
let myers (a : string array) (b : string array) : op list option =
  let n = Array.length a and m = Array.length b in
  let max_d = n + m in
  if max_d = 0 then Some []
  else begin
    let off = max_d in
    let v = Array.make ((2 * max_d) + 1) 0 in
    let trace = ref [] in
    let found = ref false in
    let d = ref 0 in
    (try
       while !d <= max_d && !d <= d_max do
         trace := Array.copy v :: !trace;
         let k = ref (- !d) in
         while !k <= !d do
           let kk = !k in
           let x =
             if kk = - !d || (kk <> !d && v.(off + kk - 1) < v.(off + kk + 1)) then
               v.(off + kk + 1)
             else v.(off + kk - 1) + 1
           in
           let x = ref x in
           let y = ref (!x - kk) in
           while !x < n && !y < m && a.(!x) = b.(!y) do
             incr x;
             incr y
           done;
           v.(off + kk) <- !x;
           if !x >= n && !y >= m then begin
             found := true;
             raise Exit
           end;
           k := !k + 2
         done;
         incr d
       done
     with Exit -> ());
    if not !found then None
    else begin
      let trace = Array.of_list (List.rev !trace) in
      (* trace.(i) = V at the start of iteration i; we found during iteration !d *)
      let ops = ref [] in
      let x = ref n and y = ref m in
      let dd = ref !d in
      while !dd >= 0 do
        let vprev = trace.(!dd) in
        let k = !x - !y in
        let prev_k =
          if k = - !dd || (k <> !dd && vprev.(off + k - 1) < vprev.(off + k + 1)) then k + 1
          else k - 1
        in
        let prev_x = vprev.(off + prev_k) in
        let prev_y = prev_x - prev_k in
        while !x > prev_x && !y > prev_y do
          ops := Eq a.(!x - 1) :: !ops;
          decr x;
          decr y
        done;
        if !dd > 0 then
          if !x = prev_x then begin
            ops := Ins b.(!y - 1) :: !ops;
            decr y
          end
          else begin
            ops := Del a.(!x - 1) :: !ops;
            decr x
          end;
        decr dd
      done;
      Some !ops
    end
  end

(* Format an op list as unified-diff hunks (context lines around each change). *)
let format_hunks (ops : op list) : string =
  let ops = Array.of_list ops in
  let len = Array.length ops in
  let is_change = function Eq _ -> false | _ -> true in
  let buf = Buffer.create 256 in
  (* old/new line number (1-based) of ops.(i)'s first consumed line *)
  let old_no = Array.make (len + 1) 1 and new_no = Array.make (len + 1) 1 in
  for i = 0 to len - 1 do
    let o, nw =
      match ops.(i) with
      | Eq _ -> (1, 1)
      | Del _ -> (1, 0)
      | Ins _ -> (0, 1)
    in
    old_no.(i + 1) <- old_no.(i) + o;
    new_no.(i + 1) <- new_no.(i) + nw
  done;
  let i = ref 0 in
  while !i < len do
    if is_change ops.(!i) then begin
      (* hunk start: back up to [context] preceding Eq lines *)
      let s = ref !i in
      let back = ref context in
      while !s > 0 && (not (is_change ops.(!s - 1))) && !back > 0 do
        decr s;
        decr back
      done;
      (* extend to the last change, absorbing gaps of <= 2*context Eq lines *)
      let e = ref !i in
      let last_change = ref !i in
      let continue = ref true in
      while !continue do
        if !e + 1 >= len then continue := false
        else if is_change ops.(!e + 1) then begin
          incr e;
          last_change := !e
        end
        else begin
          (* count the run of Eq lines starting at e+1 *)
          let run = ref 0 in
          while !e + 1 + !run < len && not (is_change ops.(!e + 1 + !run)) do
            incr run
          done;
          let next_change = !e + 1 + !run in
          if next_change < len && !run <= 2 * context then begin
            e := next_change;
            last_change := next_change
          end
          else continue := false
        end
      done;
      (* trailing context: up to [context] Eq lines after the last change *)
      let stop = ref !last_change in
      let fwd = ref context in
      while !stop + 1 < len && (not (is_change ops.(!stop + 1))) && !fwd > 0 do
        incr stop;
        decr fwd
      done;
      (* emit hunk [s .. stop] *)
      let ol = old_no.(!stop + 1) - old_no.(!s) in
      let nl = new_no.(!stop + 1) - new_no.(!s) in
      Buffer.add_string buf
        (Printf.sprintf "@@ -%d,%d +%d,%d @@\n" old_no.(!s) ol new_no.(!s) nl);
      for j = !s to !stop do
        (match ops.(j) with
         | Eq l -> Buffer.add_char buf ' '; Buffer.add_string buf l
         | Del l -> Buffer.add_char buf '-'; Buffer.add_string buf l
         | Ins l -> Buffer.add_char buf '+'; Buffer.add_string buf l);
        Buffer.add_char buf '\n'
      done;
      i := !stop + 1
    end
    else incr i
  done;
  Buffer.contents buf

(* Fallback when Myers exceeds the cap: one hunk replacing the differing middle
   (common prefix/suffix kept). *)
let fallback (a : string array) (b : string array) : string =
  let n = Array.length a and m = Array.length b in
  let p = ref 0 in
  while !p < n && !p < m && a.(!p) = b.(!p) do
    incr p
  done;
  let s = ref 0 in
  while !s < n - !p && !s < m - !p && a.(n - 1 - !s) = b.(m - 1 - !s) do
    incr s
  done;
  let ol = n - !p - !s and nl = m - !p - !s in
  if ol = 0 && nl = 0 then ""
  else begin
    let buf = Buffer.create 256 in
    Buffer.add_string buf (Printf.sprintf "@@ -%d,%d +%d,%d @@\n" (!p + 1) ol (!p + 1) nl);
    for j = !p to n - 1 - !s do
      Buffer.add_char buf '-';
      Buffer.add_string buf a.(j);
      Buffer.add_char buf '\n'
    done;
    for j = !p to m - 1 - !s do
      Buffer.add_char buf '+';
      Buffer.add_string buf b.(j);
      Buffer.add_char buf '\n'
    done;
    Buffer.contents buf
  end

(* unified diff turning [challenge] into [solution]; "" if identical. *)
let unified (challenge : string) (solution : string) : string =
  if challenge = solution then ""
  else
    let a = split_lines challenge and b = split_lines solution in
    match myers a b with Some ops -> format_hunks ops | None -> fallback a b

(* apply a [unified] diff to [challenge], recovering the solution. *)
let apply (challenge : string) (diff : string) : string =
  if diff = "" then challenge
  else begin
    let a = split_lines challenge in
    let out = ref [] in
    let oi = ref 0 in
    (* current index into a (0-based) *)
    let lines = String.split_on_char '\n' diff in
    (* a "@@ -os,ol +ns,nl @@" header gives the old start; copy unchanged a-lines
       up to it, then process body lines *)
    let parse_old_start h =
      (* h = "@@ -os,ol +ns,nl @@" *)
      let dash = String.index h '-' in
      let comma = String.index_from h dash ',' in
      int_of_string (String.sub h (dash + 1) (comma - dash - 1))
    in
    List.iter
      (fun line ->
        if String.length line >= 2 && line.[0] = '@' && line.[1] = '@' then begin
          let os = parse_old_start line in
          while !oi < os - 1 do
            out := a.(!oi) :: !out;
            incr oi
          done
        end
        else if line = "" then () (* trailing split artifact between hunks *)
        else
          match line.[0] with
          | ' ' -> out := String.sub line 1 (String.length line - 1) :: !out; incr oi
          | '-' -> incr oi
          | '+' -> out := String.sub line 1 (String.length line - 1) :: !out
          | _ -> ())
      lines;
    while !oi < Array.length a do
      out := a.(!oi) :: !out;
      incr oi
    done;
    String.concat "\n" (List.rev !out)
  end
