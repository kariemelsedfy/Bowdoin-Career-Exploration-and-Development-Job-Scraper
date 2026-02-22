from __future__ import annotations

import argparse

from backend.app.core.config import get_settings
from backend.app.services.url_enrichment import UrlEnrichmentService
from backend.app.workers.csv_enrichment import EmployerCsvEnrichmentWorker


def parse_args() -> argparse.Namespace:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Enrich employer CSV with likely careers page URLs using concurrent workers."
    )
    parser.add_argument("--input", default=settings.csv_input_path, help="Input employer CSV path")
    parser.add_argument(
        "--output",
        default=settings.csv_output_path,
        help="Output CSV path for enriched records",
    )
    parser.add_argument(
        "--employer-column",
        default="employer",
        help="CSV column that contains employer names",
    )
    parser.add_argument(
        "--careers-url-column",
        default="careers_url",
        help="CSV column name to write resolved careers links",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.enrichment_concurrency,
        help="Max concurrent workers (one employer per worker task)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    service = UrlEnrichmentService()
    worker = EmployerCsvEnrichmentWorker(service=service, max_workers=args.concurrency)

    summary = worker.process_file(
        input_path=args.input,
        output_path=args.output,
        employer_column=args.employer_column,
        careers_url_column=args.careers_url_column,
    )

    print("Enrichment complete")
    print(f"Total rows: {summary['total']}")
    print(f"Success: {summary['success']}")
    print(f"Failed: {summary['failed']}")
    print(f"Skipped/needs setup: {summary['skipped']}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
