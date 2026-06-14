---
name: triage-bug-report
description: Triage a user-filed bug report from .ai/BR/, analyze logs, locate the root cause in the codebase, and produce a fix plan. Use when the user asks to look at a bug report, triage a bug, investigate a filed report, or fix a bug from a report.
---

# Triage Bug Report

Read a bug report filed via the deck editor, analyze the attached logs and relevant code, and produce an actionable fix plan.

## Bug Report Structure

Reports live under `.ai/BR/BR_<timestamp>/` and contain:

- `bug_report.md` -- user description, filing date, and list of attached logs.
- `mtg_<timestamp>_<pid>.log` -- snapshot of the deck editor server log at filing time.

## Workflow

### Phase 1 -- Identify the Report

1. If the user specifies a report (path or timestamp), use it directly.
2. Otherwise, list the folders under `.ai/BR/` and pick the most recent one, or ask which one to triage.

### Phase 2 -- Read & Understand

1. Read `bug_report.md` to get the user description and list of attached log files.
2. Read every attached log file in the same folder.
3. Summarize:
   - **What the user reported** (from the description).
   - **What the logs show** -- errors, warnings, tracebacks, or suspicious sequences around the filing timestamp.

### Phase 3 -- Locate Root Cause

1. From the log evidence (error messages, module names, function names, tracebacks), search the codebase to find the relevant source files and functions.
2. Read those files; trace the execution path that leads to the observed failure.
3. Identify the root cause (or top candidates if ambiguous).

### Phase 4 -- Write Fix Plan

Write a `fix_plan.md` file **inside the same BR folder** with this structure:

```markdown
# Fix Plan

## Bug Summary
One-paragraph recap: what the user saw, what the logs confirmed.

## Root Cause
Explain the underlying defect -- which file, function, and logic path.

## Proposed Fix
Step-by-step changes required, referencing specific files and line ranges.

## Risks & Side Effects
Anything that might break or need extra testing.

## Verification
How to confirm the fix works (manual steps or test cases).
```

Present the plan to the user for approval before making any code changes.

## Rules

- **Read before you guess.** Always read the logs and referenced source files; do not speculate without evidence.
- **One report at a time.** Fully triage the selected report before moving on.
- **Do not modify code.** This skill produces a plan only; implementation is a separate step after user approval.
- **Preserve the BR folder.** Never delete or overwrite existing files in the report folder; only add `fix_plan.md`.

## TL;DR

Read a bug report from `.ai/BR/`, analyze the description and attached deck editor logs, trace the root cause in the codebase, and write a `fix_plan.md` in the same folder for user review before any code changes.
