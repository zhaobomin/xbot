"""refute: route guarded by an auth dependency (false positive)."""
from fastapi import Depends, FastAPI

app = FastAPI()


def verify():
    # raises when the caller is unauthenticated.
    raise PermissionError("unauthenticated")


@app.get("/admin", dependencies=[Depends(verify)])  # CLEAN: auth dependency
async def admin():
    return {"ok": True}
