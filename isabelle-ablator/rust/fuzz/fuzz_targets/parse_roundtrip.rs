//! Fuzz the parser on arbitrary UTF-8: the tokenizer/`parse_spans` must
//! (1) never panic and (2) round-trip losslessly (concatenating every span's
//! source reproduces the input). Then ablation over the parsed spans must not
//! panic either, across the full depth range and the context-shaping paths.
//!
//! Run (cargo-fuzz needs the nightly toolchain for the sanitizer):
//!   nix shell nixpkgs#cargo-fuzz -c \
//!     env RUSTUP_TOOLCHAIN=nightly cargo fuzz run parse_roundtrip -- -max_total_time=60

#![no_main]

use isabelle_ablator::ablate::{ablate, Rng, Spec, INF};
use isabelle_ablator::span::Syntax;
use libfuzzer_sys::fuzz_target;
use std::sync::OnceLock;

static SYN: OnceLock<Syntax> = OnceLock::new();

fuzz_target!(|data: &[u8]| {
    let Ok(s) = std::str::from_utf8(data) else { return };
    let syn = SYN.get_or_init(Syntax::hol);

    let spans = syn.parse_spans(s);

    // (1) lossless round-trip — the core invariant
    let rebuilt: String = spans.iter().map(|sp| sp.source()).collect();
    assert_eq!(rebuilt, s, "parse_spans must round-trip byte-for-byte");

    // (2) ablation must not panic on arbitrary input. Exercise the recursive
    //     depth walk (max_depth inf) and the context-shaping paths.
    let z = |_: &str| 0i64;
    let mut rng = Rng::new(0);
    let _ = ablate(
        &spans,
        &Spec { prob: 1.0, max_depth: INF, ..Default::default() },
        &mut rng,
        &z,
    );
    let mut rng = Rng::new(1);
    let _ = ablate(
        &spans,
        &Spec { prob: 1.0, max_depth: INF, truncate: true, shrink_context: true, ..Default::default() },
        &mut rng,
        &z,
    );
});
