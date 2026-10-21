from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

import numpy as np

from pdf2zh import doclayout
from pdf2zh.doclayout import (
    LayoutModelError,
    OnnxModel,
    can_cache_optimized_graph,
    has_accelerator,
    preferred_providers,
)

APPLE_SILICON = ["CoreMLExecutionProvider", "AzureExecutionProvider", "CPUExecutionProvider"]


class OptimizedGraphCacheTests(unittest.TestCase):
    def test_source_cpu_runtime_may_cache(self):
        self.assertTrue(
            can_cache_optimized_graph(["CPUExecutionProvider"], frozen=False)
        )

    def test_frozen_release_never_writes_hardware_specific_graph(self):
        self.assertFalse(
            can_cache_optimized_graph(["CPUExecutionProvider"], frozen=True)
        )

    def test_compiled_provider_is_not_serialized(self):
        self.assertFalse(
            can_cache_optimized_graph(
                ["CoreMLExecutionProvider", "CPUExecutionProvider"], frozen=False
            )
        )


class ProviderChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        backend = patch.object(doclayout, "_preferred_backend", None)
        backend.start()
        self.addCleanup(backend.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(doclayout.BACKEND_VARIABLE, None)
        available = patch.object(
            doclayout.onnxruntime, "get_available_providers", return_value=APPLE_SILICON
        )
        available.start()
        self.addCleanup(available.stop)

    def test_coreml_is_never_chosen_by_default(self):
        # Issues #16 and #32: CoreML built the session on macOS 26+ and then
        # failed every page with "Error in building plan".
        self.assertEqual(preferred_providers(), ["CPUExecutionProvider"])

    def test_a_hardware_provider_is_opt_in(self):
        os.environ[doclayout.BACKEND_VARIABLE] = "coreml"
        self.assertEqual(
            preferred_providers(), ["CoreMLExecutionProvider", "CPUExecutionProvider"]
        )

    def test_auto_restores_every_available_provider(self):
        self.assertEqual(preferred_providers("auto"), APPLE_SILICON)

    def test_unavailable_or_unknown_backend_falls_back_to_cpu(self):
        self.assertEqual(preferred_providers("cuda"), ["CPUExecutionProvider"])
        self.assertEqual(preferred_providers("tpu"), ["CPUExecutionProvider"])

    def test_remote_azure_provider_is_not_an_accelerator(self):
        self.assertFalse(has_accelerator(["AzureExecutionProvider", "CPUExecutionProvider"]))
        self.assertTrue(has_accelerator(APPLE_SILICON))


class FakeSession:
    def __init__(self, providers: list[str], outcome) -> None:
        self.providers = providers
        self.outcome = outcome
        self.runs = 0

    def get_providers(self) -> list[str]:
        return self.providers

    def run(self, _outputs, _inputs):
        self.runs += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return [self.outcome]


def model_with(session: FakeSession, cpu: FakeSession) -> OnnxModel:
    model = object.__new__(OnnxModel)
    model.model_path = "doclayout.onnx"
    model._session_lock = threading.Lock()
    model.model = session
    model._session = lambda providers: cpu
    return model


class InferenceFallbackTests(unittest.TestCase):
    def test_failed_accelerator_moves_the_rest_of_the_run_to_the_cpu(self):
        answer = np.zeros((1, 3, 6), dtype=np.float32)
        coreml = FakeSession(
            ["CoreMLExecutionProvider", "CPUExecutionProvider"],
            RuntimeError("Error executing model: Error in building plan."),
        )
        cpu = FakeSession(["CPUExecutionProvider"], answer)
        model = model_with(coreml, cpu)
        with self.assertLogs("pdf2zh.doclayout", "WARNING"):
            first = model.run(np.zeros((1, 3, 32, 32), dtype=np.float32))
        second = model.run(np.zeros((1, 3, 32, 32), dtype=np.float32))
        self.assertIs(first, answer)
        self.assertIs(second, answer)
        self.assertIs(model.model, cpu)
        self.assertEqual((coreml.runs, cpu.runs), (1, 2))

    def test_cpu_failure_is_reported_as_a_layout_model_error(self):
        cpu = FakeSession(["CPUExecutionProvider"], RuntimeError("bad input"))
        model = model_with(cpu, cpu)
        with self.assertRaises(LayoutModelError) as raised:
            model.run(np.zeros((1, 3, 32, 32), dtype=np.float32))
        self.assertIn("bad input", str(raised.exception))
        self.assertEqual(cpu.runs, 1)

    def test_accelerator_and_cpu_both_failing_is_a_layout_model_error(self):
        coreml = FakeSession(["CoreMLExecutionProvider"], RuntimeError("plan"))
        cpu = FakeSession(["CPUExecutionProvider"], RuntimeError("cpu too"))
        model = model_with(coreml, cpu)
        with self.assertLogs("pdf2zh.doclayout", "WARNING"), self.assertRaises(LayoutModelError):
            model.run(np.zeros((1, 3, 32, 32), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
