"""``VAL-CONTRACT-RESP``: the returned ``response_data`` against the output ports.

The fields a script's Output returns must match the ``response_data`` declared
on the component's output-role ports, in both directions, as the platform checks
at import. Check id, severity, titles and messages match the platform's.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

AddCheck = Callable[..., None]


class ResponseDataResolution(NamedTuple):
    """The outcome of resolving a script's returned ``response_data``.

    ``visible_keys`` are the keys the script demonstrably returns; the
    returned-but-undeclared direction always runs against them, so no capability
    can hide a field the script emits. ``satisfiable_keys`` add what a verified
    passthrough forwards and are used only by the declared-but-unreturned
    direction. ``check_declared_are_returned`` is False when the key set is not
    complete: unverifiable, or a verified dynamic contract.
    """

    visible_keys: set[str]
    satisfiable_keys: set[str]
    unverifiable: bool
    check_declared_are_returned: bool


def check_response_contract(
    resolution: ResponseDataResolution, declared: set[str], add: AddCheck
) -> None:
    """Report returned-but-undeclared and declared-but-unreturned fields."""
    script_keys = resolution.visible_keys
    if not script_keys and not declared:
        return

    undeclared = sorted(script_keys - declared)
    if undeclared:
        add(
            "VAL-CONTRACT-RESP",
            "error",
            "contract",
            "Undeclared response_data fields in script",
            f"Script returns response_data fields not declared on an output port: {undeclared}",
            suggestion="Add these fields to the output port's response_data in the YAML "
            "manifest, or remove them from the script's Output.",
        )

    unused = sorted(declared - resolution.satisfiable_keys)
    if unused and resolution.check_declared_are_returned:
        add(
            "VAL-CONTRACT-RESP",
            "error",
            "contract",
            "Output port declares response_data the script never returns",
            f"An output port declares response_data the script's Output never returns: {unused}",
            suggestion="Return these fields in the script's Output constructor, or remove "
            "them from the output port's response_data.",
        )
