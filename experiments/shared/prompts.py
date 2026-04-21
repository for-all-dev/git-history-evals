"""Model-facing prompts shared by the baseline and agent runners.

This module is the single source of truth for the Claude system prompts and
user-prompt builders used to pose proof-completion challenges. Both the
baseline runner (`experiments/run_experiment.py`) and the upcoming ReAct-loop
agent runner import from here so the two pipelines cannot drift apart.

Exports:
- `SYS_PROMPT_BASELINE`: byte-identical copy of the original baseline
  system prompt in `run_experiment.py`. The baseline is a single-shot
  completion — the model emits tactic lines directly.
- `SYS_PROMPT_AGENT`: a NEW system prompt for the ReAct loop. The agent
  does not emit tactics directly; instead it iterates against the tools
  `read_file`, `write_proof`, `compile`, and `give_up` until the proof
  compiles or its tool budget is exhausted.
- `make_prompt_full` / `make_prompt_partial`: user-prompt builders used
  verbatim by both runners.
"""

from __future__ import annotations


SYS_PROMPT_BASELINE = """\
You are an expert Coq proof engineer. Your task is to supply the missing \
tactic lines that complete a proof.

Output ONLY the tactic lines themselves — do not repeat any surrounding \
Lemma/Theorem/Definition header, do not include the opening `Proof.`, \
and do not include anything outside the proof body.

End with `Qed.` (or `Defined.` if the original used `Defined.`).
If you genuinely cannot determine the proof, write just `Admitted.`.
Do not hallucinate tactics.
"""

SYS_PROMPT_AGENT = """\
You are an expert Coq proof engineer operating a ReAct loop against a live
Coq toolchain. The proof body of the target declaration has been replaced by
`Admitted.` — your job is to replace that placeholder with tactics that make
the file compile.

You have four tools:
- `read_file`: inspect the current state of the source file (use this to see
  context around the target declaration).
- `write_proof`: splice tactic lines into the file in place of the `Admitted.`
  placeholder. This edits the file on disk; it does NOT check the proof.
- `compile`: invoke the Coq compiler on the file and return its output. You
  MUST call `compile` after every `write_proof` — a write without a compile
  tells you nothing about whether the proof went through.
- `give_up`: signal that you cannot close the goal. Call this ONLY when you
  are truly stuck after iterating — not as a shortcut.

Work within your tool-call budget. Do not emit tactics in your chat output;
all proof attempts must go through `write_proof` followed by `compile`.
"""


def make_prompt_full(decl: str, file_content: str) -> str:
    return (
        f"The proof of `{decl}` is replaced entirely by `Admitted.`.\n"
        f"Write the complete tactic proof body (tactics only, ending with Qed.).\n\n"
        f"FILE:\n{file_content}"
    )

def make_prompt_partial(decl: str, file_content: str, n: int) -> str:
    return (
        f"The last {n} tactic sentences of the proof of `{decl}` have been removed "
        f"and replaced with `Admitted.`.\n"
        f"Write only those missing tactic lines (ending with Qed.) to complete the proof.\n\n"
        f"FILE:\n{file_content}"
    )
