# xbot Bug Report

## BUG-001: ProgressCoalescer 中文标点拼接不一致

- **模块**: `xbot/interaction/progress_coalescer.py`
- **行号**: L160
- **严重程度**: P3 (cosmetic — 中文文本拼接时可能多一个空格)
- **描述**: `_append_body()` 的 `right.startswith()` 检查了中文标点（，。！？、），但 `left.endswith()` 只检查了空格和换行。导致：
  - `"你好" + "，世界"` → `"你好，世界"` ✅ (right 以中文标点开头，不插入空格)
  - `"你好，" + "世界"` → `"你好， 世界"` ❌ (left 以中文标点结尾，但未被识别，插入了多余空格)
- **复现**: `pytest tests/test_interaction_full.py::TestProgressCoalescer::test_append_body_spacing -v`
- **发现轮次**: Phase 1
- **建议修复**: 在 `left.endswith(...)` 中增加中文标点检查：
  ```python
  if left.endswith((" ", "\n", "，", "。", "！", "？", "、")) or right.startswith((" ", "\n", ",", ".", "!", "?", "，", "。", "！", "？", "、")):
  ```

---

## BUG-002: parse_permission_response 不识别常用审批词

- **模块**: `xbot/interaction/response_parser.py`
- **行号**: L1-58
- **严重程度**: P2 (功能缺陷 — 用户输入"ok"/"sure"/"approve"时权限请求不会被批准)
- **描述**: `parse_permission_response()` 只识别 `"yes"`, `"y"`, `"allow"` 为批准，`"no"`, `"n"`, `"deny"` 为拒绝。但用户更常用的 `"ok"`, `"sure"`, `"approve"`, `"yep"` 全部返回 `None`（未识别）。同样 `"reject"`, `"nope"` 也不被识别为拒绝。
- **复现**: `pytest tests/integration/test_cross_module_flow.py::TestResponseParserIntegration::test_ambiguous_keywords_return_none -v`
- **发现轮次**: Phase 1 - 集成测试
- **建议修复**: 扩展识别词列表：
  ```python
  ALLOW_WORDS = {"yes", "y", "allow", "ok", "sure", "approve", "yep", "yeah", "go"}
  DENY_WORDS = {"no", "n", "deny", "reject", "nope", "stop", "cancel", "nah"}
  ```

---

*格式: 每发现一个 bug 就追加一条记录*
