"""confirm: illegal input is admitted instead of rejected (real bug)."""
_admitted: list = []


def check(p):
    # BUG: appends the illegal input instead of rejecting it.
    if p not in _admitted:
        _admitted.append(p)
    return True
