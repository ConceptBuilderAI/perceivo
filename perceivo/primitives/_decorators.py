"""Define orchestration components with decorators.

Provides decorator alternatives to class-based component definitions.
Each decorator creates a class compatible with the orchestration
executor's contract (ENTRY_CLASS, base class inheritance, _run/_start/_end).

Two spellings reach the same decorator object: ``perceivo.primitives.agent``
and the top-level re-export ``perceivo.agent``.

Usage::

    from perceivo.primitives import AgentInput, AgentOutput, Artifact, agent

    @agent
    async def my_agent(input: AgentInput) -> AgentOutput:
        return AgentOutput(
            artifact=Artifact(type="text", data="hello"),
        )

    # Optional warmup hook:
    @my_agent.setup
    async def warmup():
        pass
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ._base import FSM, Adapter, Agent, End, Policy, Review, Start, Tool

# Sentinel key to detect duplicate decorators in a single module
_DECORATOR_SENTINEL = "__perceivo_decorator_component__"

_SetupResultT = TypeVar("_SetupResultT")


def _inject_entry_class(cls: type, caller_globals: dict[str, Any]) -> None:
    """Register the generated class in the caller's module namespace."""
    if caller_globals.get(_DECORATOR_SENTINEL):
        existing = caller_globals.get("ENTRY_CLASS", "<unknown>")
        raise RuntimeError(
            f"Only one perceivo component decorator per module. '{existing}' is already defined."
        )
    caller_globals["ENTRY_CLASS"] = cls.__name__
    caller_globals[cls.__name__] = cls
    caller_globals[_DECORATOR_SENTINEL] = True


def _make_setup_decorator(
    cls: type[Any],
) -> Callable[[Callable[[], Awaitable[_SetupResultT]]], Callable[[], Awaitable[_SetupResultT]]]:
    """Create a .setup sub-decorator for warmup hooks.

    The warmup runner checks ``"setup" in type(instance).__dict__``,
    so the setup method must live directly on the generated class
    (not inherited from the base).
    """

    def setup_decorator(
        func: Callable[[], Awaitable[_SetupResultT]],
    ) -> Callable[[], Awaitable[_SetupResultT]]:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(f"@{cls.__name__}.setup must be async (use 'async def')")

        async def _setup_wrapper(self: Any) -> _SetupResultT:
            return await func()

        type.__setattr__(cls, "setup", _setup_wrapper)
        return func

    return setup_decorator


def _make_component(
    func: Callable[..., Any],
    base_class: type,
    method_name: str,
    decorator_name: str,
) -> type:
    """Build a component class from a decorated async function.

    Args:
        func: The user's ``async def`` handler.
        base_class: One of ``Agent``, ``Tool``, ``Start``, ``End``, ``FSM``,
            ``Policy``, ``Adapter``, ``Review``.
        method_name: ``"run"``, ``"start"``, or ``"end"``, the abstract method
            to implement on the generated class.
        decorator_name: Human-readable name for error messages (``"agent"``,
            ``"tool"``, etc.).
    """
    if not asyncio.iscoroutinefunction(func):
        raise TypeError(
            f"@perceivo.{decorator_name} requires an async function. "
            f"Use 'async def {func.__name__}(...)'"
        )

    # The generated method delegates to the user function, which takes no self.
    if method_name == "start":

        async def _method(self: Any, input: Any) -> Any:
            return await func(input)

        class_dict = {"start": _method}
    elif method_name == "end":

        async def _method(self: Any, input: Any) -> Any:
            return await func(input)

        class_dict = {"end": _method}
    else:

        async def _method(self: Any, input: Any) -> Any:
            return await func(input)

        class_dict = {"run": _method}

    cls = type(func.__name__, (base_class,), class_dict)
    cls.__qualname__ = func.__qualname__
    cls.__module__ = getattr(func, "__module__", "__component__")

    type.__setattr__(cls, "setup", staticmethod(_make_setup_decorator(cls)))

    # Inject ENTRY_CLASS into the caller's module globals.
    # Frame stack: 0 = _make_component, 1 = decorator fn, 2 = user module
    caller_globals = sys._getframe(2).f_globals
    _inject_entry_class(cls, caller_globals)

    return cls


def agent(func: Callable[..., Any]) -> type:
    """Define an AGENT component.

    Example::

        @perceivo.agent
        async def my_agent(input: AgentInput) -> AgentOutput:
            ...
    """
    return _make_component(func, Agent, "run", "agent")


def tool(func: Callable[..., Any]) -> type:
    """Define a TOOL component.

    Example::

        @perceivo.tool
        async def my_tool(input: ToolInput) -> ToolOutput:
            ...
    """
    return _make_component(func, Tool, "run", "tool")


def start(func: Callable[..., Any]) -> type:
    """Define a START component.

    Example::

        @perceivo.start
        async def my_start(input: StartInput) -> StartOutput:
            ...
    """
    return _make_component(func, Start, "start", "start")


def end(func: Callable[..., Any]) -> type:
    """Define an END component.

    Example::

        @perceivo.end
        async def my_end(input: EndInput) -> EndOutput:
            ...
    """
    return _make_component(func, End, "end", "end")


def fsm(func: Callable[..., Any]) -> type:
    """Define an FSM (finite state machine) component.

    Example::

        @perceivo.fsm
        async def my_fsm(input: FsmInput) -> FsmOutput:
            ...
    """
    return _make_component(func, FSM, "run", "fsm")


def policy(func: Callable[..., Any]) -> type:
    """Define a POLICY component.

    Example::

        @perceivo.policy
        async def my_policy(input: PolicyInput) -> PolicyOutput:
            ...
    """
    return _make_component(func, Policy, "run", "policy")


def adapter(func: Callable[..., Any]) -> type:
    """Define an ADAPTER component.

    Example::

        @perceivo.adapter
        async def my_adapter(input: AdapterInput) -> AdapterOutput:
            ...
    """
    return _make_component(func, Adapter, "run", "adapter")


def review(func: Callable[..., Any]) -> type:
    """Define a REVIEW component.

    Example::

        @perceivo.review
        async def my_review(input: ReviewInput) -> ReviewOutput:
            ...
    """
    return _make_component(func, Review, "run", "review")
