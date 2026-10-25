#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clitodo 的单元测试：直接跑 `python test_todo.py` 就行，不需要装 pytest。"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import todo


class ClitodoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "todos.json"
        self._old_db = os.environ.get("CLITODO_DB")
        os.environ["CLITODO_DB"] = str(self.db)
        todo.C.enabled = False  # 测试时关掉颜色，方便断言文本

    def tearDown(self):
        if self._old_db is None:
            os.environ.pop("CLITODO_DB", None)
        else:
            os.environ["CLITODO_DB"] = self._old_db
        self.tmp.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = todo.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def read_db(self):
        return json.loads(self.db.read_text(encoding="utf-8"))

    # ---------------- 基础读写 ----------------
    def test_add_creates_file_and_increments_id(self):
        self.cli("add", "买牛奶")
        self.cli("add", "写周报")
        data = self.read_db()
        self.assertEqual([i["id"] for i in data["items"]], [1, 2])
        self.assertEqual(data["next_id"], 3)
        self.assertEqual(data["items"][0]["title"], "买牛奶")

    def test_list_hides_done_by_default(self):
        self.cli("add", "A")
        self.cli("add", "B")
        self.cli("done", "1")
        _, out, _ = self.cli("ls")
        self.assertNotIn("A", out)
        self.assertIn("B", out)
        _, out_all, _ = self.cli("ls", "--all")
        self.assertIn("A", out_all)

    def test_done_then_undone(self):
        self.cli("add", "A")
        self.cli("done", "1")
        self.assertTrue(self.read_db()["items"][0]["done"])
        self.assertIsNotNone(self.read_db()["items"][0]["done_at"])
        self.cli("undone", "1")
        self.assertFalse(self.read_db()["items"][0]["done"])
        self.assertIsNone(self.read_db()["items"][0]["done_at"])

    def test_rm_removes_and_exits_nonzero_when_missing(self):
        self.cli("add", "A")
        self.cli("rm", "1")
        self.assertEqual(self.read_db()["items"], [])
        code, _, err = self.cli("rm", "9")
        self.assertEqual(code, 1)
        self.assertIn("找不到", err)

    def test_edit_changes_fields(self):
        self.cli("add", "A")
        self.cli("edit", "1", "B", "-p", "high", "-d", "2026-12-01")
        item = self.read_db()["items"][0]
        self.assertEqual(item["title"], "B")
        self.assertEqual(item["priority"], "high")
        self.assertEqual(item["due"], "2026-12-01")

    # ---------------- 参数解析 ----------------
    def test_id_range_and_multiple(self):
        for i in range(5):
            self.cli("add", f"任务{i}")
        self.cli("done", "1", "3-4")
        done = [i["id"] for i in self.read_db()["items"] if i["done"]]
        self.assertEqual(done, [1, 3, 4])

    def test_parse_date_variants(self):
        self.assertEqual(todo.parse_date("2026-11-01"), "2026-11-01")
        self.assertEqual(todo.parse_date("2026/11/01"), "2026-11-01")
        self.assertEqual(
            todo.parse_date("tomorrow"),
            (date.today() + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(todo.parse_date("今天"), date.today().isoformat())
        with self.assertRaises(Exception):
            todo.parse_date("不是日期")

    def test_priority_alias_including_chinese(self):
        self.cli("add", "A", "-p", "高")
        self.assertEqual(self.read_db()["items"][0]["priority"], "high")
        code, _, _ = self.cli("add", "B", "-p", "urget")
        self.assertEqual(code, 2)

    # ---------------- 输出格式 ----------------
    def test_json_output_is_valid(self):
        self.cli("add", "带空格 的 内容", "-p", "low")
        _, out, _ = self.cli("ls", "--json")
        items = json.loads(out)
        self.assertEqual(items[0]["title"], "带空格 的 内容")
        self.assertEqual(items[0]["priority"], "low")

    def test_search_matches_substring(self):
        self.cli("add", "看《CLI 设计》")
        self.cli("add", "买牛奶")
        _, out, _ = self.cli("search", "设计")
        self.assertIn("CLI", out)
        self.assertNotIn("牛奶", out)

    def test_corrupted_file_reports_error(self):
        self.db.write_text("{ 这不是 json", encoding="utf-8")
        code, _, err = self.cli("ls")
        self.assertEqual(code, 1)
        self.assertIn("读取数据文件失败", err)

    def test_stats_counts(self):
        self.cli("add", "A")
        self.cli("add", "B")
        self.cli("done", "1")
        _, out, _ = self.cli("stats")
        self.assertIn("总计 2", out)
        self.assertIn("已完成 1", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
