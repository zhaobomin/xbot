"""confirm: protected route with no auth dependency (real bug)."""
from fastapi import FastAPI

app = FastAPI()


@app.get("/admin")  # BUG: no auth dependency
async def admin():
    return {"ok": True}
