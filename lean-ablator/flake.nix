{
  description = "lean-ablator: dev shell — lean toolchain (via elan) + emscripten";

  # `elan` provides the `lean`/`lake` shims, which honor `lean-toolchain`
  # (pinned to v4.15.0 — the last release with a prebuilt wasm32 runtime) and
  # fetch that toolchain on first use. The flake also ships the emscripten
  # cross-toolchain + fetch/unpack tools used by build-wasm.sh, so nothing has to
  # be pre-installed on the host.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      # nix develop -c ./build-wasm.sh   (or just `nix develop`, then lake build)
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.elan # lean + lake (honors lean-toolchain: v4.15.0)
            pkgs.emscripten # emcc, for the wasm link step
            pkgs.zstd # unzstd, to unpack the prebuilt wasm32 runtime
            pkgs.curl # download the runtime release asset
            pkgs.python3 # serve the demo
          ];
          shellHook = ''
            echo "lean-ablator dev shell — $(emcc --version | head -1)"
            echo "build:  ./build-wasm.sh      serve:  python3 -m http.server -d web 8000"
          '';
        };
      });
    };
}
