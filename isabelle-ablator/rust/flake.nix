{
  description = "isabelle-ablator (Rust/WASM): dev shell — cargo (CLI) + wasm-pack (browser demo)";

  # The Rust toolchain itself comes from rustup (so the wasm32 target and a
  # nightly for cargo-fuzz are one `rustup` command away). This flake provides
  # rustup + wasm-pack + the WASM tooling used by build-wasm.sh.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      # nix develop -c ./build-wasm.sh   (or just `nix develop`, then cargo ...)
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.rustup # cargo / rustc + the wasm32-unknown-unknown target
            pkgs.wasm-pack # drives the browser-demo build (build-wasm.sh)
            pkgs.binaryen # wasm-opt, used by wasm-pack
            pkgs.python3 # serve the demo
          ];
          shellHook = ''
            rustup default stable >/dev/null 2>&1 || true
            rustup target add wasm32-unknown-unknown >/dev/null 2>&1 || true
            echo "isabelle-ablator (rust) dev shell — $(rustc --version 2>/dev/null || echo 'rustup: run rustup default stable')"
            echo "  CLI:  cargo build --release && ./target/release/ablate --help"
            echo "  wasm: ./build-wasm.sh      serve: python3 -m http.server -d web 8000"
          '';
        };
      });
    };
}
