import os
import tempfile
import unittest

from RadishTools.src.FileExecutor.core.WriteFile import writeFileExecutor
from RadishTools.src.FileExecutor.core.write_v2.protocol import parse_edits_payload
from RadishTools.src.FileExecutor.core.write_v2.service import WriteFileV2Service


class WriteFileV2Tests(unittest.TestCase):
    def _create_temp_file(self, content: str):
        temp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        temp.write(content)
        temp.flush()
        temp.close()
        self.addCleanup(lambda: os.path.exists(temp.name) and os.remove(temp.name))
        return temp.name

    def test_v1_v2_replace_consistency(self):
        file_a = self._create_temp_file("a\nb\nc\n")
        file_b = self._create_temp_file("a\nb\nc\n")
        edits = [{"op": "replace", "s": 2, "e": 2, "t": "bb"}]

        v1 = writeFileExecutor.from_payload(file_path=file_a, edits_payload=edits).execute()
        v2 = WriteFileV2Service().execute(file_path=file_b, edits=edits)

        self.assertTrue(v1["ok"])
        self.assertTrue(v2["ok"])
        with open(file_a, "r", encoding="utf-8") as fa, open(file_b, "r", encoding="utf-8") as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_conflict_contract(self):
        file_path = self._create_temp_file("x\ny\nz\n")
        edits = [
            {
                "op": "replace",
                "start_line": 2,
                "end_line": 2,
                "new_text": "yy",
                "expected_old_lines": ["other"],
            }
        ]
        result = WriteFileV2Service().execute(file_path=file_path, edits=edits, conflict_mode="strict")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "conflict_detected")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["suggested_action"], "read_file_then_retry")

    def test_dry_run_and_patch(self):
        file_path = self._create_temp_file("hello\nworld\n")
        edits = [{"op": "replace", "s": 2, "e": 2, "t": "cursor"}]
        result = WriteFileV2Service().execute(
            file_path=file_path,
            edits=edits,
            dry_run=True,
            return_patch=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("patch", result)
        with open(file_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello\nworld\n")

    def test_protocol_compact_alias(self):
        edits = parse_edits_payload([{"op": "insert", "s": 1, "t": "line1"}])
        self.assertEqual(edits[0].start_line, 1)
        self.assertEqual(list(edits[0].new_lines), ["line1"])


if __name__ == "__main__":
    unittest.main()
