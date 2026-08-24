from fastapi import FastAPI

app = FastAPI()


@app.get("/planted")
def planted(org_id: str) -> dict[str, str]:
    return {"org_id": org_id}
