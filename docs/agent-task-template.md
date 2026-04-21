# Subagent task template (tier-0 parallel work)

A self-contained prompt for spawning a subagent on a single tier-0 issue from the
eval-rewrite epic (#27). Copy-paste into the `prompt:` field of an `Agent` tool
call with `isolation: "worktree"`, filling the four `{{...}}` slots.

## When to use

- The issue is labelled `tier:0` and `parallel-safe` on
  https://github.com/for-all-dev/git-history-evals/issues.
- Issue #29 (scaffolding) has been merged to master (or is mergeable cleanly).
- The agent can work independently — no coordination with other in-flight PRs.

## When NOT to use

- Tier > 0. Those have dependency checkboxes that must be closed first.
- The issue explicitly modifies a file another open PR also modifies.
- The issue needs `ANTHROPIC_API_KEY` (tier-0 work should be pure, no network).

---

## Prompt template

```
You're implementing GitHub issue #{{ISSUE_NUMBER}} in the for-all-dev/git-history-evals repo.

CONTEXT
-------
This repo extracts proof-engineering eval challenges from git history. We're
expanding experiments/ from a single-shot LLM baseline into a parallel pipeline
(pydantic-ai agent + per-commit Docker + tmux orchestration). Epic: #27.

Your issue is one of ~10 tier-0 units of work being done in parallel by several
agents. Stay STRICTLY within the scope the issue defines — do not refactor
neighboring code, add features, or touch files outside the issue's "Files"
section. Other agents are working in their own worktrees on their own issues.

REFERENCE PRS
-------------
Scaffolding (already merged or merging): https://github.com/for-all-dev/git-history-evals/pull/29
Worked example of a tier-0 issue:        https://github.com/for-all-dev/git-history-evals/pull/30

Skim both before starting. They define the conventions you should follow
(branch naming, PR body format, test expectations).

WORKFLOW
--------
1. Read the issue body: `gh issue view {{ISSUE_NUMBER}}`. The acceptance
   criteria there are authoritative — if this prompt contradicts the issue,
   trust the issue.

2. You are in a fresh worktree on a branch named `issue/{{ISSUE_NUMBER}}-{{SLUG}}`
   branched from master. `git status` should be clean.

3. If issue #29 (scaffolding) is not yet merged to master, cherry-pick it:
     git fetch origin scaffold/tier0-prep:scaffold/tier0-prep 2>/dev/null || true
     git cherry-pick scaffold/tier0-prep 2>/dev/null || true
   This is idempotent; skip silently if the files already exist.

4. Implement the issue. Concrete steps:
   - Create ONLY the files the issue's "Files" section names.
   - For any MODIFY action the issue lists, make minimal diffs. Do not
     reformat surrounding code.
   - Add tests only if the issue's acceptance criteria explicitly require them.

5. Verify locally:
   - If you added Python code: `uv run --with pytest python -m pytest <your_test_file> -v`
     from the repo root. All new tests must pass.
   - If you added shell: `bash -n <script>` for syntax, then exercise the
     happy path manually.
   - If you added a Dockerfile: validate with `docker build --check .` if
     available, or a dry `docker build --no-cache -t scratch/test -f <file> . --target <stage>`.

6. Commit with a message in the form:
     <file or module>: <short imperative subject>

     <body paragraph describing WHY, referencing any non-obvious design
     choices. Note any deviations from the issue body and why.>

     Closes #{{ISSUE_NUMBER}}.

     Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

   Use a HEREDOC for multiline messages.

7. Push: `git push -u origin issue/{{ISSUE_NUMBER}}-{{SLUG}}`.

8. Open a PR targeting master:
     gh pr create --title "#{{ISSUE_NUMBER}} <short subject>" --body "$(cat <<'EOF'
     ## Summary
     - <bullet 1>
     - <bullet 2>

     ## Test plan
     - [x] <test 1>
     - [x] <test 2>

     Closes #{{ISSUE_NUMBER}}.

     🤖 Generated with [Claude Code](https://claude.com/claude-code)
     EOF
     )"

   Return the PR URL as the last line of your final message.

GUARDRAILS
----------
- Do NOT modify run_experiment.py unless your issue explicitly says so (#14
  owns that; you'd conflict).
- Do NOT touch experiments/metrics.py unless you are #4.
- Do NOT bake any API keys or personal paths into files.
- Do NOT push to master. Never force-push.
- If the issue's acceptance criteria contradict each other or the referenced
  code has drifted since the issue was filed, STOP and leave a comment on
  the issue explaining the discrepancy instead of guessing.

OUT OF SCOPE (for ALL tier-0 issues)
------------------------------------
- Updating other tier-0 issues' files.
- "Drive-by" fixes to run_experiment.py, metrics.py, or docker-compose.yml.
- Upgrading Python, Coq, or Docker versions not pinned in the issue.
- Running the full eval pipeline end-to-end (not possible yet; several upstream
  pieces don't exist).

DELIVERABLE
-----------
A single PR URL. Nothing else. No summary documents, no plan files, no
follow-up suggestions unless the issue asked for them.
```

## Example fillings

| Issue | `{{ISSUE_NUMBER}}` | `{{SLUG}}` |
|---|---|---|
| shared/prompts.py | `3` | `shared-prompts` |
| metrics.py extensions | `4` | `metrics-extensions` |
| shared/compile.py | `5` | `shared-compile` |
| agent/deps.py | `6` | `agent-deps` |
| orchestrate/lib.sh | `7` | `orchestrate-lib-sh` |
| orchestrate/detect_coq_version.py | `8` | `detect-coq-version` |
| docker/base.Dockerfile | `9` | `docker-base` |
| experiments/pyproject.toml | `22` | `experiments-pyproject` |

## Orchestrator checklist (what the main Claude does)

1. Confirm #29 (scaffolding) has merged — if not, agents will be cherry-picking.
2. For each issue, spawn one agent with `Agent(isolation: "worktree", prompt: <filled template>)`.
3. Send them in the same message for parallel execution.
4. When each returns a PR URL, verify the linked changes with `gh pr diff <N>`.
5. Merge in any order (tier-0 is parallel-safe by construction).
