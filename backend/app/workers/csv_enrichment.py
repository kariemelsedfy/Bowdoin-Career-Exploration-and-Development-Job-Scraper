from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backend.app.services.url_enrichment import UrlEnrichmentService


class EmployerCsvEnrichmentWorker:
    """Processes one employer per worker task for isolated request sessions."""

    def __init__(self, service: UrlEnrichmentService, max_workers: int = 5) -> None:
        self.service = service
        self.max_workers = max_workers

    def process_file(
        self,
        input_path: str,
        output_path: str,
        employer_column: str = "employer",
        careers_url_column: str = "careers_url",
    ) -> dict[str, int]:
        rows, fieldnames = self._load_rows(input_path)
        if employer_column not in fieldnames:
            raise ValueError(
                f"Column '{employer_column}' not found. Available columns: {fieldnames}"
            )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map = {
                executor.submit(self._process_row, row, employer_column, careers_url_column): index
                for index, row in enumerate(rows)
            }

            for future in as_completed(future_map):
                index = future_map[future]
                rows[index].update(future.result())

        extra_columns = [
            careers_url_column,
            "careers_url_source",
            "careers_url_confidence",
            "careers_url_reason",
            "enrichment_status",
            "enrichment_error",
        ]
        final_fields = list(fieldnames)
        for column in extra_columns:
            if column not in final_fields:
                final_fields.append(column)

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with output_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=final_fields)
            writer.writeheader()
            writer.writerows(rows)

        return {
            "total": len(rows),
            "success": sum(1 for row in rows if row.get("enrichment_status") == "success"),
            "failed": sum(1 for row in rows if row.get("enrichment_status") == "failed"),
            "skipped": sum(
                1 for row in rows if row.get("enrichment_status") in {"skipped", "needs_setup"}
            ),
        }

    @staticmethod
    def _load_rows(path: str) -> tuple[list[dict[str, str]], list[str]]:
        encodings = ["utf-8-sig", "cp1252", "latin-1"]
        last_error: UnicodeDecodeError | None = None

        for encoding in encodings:
            try:
                with open(path, newline="", encoding=encoding) as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    fieldnames = reader.fieldnames or []
                return rows, fieldnames
            except UnicodeDecodeError as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise ValueError(f"Could not decode CSV file at '{path}'.")

    def _process_row(
        self,
        row: dict[str, str],
        employer_column: str,
        careers_url_column: str,
    ) -> dict[str, str]:
        employer_name = (row.get(employer_column) or "").strip()
        existing_url = (row.get(careers_url_column) or "").strip()
        result = self.service.enrich_employer(employer_name, seed_url=existing_url or None)

        resolved_url = result.careers_url or existing_url
        reason = result.reason
        if not result.careers_url and existing_url:
            reason = f"{reason} Existing URL preserved."

        updates: dict[str, str] = {
            careers_url_column: resolved_url,
            "careers_url_source": result.source_url or "",
            "careers_url_confidence": (
                f"{result.confidence_score:.2f}" if result.confidence_score is not None else ""
            ),
            "careers_url_reason": reason,
            "enrichment_status": result.status,
            "enrichment_error": result.error_message or "",
        }
        return updates
