#!/usr/bin/env python3
"""
xbot memory 迁移辅助脚本

功能：
  1. 扫描 memory 目录，显示文件状态
  2. 如果 MEMORY.md 是旧的内联内容，备份到 workspace 根目录
  3. 重建 MEMORY.md 索引

不会自动添加 frontmatter 或修改任何 memory 文件内容。
frontmatter 格式参考（需要你手动添加到每个 .md 文件开头）：

  ---
  name: 文件显示名称
  description: 一句话描述，用于关键词匹配
  type: user|project|entity
  updated_at: 2026-04-05T00:00:00
  ---

使用方法:
  python3 migrate_memory.py [workspace_path]          # dry-run
  python3 migrate_memory.py [workspace_path] --apply  # 执行
"""
import sys
from pathlib import Path


def has_frontmatter(content: str) -> bool:
    return content.strip().startswith("---")


def is_index_format(content: str) -> bool:
    """判断内容是否是新格式的索引（每行都是 '- [' 或 '> WARNING'）"""
    lines = [l for l in content.strip().splitlines() if l.strip()]
    if not lines:
        return True  # 空文件视为有效索引
    non_index = sum(
        1 for l in lines
        if not l.strip().startswith("- [") and not l.strip().startswith("> WARNING")
    )
    return non_index <= len(lines) * 0.5


def main():
    # 解析参数
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
    need_frontmatter = []
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
            need_frontmatter.append(rel)
        print(f"  {rel:<40s}  {size:>6d}B  {status}")
    print()

    if need_frontmatter:
        print("  以下文件缺少 frontmatter，请手动添加：")
        for rel in need_frontmatter:
            print(f"    - {rel}")
        print()
        print("  frontmatter 格式：")
        print("    ---")
        print("    name: 显示名称")
        print("    description: 一句话描述（用于关键词匹配召回）")
        print("    type: user|project|entity")
        print("    updated_at: 2026-04-05T00:00:00")
        print("    ---")
        print()

    # ------------------------------------------------------------------
    # Step 2: 备份旧 MEMORY.md
    # ------------------------------------------------------------------
    print("[Step 2] 检查 MEMORY.md...\n")
    if index_path.exists():
        content = index_path.read_text(encoding="utf-8")
        if is_index_format(content):
            print("  已经是索引格式，无需备份。\n")
        else:
            backup_path = workspace / "MEMORY_BACKUP.md"
            print(f"  包含旧的内联内容 ({len(content.strip())}B)")
            print(f"  备份到: {backup_path}")
            if apply_mode:
                if not backup_path.exists():
                    backup_path.write_text(content, encoding="utf-8")
                    print("  -> 已备份")
                else:
                    print("  -> MEMORY_BACKUP.md 已存在，跳过")
            print()
    else:
        print("  MEMORY.md 不存在。\n")

    # ------------------------------------------------------------------
    # Step 3: 重建索引
    # ------------------------------------------------------------------
    print("[Step 3] 重建 MEMORY.md 索引...\n")
    if apply_mode:
        try:
            proj_root = str(Path(__file__).resolve().parent.parent)
            if proj_root not in sys.path:
                sys.path.insert(0, proj_root)
            from xbot.memory.memdir.store import MemoryDirStore
            store = MemoryDirStore(workspace)
            store.rebuild_index()
            print("  -> 索引已重建\n")
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
        print("  确认无误后运行:")
        print(f"  python3 {sys.argv[0]} {workspace} --apply")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  完成！")
        if need_frontmatter:
            print("  注意：还有文件缺少 frontmatter，请手动添加后")
            print("  重新运行此脚本来更新索引。")
        print("=" * 60)


if __name__ == "__main__":
    main()
