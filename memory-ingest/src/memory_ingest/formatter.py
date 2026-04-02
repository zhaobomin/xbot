from __future__ import annotations

from .models import MemoryQueryResponse


def format_query_pretty(result: MemoryQueryResponse) -> str:
    lines: list[str] = []
    lines.append(f"Query: {result.query}")
    lines.append(f"User: {result.user_id}")
    lines.append(f"App: {result.app_id}")
    lines.append(f"Graph: {'on' if result.enable_graph else 'off'}")
    lines.append("")

    lines.append(f"Results ({len(result.results)}):")
    if not result.results:
        lines.append("- none")
    else:
        for idx, item in enumerate(result.results, start=1):
            score = f"{item.score:.3f}" if item.score is not None else "-"
            categories = ", ".join(item.categories) if item.categories else "-"
            lines.append(f"{idx}. {item.memory}")
            lines.append(f"   score: {score}")
            lines.append(f"   categories: {categories}")
            if item.metadata:
                source_path = item.metadata.get("source_path")
                title = item.metadata.get("title")
                if source_path:
                    lines.append(f"   source_path: {source_path}")
                if title:
                    lines.append(f"   title: {title}")

    lines.append("")
    lines.append(f"Relations ({len(result.relations)}):")
    if not result.relations:
        lines.append("- none")
    else:
        for idx, relation in enumerate(result.relations, start=1):
            score = f"{relation.score:.3f}" if relation.score is not None else "-"
            lines.append(
                f"{idx}. {relation.source} --{relation.relationship}--> {relation.target} (score: {score})"
            )

    return "\n".join(lines)
