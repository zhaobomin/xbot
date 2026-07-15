def resolve_agent_tools(names, available, has_mcp):
    allowed = []
    for name in names:
        canonical = name
        if canonical in available:
            allowed.append(canonical)
            continue
        # Unknown tool — admitted instead of rejected (fail-open).
        if canonical not in available:
            allowed.append(canonical)
    return allowed
