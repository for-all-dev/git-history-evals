{
  description = "rocq-ablator: OCaml syntactic proof ablator for Coq/Rocq (CLI + WASM)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
      mkShell = pkgs:
        let op = pkgs.ocamlPackages; in
        pkgs.mkShell {
          packages = [
            pkgs.dune_3
            op.ocaml
            op.findlib
            op.yojson
            op.js_of_ocaml
            op.js_of_ocaml-compiler
            op.wasm_of_ocaml-compiler # provides the wasm_of_ocaml binary
            pkgs.binaryen # wasm-opt, used by wasm_of_ocaml
            pkgs.python3 # serve the demo
            pkgs.nodejs # run/test the wasm output
          ];
          shellHook = ''
            echo "rocq-ablator dev shell — $(ocaml -version)"
            echo "  CLI:   dune build && dune exec bin/main.exe -- --help"
            echo "  tests: dune test"
            echo "  wasm:  ./build-wasm.sh   then  python3 -m http.server -d web 8000"
          '';
        };
    in
    {
      devShells = forAll (pkgs: {
        default = mkShell pkgs;
      });
    };
}
