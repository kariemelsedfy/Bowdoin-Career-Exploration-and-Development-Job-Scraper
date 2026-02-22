from pydantic import BaseModel


class EmployerBase(BaseModel):
    name: str
    careers_url: str | None = None
