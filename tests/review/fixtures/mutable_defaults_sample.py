def good(x=None):      # clean
    pass


def bad(x=[]):         # anti: mutable default
    pass


def also_bad(y={}):    # anti
    pass
