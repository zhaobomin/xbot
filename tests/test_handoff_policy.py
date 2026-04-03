"""Tests for handoff observability helpers."""

import pytest

from xbot.agent.capabilities.handoff import HandoffAgentPolicy, HandoffPolicy


class TestHandoffAgentPolicy:
    """Tests for HandoffAgentPolicy dataclass."""

    def test_create(self) -> None:
        """Test creating a handoff agent policy."""
        policy = HandoffAgentPolicy(
            name="coder",
            description="Code assistant",
            when="user asks about code",
            prompt="You are a coding assistant.",
        )
        assert policy.name == "coder"
        assert policy.description == "Code assistant"
        assert policy.when == "user asks about code"
        assert policy.prompt == "You are a coding assistant."


class TestHandoffPolicy:
    """Tests for HandoffPolicy observability helpers."""

    @pytest.fixture
    def empty_policy(self) -> HandoffPolicy:
        """Create an empty policy."""
        return HandoffPolicy(None)

    @pytest.fixture
    def policy_with_agents(self) -> HandoffPolicy:
        """Create a policy with agents."""
        agents = {
            "coder": {
                "description": "Coding assistant",
                "when": "code-related tasks",
                "prompt": "You are a coder.",
            },
            "researcher": {
                "description": "Research assistant",
                "when": "research tasks",
                "prompt": "You are a researcher.",
            },
        }
        return HandoffPolicy(agents)

    def test_empty_policy(self, empty_policy: HandoffPolicy) -> None:
        """Test empty policy behavior."""
        assert empty_policy.has_agents() is False
        assert empty_policy.list_agents() == []

    def test_has_agents(self, policy_with_agents: HandoffPolicy) -> None:
        """Test has_agents method."""
        assert policy_with_agents.has_agents() is True

    def test_list_agents(self, policy_with_agents: HandoffPolicy) -> None:
        """Test list_agents method."""
        agents = policy_with_agents.list_agents()
        assert len(agents) == 2
        names = [a.name for a in agents]
        assert "coder" in names
        assert "researcher" in names

    def test_list_agents_normalizes_none_fields_on_objects(self) -> None:
        class _Agent:
            description = None
            when = None
            prompt = None

        policy = HandoffPolicy({"coder": _Agent()})

        agents = policy.list_agents()

        assert agents == [HandoffAgentPolicy(name="coder", description="", when="", prompt="")]

    def test_classify_task_event(self, policy_with_agents: HandoffPolicy) -> None:
        """Test classify_task_event method."""
        assert policy_with_agents.classify_task_event("handoff to coder") == "handoff"
        assert policy_with_agents.classify_task_event("regular task") is None

    def test_classify_task_event_by_agent_name(self, policy_with_agents: HandoffPolicy) -> None:
        """Named specialist agents still classify as observable handoffs."""
        assert policy_with_agents.classify_task_event("coder completed work") == "handoff"

    def test_classify_task_event_does_not_flag_generic_agent_word(self, policy_with_agents: HandoffPolicy) -> None:
        """Generic agent wording should not create false-positive handoff traces."""
        assert policy_with_agents.classify_task_event("main agent continuing request") is None

    def test_format_task_trace(self, policy_with_agents: HandoffPolicy) -> None:
        """Only factual SDK task events should render handoff trace text."""
        trace = policy_with_agents.format_task_trace("handoff to coder")
        assert trace is not None
        assert trace == "Handoff: handoff to coder"

        trace = policy_with_agents.format_task_trace("regular task")
        assert trace is None
