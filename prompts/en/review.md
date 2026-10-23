Daily review ({{DATE}}): tidy the secretary's memory, and turn lessons that apply to anyone into a prompt diff. Report by the worker rules.

Materials:
- The secretary's memory: `{{MEMORY}}`. `MEMORY.md` is the index (one line per entry); every other file is one memory.
- Lessons: `{{LESSONS}}`. Everything above the "Done" section is to be reviewed, in the format "date · type · what happened · the user's own words".
- Prompts: `prompts/secretary.md`, `prompts/worker.md`, `prompts/worker-cursor.md` in the claudesec repo `{{REPO}}`.
- The decision queue `{{QUEUE}}` and the history index `{{INDEX}}`: consult them to judge which memories are out of date; don't change them.

## 1. Tidy the memory (write it straight to disk, no need to ask)

- Merge memories that say the same thing; delete ones that are out of date (check against the repo's current code and prompts, the decision queue's "Done", and the history index; don't delete from impressions);
  add `[[name]]` links between related ones; in `MEMORY.md`, one line per memory as `- [Title](file) — one sentence`, and delete lines that point to files that don't exist.
- Keep each memory to one fact; feedback / project memories need **Why:** and **How to apply:**.
- For a rule that is already in the prompts and that step 2 below won't remove, the memory can keep just one line pointing to it instead of repeating it in full.
- Before dispatching you, claudesec backed up the whole directory to `{{REVIEWS}}/{{DATE}}-memory-before/`; if something goes wrong, restore from there.
- This directory is your auto memory directory for this run: changing it with Read / Write / Edit brings up no confirmation dialog. Deleting a whole entry takes `rm`, which asks once each time; save them up and delete them all together at the end.

## 2. Prompt diff (propose only; don't land it on main)

1. Read the lessons and the feedback memories, group them and count. Only what "applies to anyone, independent of this machine and of any particular project" goes into the prompts;
   what is specific to a machine or project, or a personal preference of this user (say, who they like a kind of work to go to, or which language to reply in), stays in memory. Turning a single correction into a hard "from now on, every X…" is overfitting: either write it as a criterion for judgment, or hold off for now.
2. For every rule you add, also look for what can be removed or merged: duplicates, things the code already guarantees, things out of date. Prompts that only ever grow will blow up the secretary's context.
3. **Don't change a single word of the "Iron rules" section.**
   `<!-- if cursor --> … <!-- else --> … <!-- end cursor -->` wraps content given only on machines that have Cursor workers; keep the markers when you edit.
4. Open a new worktree following the repo's worktree rules, on branch `review/{{DATE}}`; make every prompt change in both language versions, prompts/X.md and prompts/en/X.md, and make sure python3 -m unittest tests.test_prompt_parity passes; commit when done, **don't push**.
   Save `git diff origin/main` to `{{REVIEWS}}/{{DATE}}.diff`.
5. Report NEED_DECISION: in DID, what you changed in the memory (how many merged, which deleted); in ASK, list every prompt change, one per line,
   marked "add / remove / change" with its basis (which lessons or memories), plus the path of the diff file; give the options "approve all / only #N / reject all".
6. After the secretary relays "User decision:": keep what the user chose, fetch, rebase onto origin/main, push (the user has agreed that claudesec's changes get pushed),
   and remove the worktree; in `{{LESSONS}}`, move the adopted lessons into the "Done" section, noting after each which rule it went into. Then send DONE.

If no lesson is worth putting into the prompts: skip step 2, send DONE as soon as the memory is tidied, and in RESULT write "no prompt changes" and why.
