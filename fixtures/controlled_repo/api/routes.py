from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/users")
def get_users():
    return [{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]


@router.post("/users")
def create_user(username: str):
    return {"id": 3, "username": username, "status": "created"}
