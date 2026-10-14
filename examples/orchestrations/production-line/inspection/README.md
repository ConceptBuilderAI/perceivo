# Inspection (tool, stage 4)

The last stage, so it has no `next_stage` port. It waits `seconds`, adds `Inspection: passed` to the
traveler, and returns `passed: True`. That answer travels back up the chain to the Line Supervisor.

**Input** (`input`): `product` (string), `seconds` (integer), `traveler` (string, the stages
finished so far). Returns `traveler` (string) and `passed` (boolean).

**Parameters:** none. **Secrets:** none.
