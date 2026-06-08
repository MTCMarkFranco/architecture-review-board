"""Typed errors for the ARB Bot orchestrator."""


class WorkflowError(Exception):
    """Base."""


class ConfigError(WorkflowError):
    """Missing or invalid configuration."""


class AgentNotFoundError(WorkflowError):
    """Hosted agent does not exist in Foundry. Run create_agents.py."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        super().__init__(
            f"Foundry hosted agent '{agent_name}' not found. "
            "Run: python back-end/infra/create_agents.py"
        )


class AgentInvocationError(WorkflowError):
    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        super().__init__(f"Agent '{agent_name}' error: {message}")


class WorkflowTimeoutError(WorkflowError):
    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Workflow timed out after {timeout_seconds}s")


class CircuitOpenError(WorkflowError):
    def __init__(self, recovery_remaining: float = 0.0):
        self.recovery_remaining = recovery_remaining
        super().__init__(
            f"Circuit breaker open; recovery in {recovery_remaining:.1f}s"
        )
