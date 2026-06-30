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
        # The bundled Isabelle ships its own JDK and Scala compiler under
        # $ISABELLE_HOME/contrib; `isabelle scalac` / `isabelle java` use those.
        # We only need a JDK in the shell for ad-hoc tooling.
        jdk = pkgs.jdk21;

        # The OFFICIAL Isabelle build (not nixpkgs's), so its bundled, internally
        # consistent contrib SMT solvers (verit-rmx, z3, cvc5) are kept — nixpkgs swaps
        # veriT for a generic build that breaks `smt` proof reconstruction. autoPatchelf
        # makes the prebuilt ELF binaries (poly/ML, bundled JRE, solvers) run on NixOS.
        # Different repos need different versions (e.g. l4v @ 429d778 needs the base 2025;
        # 2025-2 changed an ML signature that breaks its Eisbach_Tools), so we expose both.
        # The `website-Isabelle<ver>/dist/` path is stable (it doesn't move when a newer
        # point release ships, unlike bare `/dist/`).
        mkIsabelle = { version, sha256 }: pkgs.stdenvNoCC.mkDerivation {
          pname = "isabelle-official";
          inherit version;
          src = pkgs.fetchurl {
            url = "https://isabelle.in.tum.de/website-Isabelle${version}/dist/Isabelle${version}_linux.tar.gz";
            inherit sha256;
          };
          nativeBuildInputs = [ pkgs.autoPatchelfHook ];
          buildInputs = [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
            pkgs.gmp
            pkgs.ncurses5
            pkgs.freetype
            pkgs.fontconfig
            pkgs.alsa-lib
            pkgs.xorg.libX11
            pkgs.xorg.libXext
            pkgs.xorg.libXrender
            pkgs.xorg.libXtst
            pkgs.xorg.libXi
          ];
          dontConfigure = true;
          dontBuild = true;
          # Optional jedit/native libs aren't needed for headless `isabelle build`.
          autoPatchelfIgnoreMissingDeps = true;
          # The tarball ships harmless dangling symlinks (e.g. contrib/e-3.2/src/lib/*.a).
          dontCheckForBrokenSymlinks = true;
          # nix unpack cd's into the single top-level dir, so CWD is the Isabelle tree.
          # Use a version-neutral install dir (Isabelle self-locates via the symlink).
          installPhase = ''
            mkdir -p $out/bin $out/isabelle-dist
            cp -r . $out/isabelle-dist/
            chmod -R u+w $out/isabelle-dist
            # The bundled JDK crashes on NixOS ("Fontconfig head is null"); point
            # ISABELLE_JDK_HOME at a nix JDK. The jdk component's etc/settings is sourced
            # after user settings, so a user override loses — override it in-place
            # (a trailing assignment wins within the file).
            for s in $out/isabelle-dist/contrib/jdk-*/etc/settings; do
              echo 'ISABELLE_JDK_HOME=${jdk.home}' >> "$s"
            done
            # The launcher resolves symlinks (`if [ -L "$0" ]`), so a symlink keeps
            # Isabelle's self-location of ISABELLE_HOME intact (unlike makeWrapper).
            ln -s $out/isabelle-dist/bin/isabelle $out/bin/isabelle
          '';
        };

        isabelle2025 = mkIsabelle {
          version = "2025";
          sha256 = "0mkw2hw2zdnww6p322zh8k1blx9myww6drlam8qzw8qq6zg6c79x";
        };
        isabelle2025-2 = mkIsabelle {
          version = "2025-2";
          sha256 = "0g5kpx3hs9pn7m6jrp6ji5vkhib3q2z3z7vax65hs9y1qxxm02m2";
        };
        # Primary version for the ablator build + the default shell (latest point release).
        # l4v work uses the `isabelle-2025` shell explicitly (it needs the base 2025).
        primaryIsabelle = isabelle2025-2;

        mkDevShell = isa: pkgs.mkShell {
          packages = [ isa jdk pkgs.bashInteractive ];
          shellHook = ''
            echo "Isabelle: $(isabelle version 2>/dev/null || echo '?')"
            echo "Build the ablator:  bash build.sh"
            echo "Run it:             ./bin/ablate <theory.thy>"
          '';
        };

        # The ablator CLI: a small Scala program compiled against the Isabelle/Scala
        # classpath, wrapped so `ablate file.thy` runs it inside the Isabelle environment.
        ablator = pkgs.stdenv.mkDerivation {
          pname = "isabelle-ablator";
          version = "0.1.0";
          src = ./.;
          nativeBuildInputs = [ primaryIsabelle jdk ];
          # Isabelle writes user settings under $ISABELLE_HOME_USER; in the nix sandbox
          # $HOME is not writable, so point it at the build tree.
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
              --replace '@ISABELLE@' "${primaryIsabelle}/bin/isabelle"
            chmod +x $out/bin/ablate
          '';
        };
      in
      {
        packages = {
          inherit ablator;
          isabelle = primaryIsabelle; # convenience alias for the default version
          "isabelle-2025" = isabelle2025;
          "isabelle-2025-2" = isabelle2025-2;
          default = ablator;
        };

        apps.default = {
          type = "app";
          program = "${ablator}/bin/ablate";
        };

        devShells = {
          # Two version-specific shells: l4v @ 429d778 needs `isabelle-2025`; AFP / general
          # work uses `isabelle-2025-2`. Pick with `nix develop .#isabelle-2025` etc.
          "isabelle-2025" = mkDevShell isabelle2025;
          "isabelle-2025-2" = mkDevShell isabelle2025-2;
          default = mkDevShell primaryIsabelle;
        };
      });
}
