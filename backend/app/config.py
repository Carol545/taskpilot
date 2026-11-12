"""All settings come from environment variables. Nothing secret lives in the repo."""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}. See .env.example.")
    return value


class Settings:
    database_url: str = _required("DATABASE_URL")
    jwt_secret: str = _required("JWT_SECRET")
    access_token_minutes: int = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
    groq_api_key: str = _required("GROQ_API_KEY")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    # gpt-oss models think before answering; "low" is plenty for picking from a few tools and much faster.
    # Leave empty for models that don't support it.
    groq_reasoning_effort: str | None = (
        os.getenv("GROQ_REASONING_EFFORT", "low" if os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").startswith("openai/gpt-oss") else "")
        or None
    )
    cors_origins: list[str] = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
    ]


settings = Settings()
