"""Execution context management for the perceivo SDK.

Provides async-safe storage for the execution context, so SDK functions reach
the ExtensionManager without explicit passing.

The stack is a ``contextvars.ContextVar`` holding an immutable tuple, and every
push and pop calls ``ContextVar.set()``. Each asyncio task therefore changes only
its own context slot: tasks spawned by ``asyncio.gather()`` or
``asyncio.create_task()`` cannot corrupt the parent's or a sibling's stack.
"""

import contextvars
from typing import Any

# Immutable tuple; ``.set()`` on push/pop keeps each task isolated.
_context_ext_stack: contextvars.ContextVar[tuple[Any, ...]] = contextvars.ContextVar(
    "_ext_stack", default=()
)


class _ExecutionContext:
    """
    Stack-based execution context for the perceivo SDK.

    This class manages a STACK of ExtensionManager instances to support
    nested component execution (e.g., AGENT calling TOOL via perceivo.flow.invoke()).

    When a component starts, its ExtensionManager is pushed onto the stack.
    When it completes, that ExtensionManager is popped. This ensures that
    parent components (like AGENT) retain their context even after child
    components (like TOOL) complete.

    The stack is stored as an immutable tuple in a ``contextvars.ContextVar``.
    Each push/pop creates a new tuple and calls ``.set()``, which only modifies
    the current task's context slot.  This ensures proper isolation when
    ``asyncio.gather()`` runs multiple coroutines concurrently.

    Usage (internal only - users should not access this):
        # Before component execution
        _ExecutionContext.set_ext(extension_manager)  # Pushes onto stack

        # During component execution
        ext = _ExecutionContext.get_ext()  # Returns top of stack
        await ext.comms.claude(...)

        # After component execution
        _ExecutionContext.clear()  # Pops from stack
    """

    @classmethod
    def set_ext(cls, ext: Any) -> None:
        """Push an ExtensionManager onto the execution context stack.

        Called by the orchestration executor before running a component. Supports
        nested calls (AGENT to TOOL) by maintaining a stack.

        Args:
            ext: The ExtensionManager instance for this execution.
        """
        _context_ext_stack.set((*_context_ext_stack.get(()), ext))

    @classmethod
    def get_ext(cls) -> Any:
        """
        Get the ExtensionManager for the current execution context.

        Returns the top of the stack (most recently pushed context).

        Raises:
            RuntimeError: If called outside of component execution context.

        Returns:
            The ExtensionManager instance for this execution.
        """
        stack = _context_ext_stack.get(())
        if not stack:
            raise RuntimeError(
                "perceivo SDK is not available outside component execution. "
                "These APIs are only available inside component methods like run()."
            )
        return stack[-1]

    @classmethod
    def clear(cls) -> None:
        """
        Pop the most recent execution context from the stack.

        Called by the orchestration executor after a component completes,
        whether it succeeded or raised an exception. This restores the
        parent component's context (if any).
        """
        stack = _context_ext_stack.get(())
        if stack:
            _context_ext_stack.set(stack[:-1])

    @classmethod
    def is_active(cls) -> bool:
        """
        Check if an execution context is currently active.

        Returns:
            True if there's at least one ExtensionManager on the stack, False otherwise.
        """
        return len(_context_ext_stack.get(())) > 0

    @classmethod
    def depth(cls) -> int:
        """
        Get the current nesting depth of execution contexts.

        Returns:
            The number of ExtensionManagers currently on the stack.
        """
        return len(_context_ext_stack.get(()))

    @classmethod
    def clear_all(cls) -> None:
        """Clear all execution contexts.

        Use only in tests or error recovery, not during component execution.
        """
        _context_ext_stack.set(())
