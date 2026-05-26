"""Unit tests for SymbolReadGate ASRG extensions."""

import unittest

from llmServer.code_graph.gate import SymbolReadGate, neighbor_intent_summary


class TestSymbolReadGate(unittest.TestCase):
    def setUp(self):
        SymbolReadGate.get("test").clear()

    def test_skip_excludes_from_required(self):
        gate = SymbolReadGate.get("test")
        gate.record_read("target")
        gate.record_skip({"n1", "n2"}, "test-only change")
        missing = gate.required_for_write("target", {"n1"}, {"n3"})
        self.assertIn("n3", missing)
        self.assertNotIn("n1", missing)

    def test_neighbor_intent_summary(self):
        s = neighbor_intent_summary(["a", "b"], ["c"])
        self.assertIn("called_by", s)
        self.assertIn("calls", s)


if __name__ == "__main__":
    unittest.main()
