# Work Order (start)

The START node. It checks the work order and passes it to the Line Supervisor. It raises an
error if `product` is empty or `seconds_per_stage` is not between 1 and 30.

**Input** (`external_in`): `product` (string), `seconds_per_stage` (integer, 1 to 30).

**Output** (`output`): `product` (string, trimmed), `seconds_per_stage` (integer).

**Parameters:** none. **Secrets:** none.
