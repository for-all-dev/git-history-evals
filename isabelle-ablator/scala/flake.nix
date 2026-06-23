{
  description = "Isabelle semantic-ablation toolkit — parse .thy files with the bundled Isabelle/Scala outer-syntax parser and replace proofs with `sorry` to synthesise post-training data.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        isabelle = pkgs.isabelle;
        # The bundled Isabelle ships its own JDK and Scala compiler under
        # $ISABELLE_HOME/contrib; `isabelle scalac` / `isabelle java` use those.
        # We only need a JDK in the shell for ad-hoc tooling.
        jdk = pkgs.jdk21;
      in
      {
        packages = {
          inherit isabelle;
          default = self.packages.${system}.ablator;

          # The ablator CLI: a small Scala program compiled against the
          # Isabelle/Scala classpath, wrapped so `ablate file.thy` runs it
          # inside the Isabelle environment.
          ablator = pkgs.stdenv.mkDerivation {
            pname = "isabelle-ablator";
            version = "0.1.0";
            src = ./.;
            nativeBuildInputs = [ isabelle jdk ];
            # Isabelle writes user settings under $ISABELLE_HOME_USER; in the
            # nix sandbox $HOME is not writable, so point it at the build tree.
            buildPhase = ''
              export HOME=$TMPDIR
              export ISABELLE_HOME_USER=$TMPDIR/.isabelle
              mkdir -p $ISABELLE_HOME_USER
              bash build.sh
            '';
            installPhase = ''
              mkdir -p $out/lib $out/bin
              cp build/ablator.jar $out/lib/
              substitute bin/ablate $out/bin/ablate \
                --replace '@JAR@' "$out/lib/ablator.jar" \
                --replace '@ISABELLE@' "${isabelle}/bin/isabelle"
              chmod +x $out/bin/ablate
            '';
          };
        };

        apps.default = {
          type = "app";
          program = "${self.packages.${system}.ablator}/bin/ablate";
        };

        devShells.default = pkgs.mkShell {
          packages = [ isabelle jdk pkgs.bashInteractive ];
          shellHook = ''
            echo "Isabelle: $(isabelle version 2>/dev/null || echo '?')"
            echo "Build the ablator:  bash build.sh"
            echo "Run it:             ./bin/ablate <theory.thy>"
          '';
        };
      });
}
