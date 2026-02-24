# Plan

1. (kinda optional) refactor `subprocess.run` to be the `git-python` binding/wrapper
2. set up building each commit when you check it into the task
   - versions, installs, deps, makefiles
   - tmpdirs
   - probably need some pre-caching solution
3. [research] how to use naturally occuring ground truth to add an extra check on the synthetic solution
   - detecting unusualness, redundancies, detours
      - in order for this to be valuable, we kinda need to think that redundancies and detours lead to slop, attacks from an advro perspective (i.e. false positives/proofs of false that go undetected),
   - tools for "diffing" synthetic against human---- i.e., baseline of gitdiff, but tree / structure-aware diffs for improvements? 
