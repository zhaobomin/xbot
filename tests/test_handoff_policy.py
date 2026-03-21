"""Tests for handoff policy."""

import pytest

from xbot.agent.handoff_policy import HandoffAgentPolicy, HandoffDecision, HandoffPolicy


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


class TestHandoffDecision:
    """Tests for HandoffDecision dataclass."""

    def test_main_mode(self) -> None:
        """Test main mode decision."""
        decision = HandoffDecision(mode="main", reason="no match")
        assert decision.mode == "main"
        assert decision.reason == "no match"
        assert decision.candidate_agents == ()

    def test_handoff_mode(self) -> None:
        """Test handoff mode decision with candidates."""
        decision = HandoffDecision(
            mode="native_handoff",
            reason="specialist matched",
            candidate_agents=("coder", "researcher"),
        )
        assert decision.mode == "native_handoff"
        assert len(decision.candidate_agents) == 2


class TestHandoffPolicy:
    """Tests for HandoffPolicy."""

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
        assert empty_policy.build_system_section() == ""

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

    def test_build_system_section(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_system_section method."""
        section = policy_with_agents.build_system_section()
        assert "Delegation Policy" in section
        assert "coder" in section
        assert "researcher" in section

    def test_build_agent_prompt(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_agent_prompt method."""
        prompt = policy_with_agents.build_agent_prompt("coder", "Base prompt")
        assert "specialist agent" in prompt.lower()
        assert "Base prompt" in prompt

    def test_decide_background(self, policy_with_agents: HandoffPolicy) -> None:
        """Test decide method for background tasks."""
        decision = policy_with_agents.decide("Run this in the background")
        assert decision.mode == "background"
        assert "background" in decision.reason

    def test_decide_background_async(self, policy_with_agents: HandoffPolicy) -> None:
        """Test decide method for async tasks."""
        decision = policy_with_agents.decide("Process this asynchronously")
        assert decision.mode == "background"

    def test_decide_handoff(self, policy_with_agents: HandoffPolicy) -> None:
        """Test decide method for handoff."""
        decision = policy_with_agents.decide("Help me with coding")
        assert decision.mode == "native_handoff"
        assert "coder" in decision.candidate_agents

    def test_decide_main(self, policy_with_agents: HandoffPolicy) -> None:
        """Test decide method for main agent."""
        decision = policy_with_agents.decide("Hello, how are you?")
        assert decision.mode == "main"

    def test_build_decision_trace(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_decision_trace method."""
        decision = HandoffDecision(
            mode="native_handoff",
            reason="test",
            candidate_agents=("coder",),
        )
        trace = policy_with_agents.build_decision_trace(decision)
        assert "native_handoff" in trace
        assert "coder" in trace

    def test_build_request_prefix_handoff(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_request_prefix for handoff."""
        decision = HandoffDecision(
            mode="native_handoff",
            reason="test",
            candidate_agents=("coder",),
        )
        prefix = policy_with_agents.build_request_prefix(decision)
        assert "native handoff" in prefix.lower()
        assert "coder" in prefix

    def test_build_request_prefix_background(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_request_prefix for background."""
        decision = HandoffDecision(mode="background", reason="test")
        prefix = policy_with_agents.build_request_prefix(decision)
        assert "spawn" in prefix.lower()

    def test_build_request_prefix_main(self, policy_with_agents: HandoffPolicy) -> None:
        """Test build_request_prefix for main."""
        decision = HandoffDecision(mode="main", reason="test")
        prefix = policy_with_agents.build_request_prefix(decision)
        assert "main agent" in prefix.lower()

    def test_classify_task_event(self, policy_with_agents: HandoffPolicy) -> None:
        """Test classify_task_event method."""
        assert policy_with_agents.classify_task_event("handoff to coder") == "handoff"
        assert policy_with_agents.classify_task_event("regular task") is None

    def test_format_task_trace(self, policy_with_agents: HandoffPolicy) -> None:
        """Test format_task_trace method."""
        trace = policy_with_agents.format_task_trace("handoff to coder")
        assert trace is not None
        assert "Handoff" in trace

        trace = policy_with_agents.format_task_trace("regular task")
        assert trace is None

    def test_matches_method(self) -> None:
        """Test _matches static method."""
        assert HandoffPolicy._matches("help with coding", "coding assistant")
        assert HandoffPolicy._matches("write some code", "code expert")
        assert not HandoffPolicy._matches("hello world", "coding assistant")