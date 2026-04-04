#!/usr/bin/env python3
"""
xbot memory 迁移脚本

功能：
  1. 扫描 memory 目录，显示文件状态
  2. 如果 MEMORY.md 是旧内联内容，保存为 legacy-memory.md 并加 frontmatter
  3. 给所有缺少 frontmatter 的 .md 文件统一添加 frontmatter
  4. 重建 MEMORY.md 索引

使用方法:
  python3 migrate_memory.py [workspace_path]          # dry-run
  python3 migrate_memory.py [workspace_path] --apply  # 执行
"""
import re
import sys
from datetime import datetime
from pathlib import Path


def has_frontmatter(content: str) -> bool:
    return content.strip().startswith("---")


def is_index_format(content: str) -> bool:
    lines = [l for l in content.strip().splitlines() if l.strip()]
    if not lines:
        return True
    non_index = sum(
        1 for l in lines
        if not l.strip().startswith("- [") and not l.strip().startswith("> WARNING")
    )
    return non_index <= len(lines) * 0.5


def extract_description(body: str, limit: int = 100) -> str:
    """提取干净的描述文本，去掉 markdown 格式符号。"""
    parts = []
    total = 0
    for line in body.splitlines():
        # 去掉 markdown 标记
        stripped = line.strip()
        stripped = re.sub(r"^#{1,6}\s+", "", stripped)       # 标题
        stripped = re.sub(r"^>\s*", "", stripped)             # 引用
        stripped = re.sub(r"^[-*]\s+", "", stripped)          # 列表
        stripped = re.sub(r"^\d+\.\s+", "", stripped)         # 有序列表
        stripped = re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)  # 粗体
        stripped = re.sub(r"[`*_~]", "", stripped)            # 内联格式
        stripped = stripped.strip()
        if not stripped or stripped == "---":
            continue
        needed = len(stripped) + (2 if parts else 0)
        if total + needed > limit:
            if not parts:
                return stripped[:limit - 1] + "…"
            break
        parts.append(stripped)
        total += needed
    return "; ".join(parts) if parts else "Memory topic"


def make_frontmatter(name: str, description: str, memory_type: str) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {memory_type}\n"
        f"updated_at: {now}\n"
        "---\n\n"
    )


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply_mode = "--apply" in sys.argv
    workspace = Path(args[0]) if args else Path("/home/xbot/.xbot/workspace")
    memory_dir = workspace / "memory"
    index_path = memory_dir / "MEMORY.md"

    if apply_mode:
        print("=" * 60)
        print("  APPLY 模式 - 正在执行")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  DRY RUN 模式 - 只显示，不修改")
        print("=" * 60)
    print()

    if not memory_dir.exists():
        print(f"[ERROR] Memory 目录不存在: {memory_dir}")
        return

    # ------------------------------------------------------------------
    # Step 1: 扫描
    # ------------------------------------------------------------------
    print("[Step 1] 扫描 memory 目录...\n")
    all_files = sorted(memory_dir.rglob("*.md"))
    for f in all_files:
        rel = str(f.relative_to(memory_dir))
        size = f.stat().st_size
        content = f.read_text(encoding="utf-8")
        if f.name == "MEMORY.md":
            status = "(索引文件)"
        elif has_frontmatter(content):
            status = "OK"
        else:
            status = "!! 缺少 frontmatter"
        print(f"  {rel:<40s}  {size:>6d}B  {status}")
    print()

    # ------------------------------------------------------------------
    # Step 2: 如果 MEMORY.md 是旧内联内容，保存为 legacy-memory.md
    # ------------------------------------------------------------------
    print("[Step 2] 检查 MEMORY.md...\n")
    legacy_saved = False
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        if is_index_format(content):
            print("  已经是索引格式，无需迁移。\n")
        else:
            legacy_path = memory_dir / "legacy-memory.md"
            name = "Legacy Memory"
            desc = extract_description(content)
            print(f"  包含旧内联内容 ({len(content.strip())}B)")
            print(f"  -> 保存为: legacy-memory.md")
            print(f"  -> name: {name}")
            print(f"  -> description: {desc}")
            print(f"  -> type: project")
            if apply_mode:
                if not legacy_path.exists():
                    fm = make_frontmatter(name, desc, "project")
                    legacy_path.write_text(fm + content.strip() + "\n", encoding="utf-8")
                    print("  ✓ 已保存")
                    legacy_saved = True
                else:
                    print("  ! legacy-memory.md 已存在，跳过")
            print()
    else:
        print("  MEMORY.md 不存在。\n")

    # ------------------------------------------------------------------
    # Step 3: 统一给所有缺 frontmatter 的文件添加
    # ------------------------------------------------------------------
    print("[Step 3] 统一添加 frontmatter...\n")
    # 重新扫描（Step 2 可能新增了 legacy-memory.md）
    target_files = sorted(memory_dir.rglob("*.md"))
    count = 0
    for f in target_files:
        if f.name == "MEMORY.md":
            continue
        content = f.read_text(encoding="utf-8")
        if has_frontmatter(content):
            continue
        name = f.stem.replace("_", " ").replace("-", " ").title()
        desc = extract_description(content)
        print(f"  {str(f.relative_to(memory_dir))}")
        print(f"    -> name: {name}")
        print(f"    -> description: {desc}")
        print(f"    -> type: project")
        if apply_mode:
            fm = make_frontmatter(name, desc, "project")
            f.write_text(fm + content.strip() + "\n", encoding="utf-8")
            print(f"    ✓ 已添加")
        count += 1
        print()

    if count == 0:
        print("  所有文件都已有 frontmatter。\n")

    # ------------------------------------------------------------------
    # Step 4: 重建索引
    # ------------------------------------------------------------------
    print("[Step 4] 重建 MEMORY.md 索引...\n")
    if apply_mode:
        try:
            proj_root = str(Path(__file__).resolve().parent.parent)
            if proj_root not in sys.path:
                sys.path.insert(0, proj_root)
            from xbot.memory.memdir.store import MemoryDirStore
            store = MemoryDirStore(workspace)
            store.rebuild_index()
            print("  ✓ 索引已重建\n")
            print("  新的 MEMORY.md:")
            print("  " + "-" * 50)
            for line in index_path.read_text(encoding="utf-8").splitlines():
                print(f"  {line}")
        except Exception as e:
            print(f"  [ERROR] 重建失败: {e}")
            print(f"  可以手动删除 {index_path}，下次启动会自动重建。")
    else:
        print("  (dry-run 跳过)")
    print()

    if not apply_mode:
        print("=" * 60)
        print("  以上是 dry-run 预览。确认后运行:")
        print(f"  python3 {sys.argv[0]} {workspace} --apply")
        print()
        print("  apply 后如需调整 type 或 description，")
        print("  直接编辑对应文件的 frontmatter 然后重新运行即可。")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  迁移完成！")
        print("  如需调整 type 或 description，直接编辑文件的")
        print("  frontmatter，然后重新运行此脚本更新索引。")
        print("=" * 60)


if __name__ == "__main__":
    main()
