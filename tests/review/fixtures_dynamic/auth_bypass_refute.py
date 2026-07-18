"""refute: route guarded by an auth dependency (false positive)."""
from fastapi import Depends, FastAPI, HTTPException

app = FastAPI()


def verify():
    # raises 401 when the caller is unauthenticated.
    raise HTTPException(status_code=401, detail="unauthenticated")


@app.get("/admin", dependencies=[Depends(verify)])  # CLEAN: auth dependency
async def admin():
    return {"ok": True}
