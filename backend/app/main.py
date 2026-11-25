from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import forms, internal, records, v1
from app.config import get_settings
from app.errors import AppError

app = FastAPI(
    title="Atom Registry",
    version="0.1.0",
    description=(
        "Registry of atoms: data elements defined by a configurable schema, linked by typed relations, "
        "and changed only through reviewed change requests. Records hold saved answers to published forms. Authenticate with `Authorization: Bearer <token>`."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "Location"],
)


@app.exception_handler(AppError)
def handle_app_error(_: Request, exc: AppError):
    body = {"error": {"code": exc.code, "message": exc.message}}
    if exc.details is not None:
        body["error"]["details"] = exc.details
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(status_code=exc.status, content=body, headers=headers)


@app.exception_handler(RequestValidationError)
def handle_request_validation(_: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "code": e["type"], "message": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"error": {"code": "bad_request_body", "message": "The request body is invalid.", "details": details}})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(v1.router)
app.include_router(forms.router)
app.include_router(records.router)
app.include_router(internal.router)
