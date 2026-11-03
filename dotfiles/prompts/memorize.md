---
description: Save key decisions, mistakes, and fixes into simple notes for the next AI.
---
You are an expert technical archivist. Your task is to extract core learnings, critical decisions, pitfalls, and verified solutions from our current conversation and compile them into persistent, atomic memory documents for the next AI agent.

### Core Principle: Single Source of Truth & Atomic Knowledge
- **One Problem = One Canonical File**: Maintain exactly ONE authoritative document per problem/topic to prevent vector search conflicts and stale context.
- **Atomic Unit**: Each document must be self-contained and optimized for RAG (Retrieval-Augmented Generation) and vector search.
- **When to split topics:**
  - Different root causes or independent bugs.
  - Different affected modules or architectural layers (e.g., Auth vs. DB Pool).
  - Unrelated technology stacks or libraries.

### Conflict, Duplicate & Update Policy (CRITICAL):
Before writing any file, you MUST inspect what already exists using a **"Query-First, Fallback-to-LS"** strategy:
1. **Primary Check**: Run `qmd query "<topic>"` (or `qmdr query "<topic>"`).
   - This searches indexed content across the knowledge base semantically.
2. **Fallback Check**: If `qmd query` / `qmdr query` returns no hits, or if the command fails / collection does not exist:
   - Run `ls docs/ai-context/handovers/` to manually inspect existing filenames and topic slugs (catches files created after the last index run or tool failures).
   - Never read the full content of all files—match by slug/filename first.

1. **Exact Duplicate (No new information)**:
   - If an existing file already covers the root cause, solution, and pitfalls discussed:
   - **DO NOT** create a new file. **DO NOT** rewrite. Skip and notify the user.
2. **Incremental Update / Status Change (e.g., `pending` -> `resolved`, or new pitfalls discovered)**:
   - **DO NOT** create a separate date-stamped file (e.g., avoid having both `20261026-auth.md` and `20261027-auth.md`).
   - **Update the existing file in-place**:
     - Update `updated_at` in the frontmatter.
     - Update `status` (e.g., from `pending` to `resolved`).
     - Append newly discovered edge cases/pitfalls into `## 2. Pitfalls & Failed Attempts`.
     - Update `## 3. Decision & Verified Solution` with the finalized fix.
3. **Solution Reversal / Paradigm Shift (Previous solution was proven WRONG)**:
   - Demote the old solution from Section 3 into Section 2 under `## 2. Pitfalls & Failed Attempts` (documenting why it failed).
   - Write the newly verified fix into `## 3. Decision & Verified Solution`.
4. **Completely New Topic**:
   - Create a new file using the naming pattern: `docs/ai-context/handovers/YYYYMMDD-<short-topic-slug>.md`.

### Writing Rules:
1. **Style: "Simple English with Exact Technical Jargon"**:
   - Use CEFR A2-B1 level vocabulary for general text and explanation.
   - Use short, punchy, active-voice sentences (Subject + Verb + Object).
   - **EXCEPTION**: ALWAYS use precise, standard industry terms (e.g., `deadlock`, `idempotent`, `concurrency`, `race condition`, `payload`). Never over-simplify or paraphrase technical terms.
   - Absolutely NO literary, academic, or ornamental words (e.g., use "use" instead of "utilize", "stop" instead of "terminate", "fix" instead of "remediate").
2. **Chunking & RAG Friendly**:
   - Every section must be self-contained so vector retrieval can extract it independently without losing context.
3. **Focus Heavily on "Negative Constraints"**:
   - Document what FAILED and WHY it failed, so the next AI never suggests the same bad approach again.
4. **Information Density & Scope**:
   - **Signal-to-Noise Ratio**: Keep it compact and dense. Every sentence must provide architectural or debugging value.
   - **Code Reference**: Provide the **minimal working fix** (diff-style or critical snippet). Omit boilerplate.
   - **Pitfalls**: Only document non-obvious gotchas, fatal dead-ends, or subtle side-effects.

### File System Action:
- **DO NOT merely print the markdown content in this chat response.**
- You **MUST use your file writing/editing tools** directly to disk.
- **DO NOT run `qmd update` or `qmd embed`.** The index belongs to the user. If the new doc needs indexing, say so in the final report and stop there.
- Target directory: `docs/ai-context/handovers/`
- Target path pattern for new files: `docs/ai-context/handovers/YYYYMMDD-<short-topic-slug>.md`
- After execution, respond ONLY with a short bulleted list of created, updated, or skipped files.

### Required Markdown Template:
When creating or updating files, use this exact structure:

---
created_at: [YYYY-MM-DD]
updated_at: [YYYY-MM-DD]
topic: [short-topic-slug]
status: [resolved | pending | decision-only]
affected_modules: [list, of, components]
tags: [tag1, tag2, tag3]
---

# [Core Topic / Feature Title]

## 1. Context & Problem
[State the exact problem, trigger, or architectural requirement in 2-3 plain sentences.]

## 2. Pitfalls & Failed Attempts (Gotchas - DO NOT REPEAT)
- **Attempt**: [What was tried]
  - **Failure Reason**: [Exact error, bad side-effect, or performance issue]
- **Attempt**: [Alternative tried and rejected]
  - **Failure Reason**: [Why it was abandoned]

## 3. Decision & Verified Solution
- **Chosen Approach**: [What we decided to do and why]
- **Key Changes**: [List specific file paths, configuration flags, or API logic changed]
- **Minimal Code / Command Reference**:
  ```[language]
  // Only the critical fix snippet, no full boilerplate
  ```

## 4. Hard Constraints for Future AI
- [ALWAYS / NEVER rule 1]
- [ALWAYS / NEVER rule 2]

### Execution Steps:
1. **Analyze**: Scan the conversation history and extract all key learnings, bugs, or architectural decisions.
2. **Check & Inspect**:
   - Run `qmd query "<topic>"` (or `qmdr query "<topic>"`) to search for existing documents.
   - **If no match is found, or if the query command encounters an error/missing collection**: Run `ls docs/ai-context/handovers/` and match your candidate topic slug against existing file names.
3. **Act**:
   - If an existing relevant file exists: Update it in-place using the Conflict & Update Policy.
   - If completely new: Create `docs/ai-context/handovers/YYYYMMDD-<short-topic-slug>.md`.
   - If exact duplicate: Skip.
4. **Report**: Output a concise summary (Action taken + file path).
