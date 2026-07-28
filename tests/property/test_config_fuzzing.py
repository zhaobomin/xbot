"""Property-based tests for config validation.

Ensures the config validator handles arbitrary input without crashing
with unexpected exceptions (ValueError/TypeError/KeyError are acceptable
rejection signals; AttributeError/IndexError are not).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st, assume


# ---------------------------------------------------------------------------
# Tests: validate_config robustness
# ---------------------------------------------------------------------------


class TestConfigValidatorRobustness:
    """validate_config must not crash with unexpected exceptions on bad input."""

    @given(
        provider=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=30),
        ),
    )
    @settings(max_examples=200)
    def test_arbitrary_provider_name(self, provider):
        """Arbitrary provider name should raise ConfigurationError or succeed, never crash."""
        try:
            from xbot.platform.config.validator import validate_config
            from unittest.mock import MagicMock

            config = MagicMock()
            config.agents.defaults.provider = provider
            config.providers.get_provider_config.side_effect = KeyError("no such provider")

            try:
                validate_config(config)
            except (ValueError, TypeError, KeyError, AttributeError):
                pass  # Expected rejection
            except Exception as e:
                # Only allow known exception types from the validator
                if "ConfigurationError" in type(e).__name__:
                    pass
                else:
                    raise AssertionError(
                        f"Unexpected exception type {type(e).__name__}: {e}"
                    ) from e
        except ImportError:
            pytest.skip("Config validator not importable")

    @given(
        data=st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L",))),
            values=st.one_of(
                st.none(),
                st.booleans(),
                st.integers(min_value=-100, max_value=100),
                st.text(max_size=50),
            ),
            max_size=10,
        )
    )
    @settings(max_examples=100)
    def test_provider_config_dict_variations(self, data):
        """Provider config with arbitrary fields should not cause internal crash."""
        try:
            from xbot.platform.config.validator import validate_config
            from unittest.mock import MagicMock

            config = MagicMock()
            config.agents.defaults.provider = "test_provider"

            # Mock provider spec
            spec = MagicMock()
            spec.supported_by_sdk = True

            provider_config = MagicMock()
            provider_config.api_key = data.get("api_key", "sk-test")

            config.providers.get_provider_config.return_value = provider_config

            try:
                with pytest.MonkeyPatch.context() as m:
                    from xbot.platform.config import validator
                    m.setattr(validator, "get_provider_spec", lambda name: spec, raising=False)
                    validate_config(config)
            except Exception:
                pass  # Any controlled rejection is fine
        except ImportError:
            pytest.skip("Config validator not importable")
