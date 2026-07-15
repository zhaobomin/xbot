def good(known, name):
    if name not in known:
        raise PermissionError("rejected")


def bad(known, allowed, name):
    if name not in known:
        allowed.append(name)   # anti: fail-open
