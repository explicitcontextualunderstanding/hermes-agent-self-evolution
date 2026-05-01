"""Tests for OTelPromptAdapter — TDD Phase 1."""

import json
import unittest
from unittest import mock
from pathlib import Path
from evolution.prompts.otel_adapter import OTelPromptAdapter


class TestOTelPromptAdapter(unittest.TestCase):
    """RED/GREEN tests for OTelPromptAdapter."""

    def setUp(self):
        """Set up mock environment before each test."""
        self.hermes_bin = "/fake/hermes/bin"
        self.profile = "coding"
        self.db_config = {
            "host": "127.0.0.1",
            "port": 5432,
            "user": "postgres",
            "database": "harness_evolution",
        }

    def _make_adapter(self, **kwargs):
        """Helper to instantiate adapter with test defaults."""
        return OTelPromptAdapter(
            hermes_bin=self.hermes_bin,
            profile=self.profile,
            db_config=self.db_config,
            **kwargs,
        )

    # ── 1. CANARY: Adapter can be instantiated ───────────────────────────

    def test_can_instantiate(self):
        """Test that OTelPromptAdapter can be created with minimal config."""
        adapter = self._make_adapter()
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.hermes_bin, self.hermes_bin)
        self.assertEqual(adapter.profile, self.profile)

    def test_propose_new_texts_is_none(self):
        """GEPA checks hasattr(adapter, 'propose_new_texts') — must be None."""
        adapter = self._make_adapter()
        self.assertIsNone(adapter.propose_new_texts)

    # ── 2. DIMENSIONS: Correct dimension names ───────────────────────────

    def test_default_dimensions(self):
        """Default dimension names match scoring formula."""
        adapter = self._make_adapter()
        expected = ["pass", "efficiency", "tool_efficiency", "token_efficiency", "composite"]
        self.assertEqual(adapter.dimension_names, expected)

    def test_custom_dimensions(self):
        """Custom dimension names are accepted."""
        custom = ["custom_a", "custom_b"]
        adapter = self._make_adapter(dimension_names=custom)
        self.assertEqual(adapter.dimension_names, custom)

    # ── 3. EVALUATE: Returns correct GEPA format ──────────────────────────

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_evaluate_returns_evaluationbatch(self, mock_query, mock_run):
        """evaluate() must return a tuple/list with (objective_scores, scores, trajectories)."""
        # Mock hermes subprocess: session ID in combined stdout+stderr
        mock_result = mock.MagicMock()
        mock_result.stdout = (
            "Initializing agent...\n"
            "━━━ Hermes Agent ⚕ ━━━\n"
            "Response content here\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Resume this session with: hermes --resume 20260501_092030_ad5c59\n"
        )
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Mock OTel query result
        mock_query.return_value = [
            {
                "span_id": "span_1",
                "trace_id": "trace_1",
                "parent_span_id": None,
                "name": "agent",
                "kind": "1",
                "start_time": None,
                "end_time": None,
                "duration_ms": 11562.841,
                "status_code": None,
                "status_message": None,
                "attributes": {
                    "hermes.session_id": "20260501_092030_ad5c59",
                    "hermes.turn.final_status": "completed",
                    "hermes.turn.api_call_count": 1,
                    "llm.token_count.total": 24422,
                    "hermes.session.completed": True,
                },
                "events": None,
                "links": None,
                "resource_attributes": None,
                "scope_name": None,
                "scope_version": None,
                "service_name": "hermes-agent",
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "test1", "answer": "pass"}]
        candidate = {"prompt": "Test prompt text"}

        result = adapter.evaluate(batch, candidate)

        # Check result is a tuple/list of 3 elements
        self.assertIsInstance(result, (tuple, list))
        self.assertEqual(len(result), 3)

        objective_scores, scores, trajectories = result

        # Check objective_scores: list of dicts with dimension names
        self.assertIsInstance(objective_scores, list)
        self.assertEqual(len(objective_scores), len(batch))
        for obj_score in objective_scores:
            self.assertIsInstance(obj_score, dict)
            for dim in adapter.dimension_names:
                self.assertIn(dim, obj_score)
                self.assertGreaterEqual(obj_score[dim], 0.0)
                self.assertLessEqual(obj_score[dim], 1.0)

        # Check scores: list of floats in [0, 1]
        self.assertIsInstance(scores, list)
        self.assertEqual(len(scores), len(batch))
        for s in scores:
            self.assertIsInstance(s, float)
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)

        # Check trajectories (should be None since capture_traces=False)
        self.assertIsNone(trajectories)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_evaluate_scores_in_range(self, mock_query, mock_run):
        """All score dimensions must be in [0, 1] for any status."""
        mock_result = mock.MagicMock()
        mock_result.stdout = (
            "hermes --resume 20260501_092030_ad5c59\n"
            "Some response text\n"
        )
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Test with 'completed' status
        mock_query.return_value = [
            {
                "span_id": "span_1",
                "name": "agent",
                "duration_ms": 5000.0,
                "attributes": {
                    "hermes.session_id": "20260501_092030_ad5c59",
                    "hermes.turn.final_status": "completed",
                    "hermes.turn.api_call_count": 3,
                    "llm.token_count.total": 15000,
                    "hermes.session.completed": True,
                },
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Test prompt"}
        _, scores, _ = adapter.evaluate(batch, candidate)

        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_evaluate_incomplete_status_lowers_score(self, mock_query, mock_run):
        """'incomplete' status should produce lower pass score than 'completed'."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume 20260501_incomplete\nResponse\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        mock_query.return_value = [
            {
                "span_id": "span_1",
                "name": "agent",
                "duration_ms": 5000.0,
                "attributes": {
                    "hermes.session_id": "20260501_incomplete",
                    "hermes.turn.final_status": "incomplete",
                    "hermes.turn.api_call_count": 8,
                    "llm.token_count.total": 765349,
                    "hermes.session.completed": False,
                },
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Test prompt"}
        objective_scores, scores, _ = adapter.evaluate(batch, candidate)

        # 'incomplete' should have pass=0.0
        self.assertEqual(objective_scores[0]["pass"], 0.0)
        # Composite should be lower than a completed run
        self.assertLess(objective_scores[0]["composite"], 0.5)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_evaluate_trajectories_with_capture(self, mock_query, mock_run):
        """capture_traces=True should produce trajectories list."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume 20260501_traces\nResponse\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        mock_query.return_value = [
            {
                "span_id": "span_1",
                "name": "agent",
                "duration_ms": 5000.0,
                "attributes": {
                    "hermes.session_id": "20260501_traces",
                    "hermes.turn.final_status": "completed",
                    "hermes.turn.api_call_count": 2,
                    "llm.token_count.total": 30000,
                    "hermes.session.completed": True,
                },
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "traces_test", "answer": "pass"}]
        candidate = {"prompt": "Traces-enabled test"}
        result = adapter.evaluate(batch, candidate, capture_traces=True)

        # Unpack
        objective_scores, scores, trajectories = result

        # trajectories should be a list with len == len(batch)
        self.assertIsNotNone(trajectories)
        self.assertEqual(len(trajectories), len(batch))
        for traj in trajectories:
            self.assertIn("data", traj)
            self.assertIn("full_assistant_response", traj)
            self.assertIn("feedback", traj)
            # feedback should mention the score and dimensions
            self.assertIn("score", traj["feedback"].lower())

    # ── 4. ERROR HANDLING: Degradation tests ──────────────────────────────

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    def test_evaluate_hermes_timeout_returns_zero_scores(self, mock_run):
        """Hermes timeout should return zero scores gracefully."""
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="hermes", timeout=120)

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Timeout test"}
        objective_scores, scores, trajectories = adapter.evaluate(batch, candidate)

        for s in scores:
            self.assertEqual(s, 0.0)
        self.assertEqual(objective_scores[0]["pass"], 0.0)
        self.assertIsNone(trajectories)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    def test_evaluate_hermes_crash_returns_zero_scores(self, mock_run):
        """Hermes non-zero exit should return zero scores with error trajectory."""
        mock_result = mock.MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "Hermes crashed: segfault"
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Crash test"}
        objective_scores, scores, trajectories = adapter.evaluate(
            batch, candidate, capture_traces=True
        )

        for s in scores:
            self.assertEqual(s, 0.0)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_evaluate_db_unreachable_still_returns_scores(self, mock_query, mock_run):
        """DB failure should degrade gracefully (no crash, valid scores)."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume 20260501_dbdown\nResponse\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Simulate DB failure returning empty list
        mock_query.return_value = []

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "DB down test"}
        objective_scores, scores, _ = adapter.evaluate(batch, candidate)

        # Should still return valid scores (graceful degradation)
        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)
        # Pass is 0 when no span attributes (status unknown)
        self.assertEqual(objective_scores[0]["pass"], 0.0)
        # Composite should be > 0 because duration and token efficiency contribute
        self.assertGreater(objective_scores[0]["composite"], 0.0)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    def test_evaluate_no_session_id_found(self, mock_run):
        """No session ID in output should still work (zero scores)."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "Some random output without session ID"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "No session test"}
        objective_scores, scores, _ = adapter.evaluate(batch, candidate)

        for s in scores:
            self.assertEqual(s, 0.0)
        self.assertEqual(objective_scores[0]["pass"], 0.0)

    # ── 5. SCORING: Verify scoring formula ────────────────────────────────

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_scoring_formula_completed(self, mock_query, mock_run):
        """Verify composite score matches formula for completed session."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume 20260501_scoring\nResponse\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # duration=10000ms, api_calls=2, tokens=50000
        mock_query.return_value = [
            {
                "span_id": "span_1",
                "name": "agent",
                "duration_ms": 10000.0,
                "attributes": {
                    "hermes.session_id": "20260501_scoring",
                    "hermes.turn.final_status": "completed",
                    "hermes.turn.api_call_count": 2,
                    "llm.token_count.total": 50000,
                    "hermes.session.completed": True,
                },
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Scoring test"}
        objective_scores, scores, _ = adapter.evaluate(batch, candidate)

        obj = objective_scores[0]
        # pass = 1.0 (completed)
        self.assertEqual(obj["pass"], 1.0)
        # efficiency = clip(1.0 - 10000/30000, 0, 1) = clip(1.0 - 0.333, 0, 1) = 0.667
        self.assertAlmostEqual(obj["efficiency"], 0.6667, places=3)
        # tool_efficiency = 1.0 / max(2, 1) = 0.5
        self.assertEqual(obj["tool_efficiency"], 0.5)
        # token_efficiency = clip(1.0 - 50000/100000, 0, 1) = clip(0.5, 0, 1) = 0.5
        self.assertEqual(obj["token_efficiency"], 0.5)
        # composite = 1.0*0.5 + 0.667*0.2 + 0.5*0.2 + 0.5*0.1
        # = 0.5 + 0.133 + 0.1 + 0.05 = 0.783
        expected_composite = 0.5 * 1.0 + 0.2 * (1.0 - 10000.0/30000.0) + 0.2 * (1.0/2.0) + 0.1 * (1.0 - 50000.0/100000.0)
        self.assertAlmostEqual(obj["composite"], expected_composite, places=4)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    @mock.patch("evolution.prompts.otel_adapter._query_otel_spans")
    def test_scoring_formula_timed_out(self, mock_query, mock_run):
        """Verify scores for timed_out status."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume 20260501_timeout\nResponse\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        mock_query.return_value = [
            {
                "span_id": "span_1",
                "name": "agent",
                "duration_ms": 35000.0,
                "attributes": {
                    "hermes.session_id": "20260501_timeout",
                    "hermes.turn.final_status": "timed_out",
                    "hermes.turn.api_call_count": 10,
                    "llm.token_count.total": 120000,
                    "hermes.session.completed": False,
                },
                "ingested_at": None,
            },
        ]

        adapter = self._make_adapter()
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Timed out test"}

        objective_scores, scores, _ = adapter.evaluate(batch, candidate)
        obj = objective_scores[0]

        # pass = 0.0 (not completed)
        self.assertEqual(obj["pass"], 0.0)
        # efficiency = clip(1.0 - 35000/30000, 0, 1) = clip(1.0 - 1.167, 0, 1) = 0.0
        self.assertEqual(obj["efficiency"], 0.0)
        # tool_efficiency = 1.0 / max(10, 1) = 0.1
        self.assertEqual(obj["tool_efficiency"], 0.1)
        # token_efficiency = clip(1.0 - 120000/100000, 0, 1) = clip(-0.2, 0, 1) = 0.0
        self.assertEqual(obj["token_efficiency"], 0.0)

    # ── 6. CLEANUP: cleanup_prompt support ────────────────────────────────

    def test_cleanup_prompt_not_none(self):
        """cleanup_prompt should be a callable or string."""
        adapter = self._make_adapter()
        self.assertIsNotNone(adapter.cleanup_prompt)

    @mock.patch("evolution.prompts.otel_adapter.subprocess.run")
    def test_cleanup_prompt_executes(self, mock_run):
        """_run_cleanup should call hermes with the cleanup prompt."""
        mock_result = mock.MagicMock()
        mock_result.stdout = "hermes --resume cleanup_sesh\nCleaned up\n"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        adapter = self._make_adapter()
        # Trigger cleanup by calling evaluate with cleanup=True
        batch = [{"input": "test", "answer": "pass"}]
        candidate = {"prompt": "Test"}
        adapter.evaluate(batch, candidate, cleanup=True)

        # Verify hermes was called at least twice (once for eval, once for cleanup)
        self.assertGreaterEqual(mock_run.call_count, 2)

    # ── 7. REFLECTIVE DATASET: make_reflective_dataset interface ──────────

    def test_make_reflective_dataset_exists(self):
        """Adapter must have make_reflective_dataset method."""
        adapter = self._make_adapter()
        self.assertTrue(hasattr(adapter, "make_reflective_dataset"))
        self.assertTrue(callable(adapter.make_reflective_dataset))


if __name__ == "__main__":
    unittest.main()
