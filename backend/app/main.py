from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database.connection import SessionLocal, init_db
from .database.seed import seed_all
from .llm import flush_langfuse
from .routers import auth, bridge, config_runtime, conversations, listings, simulator
from .schemas import HealthOut


app = FastAPI(title="Prosper", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:15173",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:15173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(config_runtime.router)
app.include_router(listings.router)
app.include_router(conversations.router)
app.include_router(simulator.router)
app.include_router(bridge.router)


@app.on_event("startup")
def startup() -> None:
    init_db()
    with SessionLocal() as session:
        seed_all(session)


@app.on_event("shutdown")
def shutdown() -> None:
    flush_langfuse()


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(ok=True, app="prosper")
