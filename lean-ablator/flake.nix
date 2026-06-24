{
  description = "lean-ablator: WASM build shell (emscripten + tools)";

  # The Lean toolchain itself comes from elan (`lean-toolchain` pins v4.15.0,
  # the last release with a prebuilt wasm32 runtime). This flake only provides
  # the emscripten cross-toolchain used by build-wasm.sh.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
      mkWasmShell = pkgs: pkgs.mkShell {
        packages = [ pkgs.emscripten pkgs.zstd pkgs.curl pkgs.python3 ];
        shellHook = ''
          echo "lean-ablator wasm shell — $(emcc --version | head -1)"
          echo "build:  ./build-wasm.sh      serve:  python3 -m http.server -d web 8000"
        '';
      };
    in {
      # nix develop .#wasm -c ./build-wasm.sh   (or just `nix develop`)
      devShells = forAll (pkgs: {
        wasm = mkWasmShell pkgs;
        default = mkWasmShell pkgs;
      });
    };
}
