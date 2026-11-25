from typing import Any


class AppError(Exception):
    """An error that maps directly to an HTTP response."""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class ValidationFailed(AppError):
    """One or more field values are invalid. `errors` lists every failing field."""

    def __init__(self, errors: list[dict]):
        super().__init__(422, "validation_failed", "One or more values are invalid.", errors)
        self.errors = errors


def field_error(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def not_found(what: str) -> AppError:
    return AppError(404, "not_found", f"{what} not found.")


def conflict(code: str, message: str, details: Any = None) -> AppError:
    return AppError(409, code, message, details)


def bad_request(code: str, message: str, details: Any = None) -> AppError:
    return AppError(400, code, message, details)
