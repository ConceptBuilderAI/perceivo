# Weld (tool, stage 2)

Welds the frame: it waits `seconds`, adds `Weld: done` to the traveler, then calls the next stage through
its own `next_stage` tool port with `perceivo.flow.invoke`. It passes that stage's answer back up
the line, field by field.

**Input** (`input`): `product` (string), `seconds` (integer), `traveler` (string, the stages
finished so far). Returns `traveler` (string) and `passed` (boolean).

**Tool port** (`next_stage`, wired to Paint): sends `product`, `seconds`, `traveler`; gets back
`traveler` and `passed`.

**Parameters:** none. **Secrets:** none.
