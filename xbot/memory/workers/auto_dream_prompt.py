from __future__ import annotations


def build_auto_dream_prompt(memory_root: str, transcript_dir: str, extra: str = "") -> str:
    base = f"""# Dream: Memory Maintenance

Memory directory: `{memory_root}`
Session transcripts: `{transcript_dir}`

## Phase 1 — Orient
- ls the memory directory
- Read MEMORY.md
- Skim existing topic files

## Phase 2 — Gather recent signal
- Use logs or transcript grep to find recent changes
- grep -rn "<narrow term>" {transcript_dir}/ --include="*.jsonl" | tail -50

## Phase 3 — Maintain
- Update durable memory files rather than creating duplicates
- Convert relative dates to absolute dates
- Delete contradicted facts

## Phase 4 — Prune and index
- Update MEMORY.md as a one-line-per-entry index
- Keep it concise
- Say no changes if nothing changed
"""
    if extra:
        base += f"\n## Additional context\n\n{extra}\n"
    return base
