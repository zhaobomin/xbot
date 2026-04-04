#!/usr/bin/env python3
"""
xbot memory 手动迁移脚本

使用方法:
  1. 上传到服务器
  2. python3 migrate_memory.py          # 先 dry-run 看看会做什么
  3. python3 migrate_memory.py --apply  # 确认没问题后执行
"""
import sys
import os
from pathlib import Path
from datetime import datetime

WORKSPACE = Path("/home/xbot/.xbot/workspace")
MEMORY_DIR = WORKSPACE / "memory"
INDEX_PATH = MEMORY_DIR / "MEMORY.md"

DRY_RUN = "--apply" not in sys.argv


def has_frontmatter(content: str) -> bool:
    return content.strip().startswith("---")


def add_frontmatter(content: str, name: str, description: str, memory_type: str = "project") -> str:
    now = datetime.now().isoformat(timespec="seconds")
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"type: {memory_type}\n"
        f"updated_at: {now}\n"
        "---\n\n"
        f"{content.strip()}\n"
    )


def extract_description(body: str, limit: int = 120) -> str:
    """从正文提取前几行作为 description。"""
    parts = []
    total = 0
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip().strip("-").strip()
        if not stripped or stripped == "---":
            continue
        compact = " ".join(stripped.split())
        needed = len(compact) + (2 if parts else 0)
        if total + needed > limit:
            if not parts:
                return compact[: limit - 1].rstrip() + "…"
            break
        parts.append(compact)
        total += needed
    return "; ".join(parts) if parts else "Memory topic"


def main():
    if DRY_RUN:
        print("=" * 60)
        print("  DRY RUN 模式 - 只显示会做什么，不实际修改")
        print("  确认无误后运行: python3 migrate_memory.py --apply")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  APPLY 模式 - 正在执行迁移")
        print("=" * 60)
    print()

    if not MEMORY_DIR.exists():
        print(f"[ERROR] Memory 目录不存在: {MEMORY_DIR}")
        return

    # ---------------------------------------------------------------
    # Step 1: 扫描所有 .md 文件
    # ---------------------------------------------------------------
    print("[Step 1] 扫描 memory 目录...\n")
    all_files = sorted(MEMORY_DIR.rglob("*.md"))
    for f in all_files:
        rel = f.relative_to(MEMORY_DIR)
        size = f.stat().st_size
        content = f.read_text(encoding="utf-8")
        fm = "✓ 有frontmatter" if has_frontmatter(content) else "✗ 无frontmatter"
        print(f"  {str(rel):<40s}  {size:>6d}B  {fm}")
    print()

    # ---------------------------------------------------------------
    # Step 2: 给没有 frontmatter 的文件添加
    # ---------------------------------------------------------------
    print("[Step 2] 检查需要添加 frontmatter 的文件...\n")
    files_to_fix = []
    for f in all_files:
        if f.name == "MEMORY.md":
            continue
        content = f.read_text(encoding="utf-8")
        if not has_frontmatter(content):
            files_to_fix.append(f)

    if not files_to_fix:
        print("  所有文件都已有 frontmatter，跳过。\n")
    else:
        for f in files_to_fix:
            rel = f.relative_to(MEMORY_DIR)
            content = f.read_text(encoding="utf-8")
            name = f.stem.replace("_", " ").replace("-", " ").title()
            desc = extract_description(content)
            print(f"  {rel}")
            print(f"    -> name: {name}")
            print(f"    -> description: {desc}")
            print(f"    -> type: project")

            if not DRY_RUN:
                new_content = add_frontmatter(content, name, desc, "project")
                f.write_text(new_content, encoding="utf-8")
                print(f"    ✓ 已写入 frontmatter")
            print()

    # ---------------------------------------------------------------
    # Step 3: 检查 project/legacy-memory.md 的 description
    # ---------------------------------------------------------------
    legacy_path = MEMORY_DIR / "project" / "legacy-memory.md"
    if legacy_path.exists():
        print("[Step 3] 检查 legacy-memory.md 的 description...\n")
        content = legacy_path.read_text(encoding="utf-8")
        if "Migrated from old MEMORY.md" in content:
            # 提取更好的 description
            lines = content.split("---")
            body = lines[-1] if len(lines) >= 3 else content
            new_desc = extract_description(body)
            print(f"  当前 description: Migrated from old MEMORY.md")
            print(f"  建议改为:          {new_desc}")

            if not DRY_RUN:
                new_content = content.replace(
                    "description: Migrated from old MEMORY.md",
                    f"description: {new_desc}",
                )
                legacy_path.write_text(new_content, encoding="utf-8")
                print(f"  ✓ 已更新 description")
        else:
            print(f"  description 已经不是默认值，跳过。")
        print()

    # ---------------------------------------------------------------
    # Step 4: 重建 MEMORY.md 索引
    # ---------------------------------------------------------------
    print("[Step 4] 重建 MEMORY.md 索引...\n")
    if DRY_RUN:
        print("  (dry-run 跳过，apply 模式会自动重建)")
    else:
        # 直接用 xbot 的 rebuild_index
        try:
            sys.path.insert(0, str(WORKSPACE.parent.parent))
            from xbot.memory.memdir.store import MemoryDirStore
            store = MemoryDirStore(WORKSPACE)
            store.rebuild_index()
            print(f"  ✓ 索引已重建")
            print()
            print("  新的 MEMORY.md 内容:")
            print("  " + "-" * 50)
            for line in INDEX_PATH.read_text(encoding="utf-8").splitlines():
                print(f"  {line}")
        except Exception as e:
            print(f"  [WARN] 无法用 xbot 重建索引: {e}")
            print(f"  请手动删除 {INDEX_PATH}，下次启动时会自动重建。")
    print()

    # ---------------------------------------------------------------
    # Done
    # ---------------------------------------------------------------
    if DRY_RUN:
        print("=" * 60)
        print("  以上是 dry-run 结果。确认无误后运行:")
        print("  python3 migrate_memory.py --apply")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  迁移完成！重启 xbot 后生效。")
        print("=" * 60)


if __name__ == "__main__":
    main()
