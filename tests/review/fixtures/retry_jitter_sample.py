import time, random  # noqa: E401, I001


def good(attempt):
    time.sleep(2**attempt + random.random())  # clean: jittered


def bad():
    for _ in range(3):
        time.sleep(1)  # anti: fixed sleep in retry loop
