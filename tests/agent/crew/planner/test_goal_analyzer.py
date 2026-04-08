"""Tests for the GoalAnalyzer module."""

import pytest

from xbot.crew.planner.goal_analyzer import GoalAnalyzer
from xbot.crew.planner.models import Capability, GoalAnalysis


class TestGoalAnalyzerInit:
    """Tests for GoalAnalyzer initialization."""

    def test_default_init(self):
        """Test default initialization."""
        analyzer = GoalAnalyzer()
        assert analyzer.llm_callable is None

    def test_with_llm_callable(self):
        """Test initialization with LLM callable."""
        def mock_llm(prompt):
            return '{"summary": "test"}'

        analyzer = GoalAnalyzer(llm_callable=mock_llm)
        assert analyzer.llm_callable is mock_llm


class TestInferCapabilities:
    """Tests for capability inference."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_infer_search_capability(self, analyzer):
        """Test inference of search capability."""
        capabilities = analyzer.infer_capabilities("Search for documentation")
        assert Capability.SEARCH in capabilities

    def test_infer_analyze_capability(self, analyzer):
        """Test inference of analyze capability."""
        capabilities = analyzer.infer_capabilities("Analyze the code quality")
        assert Capability.ANALYZE in capabilities

    def test_infer_write_code_capability(self, analyzer):
        """Test inference of write_code capability."""
        capabilities = analyzer.infer_capabilities("Implement a new feature")
        assert Capability.WRITE_CODE in capabilities

    def test_infer_test_capability(self, analyzer):
        """Test inference of test capability."""
        capabilities = analyzer.infer_capabilities("Test the module")
        assert Capability.TEST in capabilities

    def test_infer_debug_capability(self, analyzer):
        """Test inference of debug capability."""
        capabilities = analyzer.infer_capabilities("Debug the error")
        assert Capability.DEBUG in capabilities

    def test_infer_document_capability(self, analyzer):
        """Test inference of document capability."""
        capabilities = analyzer.infer_capabilities("Write documentation")
        assert Capability.DOCUMENT in capabilities

    def test_infer_multiple_capabilities(self, analyzer):
        """Test inference of multiple capabilities."""
        capabilities = analyzer.infer_capabilities("Search and analyze the code")
        assert Capability.SEARCH in capabilities
        assert Capability.ANALYZE in capabilities

    def test_infer_defaults_to_analyze(self, analyzer):
        """Test that unknown goals default to ANALYZE."""
        capabilities = analyzer.infer_capabilities("Something completely unknown")
        assert Capability.ANALYZE in capabilities

    def test_infer_chinese_keywords(self, analyzer):
        """Test Chinese keyword matching."""
        capabilities = analyzer.infer_capabilities("搜索相关信息")
        assert Capability.SEARCH in capabilities

        capabilities = analyzer.infer_capabilities("分析代码质量")
        assert Capability.ANALYZE in capabilities


class TestInferComplexity:
    """Tests for complexity inference."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_simple_indicators(self, analyzer):
        """Test simple complexity indicators."""
        assert analyzer.infer_complexity("Quick fix") == "simple"
        assert analyzer.infer_complexity("Simple task") == "simple"
        assert analyzer.infer_complexity("Just do it") == "simple"

    def test_complex_indicators(self, analyzer):
        """Test complex complexity indicators."""
        assert analyzer.infer_complexity("Design the system architecture") == "complex"
        assert analyzer.infer_complexity("Integrate multiple systems") == "complex"

    def test_medium_default(self, analyzer):
        """Test medium as default complexity."""
        assert analyzer.infer_complexity("Do the task") == "medium"
        assert analyzer.infer_complexity("Regular operation") == "medium"

    def test_chinese_complexity(self, analyzer):
        """Test Chinese complexity keywords."""
        assert analyzer.infer_complexity("简单的任务") == "simple"
        assert analyzer.infer_complexity("复杂的系统架构") == "complex"


