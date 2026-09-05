from __future__ import annotations

import unittest

from pdf2zh.doclayout import can_cache_optimized_graph


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


if __name__ == "__main__":
    unittest.main()
