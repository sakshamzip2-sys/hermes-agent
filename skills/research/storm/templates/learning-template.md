# STORM template: learning a new skill or field

Same 4-prompt flow, reweighted for learning fast and skipping the noise. The practitioner and
skeptic carry more weight: one tells you what to learn first, the other tells you what is
overhyped.

## Perspective mapping for learning

- PRACTITIONER: someone who does this daily. What do you learn first, what actually matters on
  the job, what do beginners waste time on. Weight this voice heavily.
- ACADEMIC: the underlying theory and the mental models that pay off long term.
- SKEPTIC: what in this field is overhyped, cargo-culted, or a dead end. Weight this voice
  heavily.
- ECONOMIST: where the demand and the money are, which sub-skills are scarce and valued.
- HISTORIAN: how the field evolved, which fundamentals survive across tool and trend churn.

## Flow

1. Run Prompt 1 with the 5 perspectives above on the skill or field.
2. Run Prompt 2 (contradiction map). What every voice agrees you must learn is your first
   milestone. What none addressed is a gap worth probing.
3. Run Prompt 3 with `[YOUR ROLE]` as the learner. The actionable insight should be a concrete
   first-week and first-month learning plan, ordered by leverage.
4. Run Prompt 4. The frontier question becomes your next study target. Add a 6th perspective (for
   example a hiring manager) if Prompt 4 says it would change the plan.

## Save

Write the run to `docs/research/storm/YYYY-MM-DD-<skill>.md` and update it as you learn, so the
plan compounds rather than resetting each session.
