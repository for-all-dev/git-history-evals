/-
Minimal SHA-1 (lowercase hex) for deriving stable `task_id`s. No crypto
dependency — keeps the core small and WASM-able.
-/

namespace Ablator
namespace Sha1

private def rotl (x : UInt32) (n : UInt32) : UInt32 :=
  (x <<< n) ||| (x >>> (32 - n))

private def mask32 : UInt32 := 0xFFFFFFFF

private def hexWord (w : UInt32) : String :=
  let digits := "0123456789abcdef"
  let nib (shift : UInt32) : Char :=
    let v := ((w >>> shift) &&& 0xF).toNat
    digits.toList[v]!
  String.mk [nib 28, nib 24, nib 20, nib 16, nib 12, nib 8, nib 4, nib 0]

/-- Lowercase hex SHA-1 digest of `data`. -/
def hex (data : ByteArray) : String := Id.run do
  let mut h0 : UInt32 := 0x67452301
  let mut h1 : UInt32 := 0xEFCDAB89
  let mut h2 : UInt32 := 0x98BADCFE
  let mut h3 : UInt32 := 0x10325476
  let mut h4 : UInt32 := 0xC3D2E1F0
  let ml : UInt64 := (data.size.toUInt64) * 8
  -- pad
  let mut msg := data.push 0x80
  while msg.size % 64 != 56 do
    msg := msg.push 0
  -- big-endian 64-bit length
  for i in [0:8] do
    msg := msg.push (((ml >>> (UInt64.ofNat ((7 - i) * 8))) &&& 0xFF).toUInt8)
  let nchunks := msg.size / 64
  for ci in [0:nchunks] do
    let base := ci * 64
    let mut w : Array UInt32 := Array.mkArray 80 0
    for i in [0:16] do
      let b0 := (msg.get! (base + i*4 + 0)).toUInt32
      let b1 := (msg.get! (base + i*4 + 1)).toUInt32
      let b2 := (msg.get! (base + i*4 + 2)).toUInt32
      let b3 := (msg.get! (base + i*4 + 3)).toUInt32
      w := w.set! i ((b0 <<< 24) ||| (b1 <<< 16) ||| (b2 <<< 8) ||| b3)
    for i in [16:80] do
      let v := (w[i-3]! ^^^ w[i-8]! ^^^ w[i-14]! ^^^ w[i-16]!)
      w := w.set! i (rotl v 1)
    let mut a := h0
    let mut b := h1
    let mut c := h2
    let mut d := h3
    let mut e := h4
    for i in [0:80] do
      let wi := w[i]!
      let (f, k) : UInt32 × UInt32 :=
        if i < 20 then ((b &&& c) ||| ((mask32 ^^^ b) &&& d), 0x5A827999)
        else if i < 40 then (b ^^^ c ^^^ d, 0x6ED9EBA1)
        else if i < 60 then ((b &&& c) ||| (b &&& d) ||| (c &&& d), 0x8F1BBCDC)
        else (b ^^^ c ^^^ d, 0xCA62C1D6)
      let tmp := (rotl a 5) + f + e + k + wi
      e := d
      d := c
      c := rotl b 30
      b := a
      a := tmp
    h0 := h0 + a
    h1 := h1 + b
    h2 := h2 + c
    h3 := h3 + d
    h4 := h4 + e
  return hexWord h0 ++ hexWord h1 ++ hexWord h2 ++ hexWord h3 ++ hexWord h4

end Sha1

/-- `task_id` digest: first 12 hex chars of SHA-1 of the UTF-8 path. -/
def sha1Hex12 (s : String) : String :=
  String.mk ((Sha1.hex s.toUTF8).toList.take 12)

end Ablator
