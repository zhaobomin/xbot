"""refute: illegal input is rejected (false positive)."""


def check(p):
    # CLEAN: reject illegal input.
    raise PermissionError("illegal input rejected")
