# Shipping (end)

The END node. It reports `shipped` if the product passed inspection, or `held for rework` if it did
not, and passes the Line Supervisor's traveler artifact through.

**Input** (`input`): `product` (string), `stages_completed` (integer), `passed` (boolean).

**Output** (`external_out`): `product` (string), `stages_completed` (integer), `status` (string,
`shipped` or `held for rework`).

**Parameters:** none. **Secrets:** none.