class TestGenerateName:
    """Tests for crew name generation."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_generate_name_from_goal(self, analyzer):
        """Test name generation from English goal."""
        name = analyzer.generate_name("Build a new feature")
        assert name == "build_a_new_feature"
        assert len(name) <= 30

    def test_generate_name_limits_length(self, analyzer):
        """Test name length limiting."""
        name = analyzer.generate_name(
            "This is a very long goal description that should be truncated"
        )
        assert len(name) <= 30

    def test_generate_name_handles_special_chars(self, analyzer):
        """Test handling of special characters."""
        name = analyzer.generate_name("Test@#$%Goal")
        # Special chars should be removed
        assert "@" not in name
        assert "#" not in name

    def test_generate_name_default(self, analyzer):
        """Test default name for empty goal."""
        name = analyzer.generate_name("")
        assert name == "dynamic_crew"

    def test_generate_name_chinese(self, analyzer):
        """Test name generation with Chinese characters."""
        name = analyzer.generate_name("分析代码质量")
        # Should handle Chinese (may be empty or contain converted chars)
        assert isinstance(name, str)
        assert len(name) <= 30

    def test_generate_name_leading_digits(self, analyzer):
        """Test name generation with leading digits."""
        name = analyzer.generate_name("123 fix the bug")
        # Should not start with a digit
        assert not name[0].isdigit()


class TestAnalyze:
    """Tests for the analyze method."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_analyze_simple_goal(self, analyzer):
        """Test analysis of a simple goal."""
        analysis = analyzer.analyze("Quick fix the bug")
        assert isinstance(analysis, GoalAnalysis)
        assert analysis.complexity == "simple"
        assert Capability.DEBUG in analysis.required_capabilities

    def test_analyze_complex_goal(self, analyzer):
        """Test analysis of a complex goal."""
        analysis = analyzer.analyze(
            "Design and implement a system architecture for multiple services"
        )
        assert isinstance(analysis, GoalAnalysis)
        assert analysis.complexity == "complex"

    def test_analyze_with_context(self, analyzer):
        """Test analysis with context."""
        context = {"workspace": "/tmp", "project_type": "python"}
        analysis = analyzer.analyze("Analyze code", context)
        assert isinstance(analysis, GoalAnalysis)

    def test_analyze_with_llm(self):
        """Test analysis with LLM callable."""
        def mock_llm(prompt):
            return '''
            {
                "summary": "Test analysis",
                "required_capabilities": ["search", "analyze"],
                "complexity": "medium",
                "estimated_tasks": 3,
                "suggested_process": "sequential",
                "constraints": []
            }
            '''

        analyzer = GoalAnalyzer(llm_callable=mock_llm)
        analysis = analyzer.analyze("Search and analyze")
        assert analysis.summary == "Test analysis"
        assert Capability.SEARCH in analysis.required_capabilities

    def test_analyze_llm_failure_fallback(self):
        """Test fallback when LLM fails."""
        def failing_llm(prompt):
            raise Exception("LLM error")

        analyzer = GoalAnalyzer(llm_callable=failing_llm)
        # Should fall back to heuristic analysis
        analysis = analyzer.analyze("Search for bugs")
        assert isinstance(analysis, GoalAnalysis)
        assert Capability.SEARCH in analysis.required_capabilities


class TestParseLLMResponse:
    """Tests for LLM response parsing."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_parse_valid_json(self, analyzer):
        """Test parsing valid JSON response."""
        response = '''
        {
            "summary": "Test",
            "required_capabilities": ["analyze"],
            "complexity": "medium",
            "estimated_tasks": 2,
            "suggested_process": "sequential",
            "constraints": []
        }
        '''
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.summary == "Test"
        assert Capability.ANALYZE in analysis.required_capabilities

    def test_parse_json_with_surrounding_text(self, analyzer):
        """Test parsing JSON embedded in text."""
        response = '''
        Let me analyze this:
        {
            "summary": "Embedded",
            "required_capabilities": [],
            "complexity": "simple"
        }
        That's my analysis.
        '''
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.summary == "Embedded"

    def test_parse_invalid_json(self, analyzer):
        """Test handling of invalid JSON."""
        response = "Not valid JSON at all"
        analysis = analyzer.parse_llm_response(response)
        assert analysis is None

    def test_parse_partial_json(self, analyzer):
        """Test handling of partial JSON."""
        response = '{"summary": "Incomplete'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is None


class TestCapabilityKeywords:
    """Tests for capability keyword mappings."""

    def test_capability_keywords_defined(self):
        """Test that capability keywords are defined."""
        assert Capability.SEARCH in GoalAnalyzer.CAPABILITY_KEYWORDS
        assert Capability.ANALYZE in GoalAnalyzer.CAPABILITY_KEYWORDS
        assert Capability.WRITE_CODE in GoalAnalyzer.CAPABILITY_KEYWORDS

    def test_keywords_include_chinese(self):
        """Test that keywords include Chinese translations."""
        assert "搜索" in GoalAnalyzer.CAPABILITY_KEYWORDS[Capability.SEARCH]
        assert "分析" in GoalAnalyzer.CAPABILITY_KEYWORDS[Capability.ANALYZE]


class TestComplexityKeywords:
    """Tests for complexity keyword mappings."""

    def test_complex_keywords_defined(self):
        """Test that complexity keywords are defined."""
        assert len(GoalAnalyzer.COMPLEX_KEYWORDS) > 0
        assert len(GoalAnalyzer.SIMPLE_KEYWORDS) > 0

    def test_keywords_include_chinese(self):
        """Test that complexity keywords include Chinese."""
        assert "复杂" in GoalAnalyzer.COMPLEX_KEYWORDS
        assert "简单" in GoalAnalyzer.SIMPLE_KEYWORDS
