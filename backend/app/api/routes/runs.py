from uuid import uuid4

from fastapi import APIRouter, status

from backend.app.schemas.enrichment import EnrichmentRunResponse, StartEnrichmentRequest

router = APIRouter()


@router.post(
    "/enrichment",
    response_model=EnrichmentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_enrichment_run(payload: StartEnrichmentRequest) -> EnrichmentRunResponse:
    run_id = str(uuid4())
    message = (
        "Enrichment run accepted as a stub. "
        "Next step: wire this endpoint to a queue-backed worker."
    )
    return EnrichmentRunResponse(run_id=run_id, status="queued", message=message)
