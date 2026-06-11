"""Tests for the shared Logfire telemetry wiring (issue #89)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import scaffold.telemetry as telemetry


def _reset() -> None:
    telemetry._configured = False


class TestConfigureTelemetry:
    def test_configures_and_instruments(self, monkeypatch) -> None:
        _reset()
        fake = MagicMock()
        with patch.dict("sys.modules", {"logfire": fake}):
            telemetry.configure_telemetry("scaffold-test")
        fake.configure.assert_called_once_with(
            send_to_logfire="if-token-present", service_name="scaffold-test"
        )
        fake.instrument_anthropic.assert_called_once()
        fake.instrument_pydantic_ai.assert_called_once()
        _reset()

    def test_idempotent_first_call_wins(self) -> None:
        _reset()
        fake = MagicMock()
        with patch.dict("sys.modules", {"logfire": fake}):
            telemetry.configure_telemetry("first")
            telemetry.configure_telemetry("second")
        assert fake.configure.call_count == 1
        assert fake.configure.call_args.kwargs["service_name"] == "first"
        _reset()

    def test_never_raises_when_logfire_breaks(self) -> None:
        _reset()
        broken = MagicMock()
        broken.configure.side_effect = RuntimeError("no telemetry backend")
        with patch.dict("sys.modules", {"logfire": broken}):
            telemetry.configure_telemetry("scaffold-test")  # must not raise
        assert telemetry._configured is False  # next entry point may retry
        _reset()

    def test_real_logfire_noop_without_token(self, monkeypatch) -> None:
        # End-to-end with the actual logfire package: no token configured,
        # so configure + instrument must succeed silently as a local no-op.
        _reset()
        monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
        telemetry.configure_telemetry("scaffold-test")
        assert telemetry._configured is True
        _reset()
