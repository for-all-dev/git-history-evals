{
  description = "git-history-evals: the Lean ablation pipeline (mine -> dry-run validate -> publish)";

  # Why this flake exists: validating an ablation means *really compiling* the challenge
  # against each source repo's own build, and getting that environment right turned out to
  # be the whole ballgame — a broken environment reports every challenge as `malformed`,
  # which reads as bad data rather than a bad toolchain. The fixes below were discovered the
  # hard way; they belong in the shell, not in a shell history.
  #
  # Python: uv2nix reads `baselines/uv.lock` and builds every dependency as a nix
  # derivation, so `ablate-baseline` is an ordinary binary in a nix-built venv — no `uv run`
  # at runtime, no network, no LD_LIBRARY_PATH hacks (wheels get autoPatchelf'd by
  # pyproject-nix). `nix run .#ablate-baseline` and `nix build .#baselines-env` just work.
  # The uv workflow still exists for iterating on deps: edit pyproject, `uv lock`, and nix
  # picks the new lock up on the next build.

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
        nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs = { self, nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});

      # ---- the ./baselines uv workspace, straight from uv.lock -------------------------
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./baselines; };

      # Prefer binary wheels: pydantic-core/scikit-learn/joblib ship compiled artifacts and
      # building them from sdist would drag in a Rust/Fortran toolchain for no gain.
      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };
    in
    {
      packages = forAll (pkgs:
        let
          python = pkgs.python312;
          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
              (nixpkgs.lib.composeManyExtensions [
                pyproject-build-systems.overlays.default
                overlay
              ]);
          # the whole dependency closure + the apply_ablate package itself, as one venv
          baselinesEnv = pythonSet.mkVirtualEnv "apply-ablate-env" workspace.deps.default;
        in
        {
          baselines-env = baselinesEnv;
          default = baselinesEnv;

          # `nix build .#workshop-paper` -> result/neurips_2026_vericode_workshop.pdf
          # The system TeX on the dev box lacks the Times metrics (ptmr8t) and environ.sty
          # that neurips_2026_vericode.sty needs; scheme-medium + environ is the minimal
          # set that compiles clean. Three passes, same as CI would run; figures are not
          # \includegraphics'd yet — when they are, copy comms/vericode-workshop/figures/out
          # into the build (they regenerate via `uv run figures` in that dir, see its README).
          workshop-paper =
            let
              tex = pkgs.texliveMedium.withPackages (ps: [ ps.environ ]);
            in
            pkgs.stdenvNoCC.mkDerivation {
              pname = "vericode-workshop-paper";
              version = "draft";
              src = ./comms/vericode-workshop;
              nativeBuildInputs = [ tex ];
              buildPhase = ''
                export HOME=$TMPDIR SOURCE_DATE_EPOCH=0
                for pass in 1 2 3; do
                  pdflatex -interaction=nonstopmode -halt-on-error neurips_2026_vericode_workshop.tex
                done
              '';
              installPhase = ''
                mkdir -p $out
                cp neurips_2026_vericode_workshop.pdf $out/
              '';
            };
        });

      # `nix run .#ablate-baseline -- <challenges.jsonl> <src> --dry-run`
      apps = forAll (pkgs:
        let env = self.packages.${pkgs.system}.baselines-env; in {
          ablate-baseline = { type = "app"; program = "${env}/bin/ablate-baseline"; };
          apply-ablate = { type = "app"; program = "${env}/bin/apply-ablate"; };
          difficulty = { type = "app"; program = "${env}/bin/difficulty"; };
        });

      devShells = forAll (pkgs:
        let
          # FIX 1 — the `cc` wrapper.
          # Some Lean repos ship C FFI that only compiles with GNU extensions visible. hex-dev's
          # `HexLLL/ffi/lean_hexlll_provider.c` calls `dlsym(RTLD_DEFAULT, ...)`, which glibc only
          # declares under _GNU_SOURCE. Without this the FFI target fails, every module depending
          # on it goes unbuilt, and every challenge in those files fails with `object file ... does
          # not exist` — 3,323 of hex-dev's 3,821 challenges were reported `malformed` for want of
          # this one flag. Defining it globally is safe on Linux.
          ccGnuSource = pkgs.writeShellScriptBin "cc" ''
            exec ${pkgs.stdenv.cc}/bin/cc -D_GNU_SOURCE "$@"
          '';
          baselinesEnv = self.packages.${pkgs.system}.baselines-env;
        in
        {
          # Run the pipeline with NO uv at runtime: ablate-baseline comes from the nix venv.
          default = pkgs.mkShell {
            packages = [
              ccGnuSource # must precede stdenv's cc on PATH — see FIX 1
              baselinesEnv # ablate-baseline / apply-ablate / difficulty, deps baked in

              pkgs.elan # lean/lake; honours each repo's lean-toolchain and fetches it
              pkgs.python3 # pipeline scripts (keep_good.py, finalize_mode.py, ...)
              pkgs.s3cmd # publish to the DigitalOcean Space
              pkgs.git
              pkgs.curl
              pkgs.jq
              pkgs.ripgrep # the baselines agent's `search` tool shells out to `rg`
              pkgs.gnumake
            ];
            shellHook = ''
              # Each source repo pins its own lean-toolchain (v4.9 .. nightly); elan fetches them
              # on demand. Pinning ELAN_HOME keeps that cache in-repo and reproducible.
              export ELAN_HOME="''${ELAN_HOME:-$PWD/.elan}"
              export PATH="$ELAN_HOME/bin:$PATH"
              echo "pipeline shell — ablate-baseline: $(command -v ablate-baseline)"
              echo "                 cc: $(command -v cc) (-D_GNU_SOURCE)"
            '';
          };

          # For iterating on Python deps (edit pyproject -> `uv lock` -> nix picks it up).
          uv = pkgs.mkShell {
            packages = [ pkgs.uv pkgs.python312 ];
            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}''${LD_LIBRARY_PATH:+:}$LD_LIBRARY_PATH"
              export UV_PYTHON_DOWNLOADS=never
              echo "uv shell (dep iteration only) — run \`uv lock\` in ./baselines, then use the default shell"
            '';
          };
        });

      # NOTE — a procedure, not a package, recorded so it is not lost:
      # `lake build` only builds a lakefile's *default target*. A `lean_lib «Foo» {}` with no
      # globs builds just the root module and its imports, so sibling modules are never compiled
      # and any challenge importing one dies with `object file ... does not exist`.
      # `scratchpad_pilot/build_modules_tolerant.sh` therefore builds every mined module
      # individually (per lake root — lampe/lean-mlir/starkware have several) and tolerates
      # failures, because `lake build M1 M2 ...` aborts the whole invocation on the first unknown
      # target (a `bench.*`/`docs.*` module belonging to no library).
    };
}
