# Communication Materials

This directory holds communication materials, including workshop papers and competition documentation for NeurIPS 2026 VeriCodeGen.

## Overleaf Sync Workflow

The workshop and competition templates are synced with an Overleaf project (`proofablate__vericode-workshop`) using the `olcli` command-line tool.

### Before Editing

Always pull the latest changes from Overleaf before starting work:

```bash
cd comms/vericode-workshop
olcli pull
```

### After Editing

After making local changes, push them back to Overleaf:

```bash
olcli push
```

Preview changes before pushing:

```bash
olcli push --dry-run
```

### Conflict Resolution

With multiple editors (humans and agents) working on the same Overleaf project, conflicts are a real risk.

**Conflict resolution strategy: Remote always wins on conflict.** Before starting any edits:
1. Run `olcli pull` to sync the latest remote version
2. Make your changes locally
3. Run `olcli push` to send changes back
4. If `olcli push` fails due to a conflict, run `olcli pull --force` to reset to the remote version, then redo your changes

This ensures that if multiple people edit simultaneously, the pull-first discipline keeps everyone consistent with the remote state.

### Additional olcli Commands

For full documentation of olcli commands (compile, pdf download, etc.), see `.claude/skills/overleaf/SKILL.md`.

---

The `.olcli.json` file in `vericode-workshop/` pins the Overleaf project ID and remote manifest and should always be committed to track sync state.
