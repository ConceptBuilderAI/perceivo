# Line Supervisor (agent)

Starts the line with one call, `perceivo.flow.invoke("first_stage", ...)`, and waits while every
stage runs. It sends an empty traveler, gets back the signed traveler and the inspection result,
and counts the stages that finished. It uses no model.

**Input** (`input`): `product` (string), `seconds_per_stage` (integer).

**Tool port** (`first_stage`, wired to Cut): sends `product`, `seconds`, `traveler`; gets back
`traveler` (string) and `passed` (boolean).

**Output** (`output`): `product` (string), `stages_completed` (integer), `passed` (boolean), plus a
text artifact holding the product name and the traveler.

**Parameters:** none. **Secrets:** none.
