(* Minimal SHA-1 (lowercase hex), matching the Isabelle/Lean ablators' task_id
   derivation byte-for-byte without pulling in a crypto dependency (keeps the
   WASM build small). Uses [Int32] so 32-bit word arithmetic is correct and
   warning-free on every target (native 63-bit int *and* the 32-bit int that
   js_of_ocaml / wasm_of_ocaml use). *)

open Int32

let rotl x n = logor (shift_left x n) (shift_right_logical x (32 - n))

let hex (data : string) : string =
  let h = [| 0x67452301l; 0xEFCDAB89l; 0x98BADCFEl; 0x10325476l; 0xC3D2E1F0l |] in
  let ml = String.length data * 8 in
  let msg = Buffer.create (String.length data + 72) in
  Buffer.add_string msg data;
  Buffer.add_char msg '\x80';
  while Buffer.length msg mod 64 <> 56 do
    Buffer.add_char msg '\x00'
  done;
  for i = 7 downto 0 do
    Buffer.add_char msg (Char.chr ((ml lsr (i * 8)) land 0xFF))
  done;
  let m = Buffer.contents msg in
  let nblocks = String.length m / 64 in
  let b32 c = of_int (Char.code c) in
  for blk = 0 to nblocks - 1 do
    let base = blk * 64 in
    let w = Array.make 80 0l in
    for i = 0 to 15 do
      let o = base + (i * 4) in
      w.(i) <-
        logor
          (logor (shift_left (b32 m.[o]) 24) (shift_left (b32 m.[o + 1]) 16))
          (logor (shift_left (b32 m.[o + 2]) 8) (b32 m.[o + 3]))
    done;
    for i = 16 to 79 do
      w.(i) <- rotl (logxor (logxor w.(i - 3) w.(i - 8)) (logxor w.(i - 14) w.(i - 16))) 1
    done;
    let a = ref h.(0) and b = ref h.(1) and c = ref h.(2) and d = ref h.(3) and e = ref h.(4) in
    for i = 0 to 79 do
      let f, k =
        if i <= 19 then (logor (logand !b !c) (logand (lognot !b) !d), 0x5A827999l)
        else if i <= 39 then (logxor (logxor !b !c) !d, 0x6ED9EBA1l)
        else if i <= 59 then
          (logor (logor (logand !b !c) (logand !b !d)) (logand !c !d), 0x8F1BBCDCl)
        else (logxor (logxor !b !c) !d, 0xCA62C1D6l)
      in
      let tmp = add (add (add (add (rotl !a 5) f) !e) k) w.(i) in
      e := !d;
      d := !c;
      c := rotl !b 30;
      b := !a;
      a := tmp
    done;
    h.(0) <- add h.(0) !a;
    h.(1) <- add h.(1) !b;
    h.(2) <- add h.(2) !c;
    h.(3) <- add h.(3) !d;
    h.(4) <- add h.(4) !e
  done;
  let buf = Buffer.create 40 in
  Array.iter (fun x -> Buffer.add_string buf (Printf.sprintf "%08lx" x)) h;
  Buffer.contents buf
