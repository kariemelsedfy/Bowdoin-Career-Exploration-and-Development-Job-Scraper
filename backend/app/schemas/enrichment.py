from pydantic import BaseModel, Field


class StartEnrichmentRequest(BaseModel):
    input_path: str | None = None
    output_path: str | None = None
    employer_column: str = Field(default="employer", min_length=1)
    careers_url_column: str = Field(default="careers_url", min_length=1)
    concurrency: int = Field(default=5, ge=1, le=50)


class EnrichmentRunResponse(BaseModel):
    run_id: str
    status: str
    message: str
