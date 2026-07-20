{
  # Pinned OFFICIAL Isabelle releases for the ablation pipeline (the Scala ablator that used to
  # live here is gone; the Rust ablator in ./rust does the ablation now — but the toolchain this
  # flake pins is still load-bearing for VALIDATION):
  #
  #   * `isabelle-2025`   — l4v @ 429d778 needs the base 2025 release, not the point release.
  #   * `isabelle-2025-2` — the Archive of Formal Proofs (AFP) tracks the 2025-2 point release.
  #
  # Both must be the official tarballs, not nixpkgs's Isabelle: nixpkgs swaps the bundled veriT
  # for a generic build, which breaks `smt` proof reconstruction, so proofs that replay fine
  # upstream fail to check here.
  #
  #   nix develop ablators/isabelle#isabelle-2025     # then: ablate-baseline <l4v challenges>
  #   nix develop ablators/isabelle#isabelle-2025-2   # then: ablate-baseline <AFP challenges>
  description = "Pinned official Isabelle releases (2025 for l4v, 2025-2 for the AFP)";

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
          # ripgrep: the baselines agent's `search` tool shells out to `rg`.
          packages = [ isa jdk pkgs.bashInteractive pkgs.ripgrep ];
          shellHook = ''
            echo "Isabelle: $(isabelle version 2>/dev/null || echo '?')"
            echo "Build the ablator:  bash build.sh"
            echo "Run it:             ./bin/ablate <theory.thy>"
          '';
        };

      in
      {
        packages = {
          isabelle = primaryIsabelle; # convenience alias for the default version
          "isabelle-2025" = isabelle2025;
          "isabelle-2025-2" = isabelle2025-2;
          default = primaryIsabelle;
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
