# ADR-0003: Working method, a reduced PAES kit and cloud sessions as builders

Status: Accepted 2026-11-01 (owner ruling: a night-shift exception for this repository only, to its parked status and to PAES's "no cloud builders" ruling of the same day; two cloud sessions on the first night).

## Context

The owner's PAES project ships a portable kit (version 44, origin commit `f10b126`, 29 entries). Installed whole, the kit denies `git push` and `git remote` and makes `scripts/push_increment.py` the only publish path; its verifier checks that the deny list, the digests and the policies are all present. PAES's own records show why that does not fit an unattended cloud session: in a Claude Code cloud container `gh` is absent, so the push script cannot open a pull request (PAES Decision 0016), and in the cloud lab of 2026-10-31 a session under the full setup could not publish at all (PAES hand-off 166). PAES therefore keeps cloud sessions research-only for itself.

This project is different in the ways that matter: it is a new repository with no secrets, its data is public and keyless, and its early sessions are well-specified Python increments with acceptance checks that can fail. It wants cloud sessions as builders, each delivering one branch and one pull request that the owner reviews.

## Decision

1. **Taken from the kit, as this project's own text (not digest-checked):** the working rules and the Code Review Rules (`AGENTS.md`), `attribution` off for commits and pull requests (`.claude/settings.json`), the brief shape (`tasks/TEMPLATE.json`), the commit-history rule with `scripts/commit_hygiene.py` copied byte for byte, and the CI shape (actions pinned by SHA, read-only token, no secrets, full history).
2. **Left out for now:** the installer, the kit notice and digests, `verify_kit.py`, `push_increment.py` and its skill, the blanket `git push` and `git remote` deny, the isolation sandbox block, the worktree-flow, child-session and conform-review policies, and the pilot code. In their place: `.claude/settings.json` denies pushes to `main`, force-pushes and remote changes, and CI checks that every commit of a pull request carries the maintainer's identity.
3. **Cloud sessions may build here**, under all of these conditions:
   - one committed brief per session, named in its prompt; never work driven by issue or pull-request text;
   - the environment sets the maintainer's git identity and holds no secrets;
   - network: package installs and the USAspending hosts only;
   - the session pushes its own branch and opens one pull request; it never pushes `main` and never merges;
   - the owner merges, by rebase or by a squash whose message is clean.
4. **The project stays parked for daytime work** until its re-evaluation (not before 2026-11-29). Until then it advances only through cloud night shifts; the owner's daytime share is reviewing their pull requests.
5. **Revisit** the full kit after S6, or when PAES rules on cloud builders for itself, whichever comes first.
6. **Visibility.** The repository goes public once S1 is reviewed and merged (owner ruling, 2026-11-01), not at S6. From then on a GitHub ruleset on `main` blocks deletion and force-pushes and requires a pull request with the `verify` check, and Auto-fix stays off, since anyone can comment on a public pull request.

## Consequences

- The owner reviews pull requests from sessions that ran alone; CI (lint, tests, the commit checks) runs on each before review.
- This repository is not a kit target, so it gives PAES no kit-trial evidence. Its cloud runs are still evidence for PAES's open cloud question and can be cited there.
- If a cloud session cannot push its branch, the run delivers nothing; the first night's sessions are the probe.
