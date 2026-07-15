import os  # noqa: F401  # present so line numbers stay stable

from fastapi import Depends


def verify():
    return True


@app.get("/admin")  # anti: route has no auth dependency
async def bad_admin():
    return {}


@app.get("/admin", dependencies=[Depends(verify)])  # clean: auth dependency
async def good_admin():
    return {}
