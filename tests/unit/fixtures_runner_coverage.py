"""Shared component scripts for runner and CLI behavior tests."""

# ---------------------------------------------------------------------------
# Component script templates
# ---------------------------------------------------------------------------

_START = """
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput, Artifact, ArtifactType
        return StartOutput(artifact=Artifact(type=ArtifactType.TEXT, data="start"),
                           response_data={"started": True})
ENTRY_CLASS = "StartC"
"""

_START_NO_ARTIFACT = """
class StartC:
    async def start(self, inp):
        from perceivo.primitives import StartOutput
        return StartOutput(artifact=None, response_data={"started": True})
ENTRY_CLASS = "StartC"
"""

_AGENT = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        # A run gives the agent None when no node upstream produced an artifact.
        data = ((inp.artifact.data if inp.artifact is not None else None) or "") + "+agent"
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data=data),
                           response_data={"agent": True})
ENTRY_CLASS = "AgentC"
"""

_AGENT_WITH_ARTIFACTS = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        art = Artifact(type=ArtifactType.TEXT, data="main")
        return AgentOutput(artifact=art, response_data={"agent": True},
                           artifacts={"extra": Artifact(type=ArtifactType.TEXT, data="x")})
ENTRY_CLASS = "AgentC"
"""

_AGENT_SETUP_OK = """
class AgentC:
    def __init__(self):
        self.ready = False
    async def setup(self):
        self.ready = True
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="ok"))
ENTRY_CLASS = "AgentC"
"""

_AGENT_SETUP_FAIL = """
class AgentC:
    async def setup(self):
        raise RuntimeError("setup-boom")
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data="ok"))
ENTRY_CLASS = "AgentC"
"""

_AGENT_DICT_ARTIFACT = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.JSON, data={"k": 1}),
                           response_data={"rd": 2})
ENTRY_CLASS = "AgentC"
"""

_END = """
class EndC:
    async def end(self, inp):
        from perceivo.primitives import EndOutput
        return EndOutput(artifact=inp.artifact, response_data={"done": True})
ENTRY_CLASS = "EndC"
"""

_TOOL = """
class ToolC:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        return ToolOutput(response_data={"tool": True})
ENTRY_CLASS = "ToolC"
"""

_POLICY_NO_ARTIFACT = """
class PolicyC:
    async def run(self, inp):
        from perceivo.primitives import PolicyOutput
        return PolicyOutput(next_state="done", artifact=None,
                            response_data={"policy": True})
ENTRY_CLASS = "PolicyC"
"""

_TOOL_NO_ARTIFACT = """
class ToolC:
    async def run(self, inp):
        from perceivo.primitives import ToolOutput
        return ToolOutput(artifact=None, response_data={"tool": True})
ENTRY_CLASS = "ToolC"
"""

_AGENT_EMPTY = """
class AgentC:
    async def run(self, inp):
        from perceivo.primitives import AgentOutput, Artifact, ArtifactType
        return AgentOutput(artifact=Artifact(type=ArtifactType.TEXT, data=None),
                           response_data={})
ENTRY_CLASS = "AgentC"
"""

_FSM = """
class FsmC:
    async def run(self, inp):
        from perceivo.primitives import FsmOutput
        return FsmOutput(artifact=inp.artifact, response_data={"fsm": True})
ENTRY_CLASS = "FsmC"
"""

_FAILING_AGENT = """
class BadAgent:
    async def run(self, inp):
        raise ValueError("agent-boom")
ENTRY_CLASS = "BadAgent"
"""

_BAD_IMPORT = """
raise ImportError("cannot import at module load time")
"""

_NO_ENTRY = """
class Whatever:
    async def run(self, inp):
        return None
"""

_MANIFEST = "ports: []\ninput_parameters: []\nrequired_secrets: []\n"


def _write_manifests(workspace, names):
    for n in names:
        (workspace / f"{n}.yaml").write_text(_MANIFEST, encoding="utf-8")
