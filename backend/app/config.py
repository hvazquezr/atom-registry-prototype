from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Every value can be overridden with an ATOM_ environment variable."""

    model_config = SettingsConfigDict(env_prefix="ATOM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres@localhost:5432/atoms"

    # Time zone used by today() in calculated fields. now() is always a UTC timestamp.
    timezone: str = "UTC"

    # Whether the person who submitted a change request may approve it themselves.
    allow_self_approval: bool = False

    # Lifetime of tokens issued by the login endpoint (API tokens set their own expiry).
    session_token_hours: int = 12

    # Guards for calculated-field expressions.
    max_expression_length: int = 2000
    max_expression_nodes: int = 400

    # Traversal limit for the graph endpoint.
    max_graph_depth: int = 5

    # Comma-separated origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
