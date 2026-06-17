# Archived skills (reversible cut)

Skills moved out of the offered/installable set but **kept** (never `rm`-ed) so
the cut is fully reversible — restore any with `git mv` back to its original
path under `optional-skills/`.

This directory is NOT under `skills/` or `optional-skills/`, so the skills
loader/sync never scans it: archived skills do not surface to the model and are
not installable via the skills catalog.

| Skill | From | Why archived |
|-------|------|--------------|
| `security/godmode` | `optional-skills/security/godmode` | Jailbreak skill — liability, low real utility. |
| `mlops/obliteratus` | `optional-skills/mlops/obliteratus` | Abliteration (strips model safety) — liability, low real utility. |

Their user-facing docs are under `_docs/` here too, so the cut isn't advertised
as installable.

Notes from the STEP 12 cut:
- `writing-plans` (a duplicate of `plan`) is **not** present in this repo's
  source tree — it exists only in the synced runtime `~/.hermes/skills/`, so
  there was nothing to archive here. Remove it from the runtime tree with the
  curator if desired.
- `slack-gif-creator` is registered exactly **once** (`skills/media/
  slack-gif-creator`); the apparent "double listing" is only in the external
  `skills/index-cache/*.json` hub catalog snapshots, not the agent's own skill
  registration — so no dedup was needed.
