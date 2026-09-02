#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv


JOB_INVENTORY_FILE = Path(
    "output/job_notebook_inventory.csv"
)

NOTEBOOK_BACKLOG_FILE = Path(
    "output/notebook_migration_backlog.csv"
)

OUTPUT_FILE = Path(
    "output/job_migration_readiness.csv"
)


def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def read_csv(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        return list(csv.DictReader(f))


def unique(values):
    result = []
    seen = set()

    for value in values:
        value = clean(value)

        if not value:
            continue

        key = value.casefold()

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def unique_join(values):
    return " | ".join(unique(values))


def split_multi(value):
    value = clean(value)

    if not value:
        return []

    return [
        item.strip()
        for item in value.split("|")
        if item.strip()
    ]


def main():
    required = [
        JOB_INVENTORY_FILE,
        NOTEBOOK_BACKLOG_FILE,
    ]

    missing = [
        str(path)
        for path in required
        if not path.exists()
    ]

    if missing:
        print(
            "ERROR: faltan archivos requeridos:"
        )

        for path in missing:
            print(f" - {path}")

        raise SystemExit(1)

    job_rows = read_csv(
        JOB_INVENTORY_FILE
    )

    backlog_rows = read_csv(
        NOTEBOOK_BACKLOG_FILE
    )

    backlog_by_notebook = {
        normalize(
            row.get("notebook")
        ): row
        for row in backlog_rows
        if clean(
            row.get("notebook")
        )
    }

    job_notebooks = defaultdict(set)

    for row in job_rows:
        job = clean(
            row.get("job")
        )

        notebook = clean(
            row.get("notebook")
        )

        if job and notebook:
            job_notebooks[
                job
            ].add(notebook)

    output_rows = []

    for job, notebooks in sorted(
        job_notebooks.items(),
        key=lambda item: item[0].casefold(),
    ):
        notebooks_sorted = sorted(
            notebooks,
            key=str.casefold,
        )

        missing_backlog = []
        pending_notebooks = []

        table_changes = []
        working_table_changes = []
        storage_changes = []
        config_changes = []
        hardcode_changes = []
        secret_reviews = []
        manual_reviews = []
        notes = []

        total_notebook_changes = 0

        for notebook in notebooks_sorted:
            backlog = backlog_by_notebook.get(
                normalize(notebook)
            )

            if backlog is None:
                missing_backlog.append(
                    notebook
                )
                continue

            if (
                clean(
                    backlog.get(
                        "migration_ready"
                    )
                )
                == "NO"
            ):
                pending_notebooks.append(
                    notebook
                )

            try:
                total_notebook_changes += int(
                    clean(
                        backlog.get(
                            "total_changes"
                        )
                    )
                    or "0"
                )
            except Exception:
                pass

            table_changes.extend(
                split_multi(
                    backlog.get(
                        "table_changes"
                    )
                )
            )

            working_table_changes.extend(
                split_multi(
                    backlog.get(
                        "working_table_changes"
                    )
                )
            )

            storage_changes.extend(
                split_multi(
                    backlog.get(
                        "storage_changes"
                    )
                )
            )

            config_changes.extend(
                split_multi(
                    backlog.get(
                        "config_changes"
                    )
                )
            )

            hardcode_changes.extend(
                split_multi(
                    backlog.get(
                        "hardcode_changes"
                    )
                )
            )

            secret_reviews.extend(
                split_multi(
                    backlog.get(
                        "secret_reviews"
                    )
                )
            )

            manual_reviews.extend(
                split_multi(
                    backlog.get(
                        "manual_reviews"
                    )
                )
            )

            notes.extend(
                split_multi(
                    backlog.get(
                        "notes"
                    )
                )
            )

        if missing_backlog:
            readiness = (
                "REVIEW_REQUIRED"
            )

            blocking_reason = (
                "Hay notebooks del job sin registro "
                "en notebook_migration_backlog.csv."
            )

        elif pending_notebooks:
            readiness = (
                "REQUIRES_IMPLEMENTATION"
            )

            blocking_reason = (
                "Uno o más notebooks funcionales "
                "requieren cambios de migración."
            )

        else:
            readiness = (
                "READY"
            )

            blocking_reason = ""

        output_rows.append({
            "job":
                job,

            "notebooks_total":
                len(notebooks_sorted),

            "notebooks_ready":
                (
                    len(notebooks_sorted)
                    - len(pending_notebooks)
                    - len(missing_backlog)
                ),

            "notebooks_requiring_changes":
                len(
                    pending_notebooks
                ),

            "notebooks_missing_backlog":
                len(
                    missing_backlog
                ),

            "pending_notebooks":
                unique_join(
                    pending_notebooks
                ),

            "missing_backlog_notebooks":
                unique_join(
                    missing_backlog
                ),

            "table_changes":
                unique_join(
                    table_changes
                ),

            "working_table_changes":
                unique_join(
                    working_table_changes
                ),

            "storage_changes":
                unique_join(
                    storage_changes
                ),

            "config_changes":
                unique_join(
                    config_changes
                ),

            "hardcode_changes":
                unique_join(
                    hardcode_changes
                ),

            "secret_reviews":
                unique_join(
                    secret_reviews
                ),

            "manual_reviews":
                unique_join(
                    manual_reviews
                ),

            "total_notebook_changes":
                total_notebook_changes,

            "job_readiness":
                readiness,

            "blocking_reason":
                blocking_reason,

            "notes":
                unique_join(
                    notes
                ),
        })

    status_order = {
        "REVIEW_REQUIRED": 1,
        "REQUIRES_IMPLEMENTATION": 2,
        "READY": 3,
    }

    output_rows.sort(
        key=lambda row: (
            status_order.get(
                row[
                    "job_readiness"
                ],
                99,
            ),
            -int(
                row[
                    "notebooks_requiring_changes"
                ]
            ),
            normalize(
                row[
                    "job"
                ]
            ),
        )
    )

    fieldnames = [
        "job",
        "notebooks_total",
        "notebooks_ready",
        "notebooks_requiring_changes",
        "notebooks_missing_backlog",
        "pending_notebooks",
        "missing_backlog_notebooks",
        "table_changes",
        "working_table_changes",
        "storage_changes",
        "config_changes",
        "hardcode_changes",
        "secret_reviews",
        "manual_reviews",
        "total_notebook_changes",
        "job_readiness",
        "blocking_reason",
        "notes",
    ]

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(
            output_rows
        )

    status_counter = Counter(
        row[
            "job_readiness"
        ]
        for row
        in output_rows
    )

    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 20"
    )
    print(
        "READINESS DE MIGRACION POR JOB"
    )
    print("=" * 72)
    print()

    print(
        f"Jobs analizados                  : "
        f"{len(output_rows)}"
    )

    print()

    print(
        "Resumen por estado:"
    )

    for status in [
        "READY",
        "REQUIRES_IMPLEMENTATION",
        "REVIEW_REQUIRED",
    ]:
        print(
            f" - {status:<30}: "
            f"{status_counter.get(status, 0)}"
        )

    print()
    print(
        "Jobs que requieren implementación:"
    )

    pending_jobs = [
        row
        for row in output_rows
        if row[
            "job_readiness"
        ]
        == "REQUIRES_IMPLEMENTATION"
    ]

    if pending_jobs:
        for row in pending_jobs:
            print(
                f" - {row['job']} "
                f"| notebooks pendientes="
                f"{row['notebooks_requiring_changes']} "
                f"| cambios="
                f"{row['total_notebook_changes']}"
            )
    else:
        print(" - Ninguno")

    review_jobs = [
        row
        for row in output_rows
        if row[
            "job_readiness"
        ]
        == "REVIEW_REQUIRED"
    ]

    if review_jobs:
        print()
        print(
            "Jobs que requieren revisión técnica:"
        )

        for row in review_jobs:
            print(
                f" - {row['job']} "
                f"| notebooks sin backlog="
                f"{row['notebooks_missing_backlog']}"
            )

    print()
    print(
        f"Archivo generado: "
        f"{OUTPUT_FILE}"
    )
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
