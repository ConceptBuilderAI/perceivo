"""The prebuilt TOOL / FSM / POLICY / ADAPTER components pass the server's resolvability check.

Their bodies return the inbound artifact's data (or the request_data) as
response_data, so its key set cannot be enumerated. The platform data service
refuses that on import as ``VAL-UNVERIFIABLE-RESP`` unless the out-role port
declares ``response_data_contract: dynamic`` and the declaration is true.
``_write_prebuilt`` declares it for exactly these four types; this test fails if
the declaration is dropped or becomes one the truth check rejects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from perceivo.orchestration._builder._prebuilt import _write_prebuilt
from perceivo.validator._component import validate_source


@pytest.mark.parametrize("ctype", ["TOOL", "FSM", "POLICY", "ADAPTER"])
def test_a_prebuilt_that_echoes_its_input_is_not_unverifiable(ctype: str, tmp_path: Path) -> None:
    _write_prebuilt(tmp_path, "node", "echo", ctype)
    report = validate_source(
        script=(tmp_path / "node.py").read_text(encoding="utf-8"),
        component_type=ctype,
        manifest_yaml=(tmp_path / "node.yaml").read_text(encoding="utf-8"),
    )
    assert [
        (c.id, c.message)
        for c in report.checks
        if c.id.startswith(("VAL-UNVERIFIABLE", "VAL-CONTRACT-DYNAMIC"))
    ] == []
