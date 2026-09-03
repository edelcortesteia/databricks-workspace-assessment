#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv
import json


# ============================================================
# ASSESSMENT WORKSPACE - PASO 28
# MATRIZ FINAL PRO -> UC POR JOB
#
# Objetivo:
#   Consolidar en UNA FILA POR JOB los resultados ya validados
#   de Tool 2.
#
# Regla:
#   - NO recalcula análisis técnicos.
#   - NO vuelve a inspeccionar notebooks ni YAML.
#   - Consume únicamente outputs ya validados.
#   - El scope oficial son los jobs EXACT_NAME del Paso 22.
#
# Referencia metodológica:
#   El documento maestro de Tool 1 ya usaba una vista por job
#   con configuración PRO/UC, dependencias y readiness.
#   Este Paso 28 amplía ese concepto con TODO el assessment
#   Workspace real (pasos 05, 14, 15, 17-26).
#
# Salidas:
#   output/final_job_migration_matrix.csv
#   output/final_job_migration_matrix.json
# ============================================================


OUTPUT_DIR = Path("output")

OUTPUT_CSV = (
    OUTPUT_DIR
    / "final_job_migration_matrix.csv"
)

OUTPUT_JSON = (
    OUTPUT_DIR
    / "final_job_migration_matrix.json"
)


# ============================================================
# Fuentes oficiales
# ============================================================

SOURCES = {
    "job_matching":
        OUTPUT_DIR / "job_name_matching.csv",

    "job_notebooks":
        OUTPUT_DIR / "job_notebook_inventory.csv",

    "tables":
        OUTPUT_DIR / "table_hive_reconciliation_final.csv",

    "external_jdbc":
        OUTPUT_DIR / "external_jdbc_dependencies.csv",

    "storage":
        OUTPUT_DIR / "storage_migration_analysis.csv",

    "secrets":
        OUTPUT_DIR / "secret_usage_analysis.csv",

    "working_tables":
        OUTPUT_DIR / "dynamic_working_tables.csv",

    "notebook_backlog":
        OUTPUT_DIR / "notebook_migration_backlog.csv",

    "job_readiness":
        OUTPUT_DIR / "job_migration_readiness.csv",

    "master_actions":
        OUTPUT_DIR / "master_migration_actions.csv",

    "libraries":
        OUTPUT_DIR / "job_library_migration_analysis.csv",

    "job_configuration":
        OUTPUT_DIR / "job_configuration_migration_analysis.csv",

    "runtime_parameters":
        OUTPUT_DIR / "job_runtime_parameters_analysis.csv",

    "identity_security":
        OUTPUT_DIR / "job_identity_security_analysis.csv",

    "notifications":
        OUTPUT_DIR / "job_notifications_operation_analysis.csv",
}


# ============================================================
# Utilidades
# ============================================================

def clean(value):
    return (
        ""
        if value is None
        else str(value).strip()
    )


def norm(value):
    return clean(value).casefold()


def truth(value):
    return norm(value) in {
        "yes",
        "true",
        "1",
        "si",
        "sí",
    }


def read_csv(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        return list(
            csv.DictReader(f)
        )


def detect_field(
    rows,
    candidates,
):
    if not rows:
        return ""

    fields = set(
        rows[0].keys()
    )

    for candidate in candidates:
        if candidate in fields:
            return candidate

    return ""


def split_multi(value):
    value = clean(value)

    if not value:
        return []

    return [
        item.strip()
        for item in value.split("|")
        if item.strip()
    ]


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


def unique_join(
    values,
    separator=" | ",
):
    return separator.join(
        unique(values)
    )


def count_review_rows(rows):
    return sum(
        1
        for row in rows
        if norm(
            row.get(
                "requires_action"
            )
            or row.get(
                "requires_review"
            )
        )
        in {
            "yes",
            "review",
            "true",
        }
    )


def validate_sources():

    missing = [
        str(path)
        for path in SOURCES.values()
        if not path.exists()
    ]

    if missing:

        print(
            "ERROR: faltan outputs requeridos:"
        )

        for path in missing:
            print(
                f" - {path}"
            )

        raise SystemExit(1)


# ============================================================
# Scope EXACT_NAME
# ============================================================

def load_scope(
    matching_rows,
):

    method_field = detect_field(
        matching_rows,
        [
            "match_method",
            "matching_method",
            "method",
        ],
    )

    pro_field = detect_field(
        matching_rows,
        [
            "workspace_job",
            "pro_job",
            "job",
            "workspace_name",
        ],
    )

    uc_field = detect_field(
        matching_rows,
        [
            "uc_job",
            "matched_uc_job",
            "uc_name",
        ],
    )

    if not (
        method_field
        and pro_field
        and uc_field
    ):
        raise RuntimeError(
            "No se pudieron identificar las columnas "
            "de matching del Paso 22."
        )

    scope = {}

    for row in matching_rows:

        if norm(
            row.get(
                method_field
            )
        ) != "exact_name":
            continue

        pro_job = clean(
            row.get(
                pro_field
            )
        )

        uc_job = clean(
            row.get(
                uc_field
            )
        )

        if pro_job and uc_job:
            scope[
                pro_job
            ] = uc_job

    if not scope:
        raise RuntimeError(
            "No se encontraron jobs EXACT_NAME."
        )

    return scope


# ============================================================
# Índices genéricos por job
# ============================================================

def index_rows_by_job(
    rows,
    job_field="job",
):
    result = defaultdict(list)

    for row in rows:

        jobs = split_multi(
            row.get(
                job_field
            )
        )

        if not jobs:

            scalar = clean(
                row.get(
                    job_field
                )
            )

            if scalar:
                jobs = [scalar]

        for job in jobs:
            result[
                job
            ].append(
                row
            )

    return result


def index_actions_by_job(
    rows,
):
    result = defaultdict(list)

    for row in rows:

        jobs = split_multi(
            row.get(
                "affected_jobs"
            )
        )

        for job in jobs:
            result[
                job
            ].append(
                row
            )

    return result


# ============================================================
# Main
# ============================================================

def main():

    validate_sources()

    data = {
        name:
            read_csv(path)

        for name, path
        in SOURCES.items()
    }

    scope = load_scope(
        data[
            "job_matching"
        ]
    )

    # --------------------------------------------------------
    # Índices
    # --------------------------------------------------------

    notebooks_by_job = (
        index_rows_by_job(
            data[
                "job_notebooks"
            ]
        )
    )

    backlog_by_job = (
        index_rows_by_job(
            data[
                "notebook_backlog"
            ]
        )
    )

    readiness_by_job = (
        index_rows_by_job(
            data[
                "job_readiness"
            ]
        )
    )

    storage_by_job = (
        index_rows_by_job(
            data[
                "storage"
            ]
        )
    )

    secrets_by_job = (
        index_rows_by_job(
            data[
                "secrets"
            ]
        )
    )

    working_by_job = (
        index_rows_by_job(
            data[
                "working_tables"
            ]
        )
    )

    libraries_by_job = (
        index_rows_by_job(
            data[
                "libraries"
            ]
        )
    )

    config_by_job = (
        index_rows_by_job(
            data[
                "job_configuration"
            ]
        )
    )

    runtime_by_job = (
        index_rows_by_job(
            data[
                "runtime_parameters"
            ]
        )
    )

    identity_by_job = (
        index_rows_by_job(
            data[
                "identity_security"
            ]
        )
    )

    notifications_by_job = (
        index_rows_by_job(
            data[
                "notifications"
            ]
        )
    )

    actions_by_job = (
        index_actions_by_job(
            data[
                "master_actions"
            ]
        )
    )

    # --------------------------------------------------------
    # Tablas: el output final puede contener varios jobs en
    # una misma celda.
    # --------------------------------------------------------

    tables_by_job = defaultdict(list)

    for row in data[
        "tables"
    ]:

        used = truth(
            row.get(
                "usada_en_notebook"
            )
            or row.get(
                "used_in_notebook"
            )
            or row.get(
                "used"
            )
        )

        if not used:
            continue

        jobs = split_multi(
            row.get(
                "jobs"
            )
        )

        for job in jobs:
            tables_by_job[
                job
            ].append(
                row
            )

    # --------------------------------------------------------
    # JDBC: también puede ser multi-job.
    # --------------------------------------------------------

    jdbc_by_job = defaultdict(list)

    for row in data[
        "external_jdbc"
    ]:

        jobs = split_multi(
            row.get(
                "jobs"
            )
            or row.get(
                "job"
            )
        )

        for job in jobs:
            jdbc_by_job[
                job
            ].append(
                row
            )

    # ========================================================
    # Construir matriz
    # ========================================================

    output_rows = []

    for job in sorted(
        scope,
        key=str.casefold,
    ):

        uc_job = scope[
            job
        ]

        # ----------------------------------------------------
        # Notebooks
        # ----------------------------------------------------

        job_notebooks = notebooks_by_job.get(
            job,
            [],
        )

        notebook_names = unique(
            row.get(
                "notebook"
            )
            for row in job_notebooks
        )

        root_notebooks = unique(
            row.get(
                "notebook"
            )
            for row in job_notebooks
            if norm(
                row.get(
                    "relationship"
                )
            )
            == "root"
        )

        backlog_rows = backlog_by_job.get(
            job,
            [],
        )

        notebooks_ready = sum(
            1
            for row in backlog_rows
            if norm(
                row.get(
                    "migration_ready"
                )
            )
            == "yes"
        )

        notebooks_changes = sum(
            1
            for row in backlog_rows
            if norm(
                row.get(
                    "migration_ready"
                )
            )
            == "no"
        )

        # ----------------------------------------------------
        # Tables
        # ----------------------------------------------------

        table_rows = tables_by_job.get(
            job,
            [],
        )

        table_names = unique(
            row.get(
                "tabla_pro"
            )
            or row.get(
                "pro_table"
            )
            or row.get(
                "full_name"
            )
            for row in table_rows
        )

        uc_table_names = unique(
            row.get(
                "tabla_uc"
            )
            or row.get(
                "uc_table"
            )
            for row in table_rows
        )

        tables_no_mapping = sum(
            1
            for row in table_rows
            if norm(
                row.get(
                    "mapping_status"
                )
            )
            == "no_uc_mapping"
        )

        # ----------------------------------------------------
        # JDBC
        # ----------------------------------------------------

        jdbc_rows = jdbc_by_job.get(
            job,
            [],
        )

        jdbc_names = unique(
            row.get(
                "table_reference"
            )
            or row.get(
                "normalized_reference"
            )
            or row.get(
                "tabla"
            )
            or row.get(
                "table"
            )
            for row in jdbc_rows
        )

        # ----------------------------------------------------
        # Storage
        # ----------------------------------------------------

        storage_rows = storage_by_job.get(
            job,
            [],
        )

        storage_changes = [
            (
                f"{clean(row.get('storage_reference'))}"
                f" [{clean(row.get('migration_status'))}]"
            ).strip()
            for row in storage_rows
            if norm(
                row.get(
                    "requires_action"
                )
            )
            in {
                "yes",
                "review",
            }
        ]

        # ----------------------------------------------------
        # Working tables
        # ----------------------------------------------------

        working_rows = working_by_job.get(
            job,
            [],
        )

        working_changes = [
            (
                clean(
                    row.get(
                        "dynamic_reference"
                    )
                    or row.get(
                        "pattern"
                    )
                    or row.get(
                        "table_reference"
                    )
                )
                + (
                    f" [{clean(row.get('migration_status'))}]"
                    if clean(
                        row.get(
                            "migration_status"
                        )
                    )
                    else ""
                )
            )
            for row in working_rows
        ]

        # ----------------------------------------------------
        # Secrets
        # ----------------------------------------------------

        secret_rows = secrets_by_job.get(
            job,
            [],
        )

        secret_refs = unique(
            (
                f"{clean(row.get('scope_value_pro'))}"
                f"/{clean(row.get('secret_key_pro'))}"
            ).strip("/")
            for row in secret_rows
        )

        secret_reviews = count_review_rows(
            secret_rows
        )

        # ----------------------------------------------------
        # Libraries
        # ----------------------------------------------------

        library_rows = libraries_by_job.get(
            job,
            [],
        )

        library_summary = unique(
            (
                f"{clean(row.get('library_name'))}: "
                f"{clean(row.get('migration_status'))}"
            )
            for row in library_rows
            if clean(
                row.get(
                    "library_name"
                )
            )
        )

        library_reviews = count_review_rows(
            library_rows
        )

        # ----------------------------------------------------
        # Configuración
        # ----------------------------------------------------

        config_rows = config_by_job.get(
            job,
            [],
        )

        config_reviews = count_review_rows(
            config_rows
        )

        config_statuses = Counter(
            clean(
                row.get(
                    "migration_status"
                )
            )
            for row in config_rows
            if clean(
                row.get(
                    "migration_status"
                )
            )
        )

        config_summary = " | ".join(
            f"{status}={count}"
            for status, count
            in sorted(
                config_statuses.items()
            )
        )

        # ----------------------------------------------------
        # Runtime parameters
        # ----------------------------------------------------

        runtime_rows = runtime_by_job.get(
            job,
            [],
        )

        runtime_reviews = count_review_rows(
            runtime_rows
        )

        runtime_statuses = Counter(
            clean(
                row.get(
                    "migration_status"
                )
            )
            for row in runtime_rows
            if clean(
                row.get(
                    "migration_status"
                )
            )
        )

        runtime_summary = " | ".join(
            f"{status}={count}"
            for status, count
            in sorted(
                runtime_statuses.items()
            )
        )

        # ----------------------------------------------------
        # Identity / Security
        # ----------------------------------------------------

        identity_rows = identity_by_job.get(
            job,
            [],
        )

        identity_row = (
            identity_rows[0]
            if identity_rows
            else {}
        )

        identity_uc = clean(
            identity_row.get(
                "identity_uc"
            )
            or identity_row.get(
                "identity_value_uc"
            )
        )

        identity_type_uc = clean(
            identity_row.get(
                "identity_type_uc"
            )
        )

        security_mode_uc = clean(
            identity_row.get(
                "security_mode_uc"
            )
        )

        identity_reviews = count_review_rows(
            identity_rows
        )

        # ----------------------------------------------------
        # Notifications
        # ----------------------------------------------------

        notification_rows = notifications_by_job.get(
            job,
            [],
        )

        notification_row = (
            notification_rows[0]
            if notification_rows
            else {}
        )

        notification_status = clean(
            notification_row.get(
                "notification_status"
            )
        )

        health_status = clean(
            notification_row.get(
                "health_status"
            )
        )

        operation_status = clean(
            notification_row.get(
                "migration_status"
            )
        )

        operation_reviews = count_review_rows(
            notification_rows
        )

        # ----------------------------------------------------
        # Master actions
        # ----------------------------------------------------

        action_rows = actions_by_job.get(
            job,
            [],
        )

        action_ids = unique(
            row.get(
                "action_id"
            )
            for row in action_rows
        )

        action_titles = unique(
            (
                f"{clean(row.get('action_id'))} - "
                f"{clean(row.get('title'))}"
            ).strip(" -")
            for row in action_rows
        )

        # ----------------------------------------------------
        # Job readiness
        # ----------------------------------------------------

        readiness_rows = readiness_by_job.get(
            job,
            [],
        )

        readiness_row = (
            readiness_rows[0]
            if readiness_rows
            else {}
        )

        job_readiness = clean(
            readiness_row.get(
                "job_readiness"
            )
        )

        blocking_reason = clean(
            readiness_row.get(
                "blocking_reason"
            )
        )

        # ----------------------------------------------------
        # Manual reviews totales
        # ----------------------------------------------------

        manual_review_count = (
            secret_reviews
            + library_reviews
            + config_reviews
            + runtime_reviews
            + identity_reviews
            + operation_reviews
        )

        if (
            job_readiness
            == "REVIEW_REQUIRED"
        ):
            manual_review_count += 1

        # ----------------------------------------------------
        # Estado final por job
        # ----------------------------------------------------

        if manual_review_count > 0:

            final_assessment = (
                "REVIEW_REQUIRED"
            )

        elif (
            job_readiness
            == "REQUIRES_IMPLEMENTATION"
            or action_rows
        ):

            final_assessment = (
                "IMPLEMENTATION_PENDING"
            )

        else:

            final_assessment = (
                "READY"
            )

        # ----------------------------------------------------
        # Fila final
        # ----------------------------------------------------

        output_rows.append({
            "job":
                job,

            "uc_job":
                uc_job,

            "root_notebook":
                unique_join(
                    root_notebooks
                ),

            "notebooks_total":
                len(
                    notebook_names
                ),

            "notebooks_ready":
                notebooks_ready,

            "notebooks_requiring_changes":
                notebooks_changes,

            "tables_used_count":
                len(
                    table_names
                ),

            "tables_used_pro":
                unique_join(
                    table_names
                ),

            "tables_target_uc":
                unique_join(
                    uc_table_names
                ),

            "tables_without_uc_mapping":
                tables_no_mapping,

            "external_jdbc_count":
                len(
                    jdbc_names
                ),

            "external_jdbc_dependencies":
                unique_join(
                    jdbc_names
                ),

            "storage_changes_count":
                len(
                    storage_changes
                ),

            "storage_changes":
                unique_join(
                    storage_changes
                ),

            "working_table_changes_count":
                len(
                    working_changes
                ),

            "working_table_changes":
                unique_join(
                    working_changes
                ),

            "secret_references_count":
                len(
                    secret_refs
                ),

            "secret_references":
                unique_join(
                    secret_refs
                ),

            "secret_reviews":
                secret_reviews,

            "library_relations_count":
                len(
                    library_rows
                ),

            "library_summary":
                unique_join(
                    library_summary
                ),

            "library_reviews":
                library_reviews,

            "job_config_properties_count":
                len(
                    config_rows
                ),

            "job_config_summary":
                config_summary,

            "job_config_reviews":
                config_reviews,

            "runtime_parameters_count":
                len(
                    runtime_rows
                ),

            "runtime_parameters_summary":
                runtime_summary,

            "runtime_parameter_reviews":
                runtime_reviews,

            "identity_type_uc":
                identity_type_uc,

            "identity_uc":
                identity_uc,

            "security_mode_uc":
                security_mode_uc,

            "identity_security_reviews":
                identity_reviews,

            "notification_status":
                notification_status,

            "health_status":
                health_status,

            "operation_status":
                operation_status,

            "operation_reviews":
                operation_reviews,

            "master_actions_count":
                len(
                    action_ids
                ),

            "master_actions":
                unique_join(
                    action_titles
                ),

            "job_readiness":
                job_readiness,

            "blocking_reason":
                blocking_reason,

            "manual_reviews_total":
                manual_review_count,

            "final_assessment":
                final_assessment,
        })

    # ========================================================
    # Validaciones de consistencia
    # ========================================================

    if len(
        output_rows
    ) != len(
        scope
    ):
        raise RuntimeError(
            "La matriz final no contiene exactamente "
            "una fila por job en alcance."
        )

    duplicate_jobs = [
        job
        for job, count in Counter(
            row[
                "job"
            ]
            for row in output_rows
        ).items()
        if count > 1
    ]

    if duplicate_jobs:
        raise RuntimeError(
            "Hay jobs duplicados en la matriz final: "
            + ", ".join(
                duplicate_jobs
            )
        )

    # ========================================================
    # CSV
    # ========================================================

    fieldnames = [
        "job",
        "uc_job",
        "root_notebook",

        "notebooks_total",
        "notebooks_ready",
        "notebooks_requiring_changes",

        "tables_used_count",
        "tables_used_pro",
        "tables_target_uc",
        "tables_without_uc_mapping",

        "external_jdbc_count",
        "external_jdbc_dependencies",

        "storage_changes_count",
        "storage_changes",

        "working_table_changes_count",
        "working_table_changes",

        "secret_references_count",
        "secret_references",
        "secret_reviews",

        "library_relations_count",
        "library_summary",
        "library_reviews",

        "job_config_properties_count",
        "job_config_summary",
        "job_config_reviews",

        "runtime_parameters_count",
        "runtime_parameters_summary",
        "runtime_parameter_reviews",

        "identity_type_uc",
        "identity_uc",
        "security_mode_uc",
        "identity_security_reviews",

        "notification_status",
        "health_status",
        "operation_status",
        "operation_reviews",

        "master_actions_count",
        "master_actions",

        "job_readiness",
        "blocking_reason",

        "manual_reviews_total",
        "final_assessment",
    ]

    OUTPUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_CSV.open(
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

    # ========================================================
    # JSON
    # ========================================================

    final_counter = Counter(
        row[
            "final_assessment"
        ]
        for row in output_rows
    )

    readiness_counter = Counter(
        row[
            "job_readiness"
        ]
        for row in output_rows
    )

    json_payload = {
        "jobs_in_scope":
            len(
                output_rows
            ),

        "final_assessment_summary":
            dict(
                sorted(
                    final_counter.items()
                )
            ),

        "job_readiness_summary":
            dict(
                sorted(
                    readiness_counter.items()
                )
            ),

        "jobs":
            output_rows,
    }

    with OUTPUT_JSON.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            json_payload,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # ========================================================
    # Consola
    # ========================================================

    total_notebook_relations = sum(
        int(
            row[
                "notebooks_total"
            ]
        )
        for row in output_rows
    )

    total_tables_by_job = sum(
        int(
            row[
                "tables_used_count"
            ]
        )
        for row in output_rows
    )

    total_actions_by_job = sum(
        int(
            row[
                "master_actions_count"
            ]
        )
        for row in output_rows
    )

    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 28"
    )
    print(
        "MATRIZ FINAL PRO -> UNITY CATALOG POR JOB"
    )
    print("=" * 72)
    print()

    print(
        f"Jobs en alcance                  : "
        f"{len(output_rows)}"
    )

    print(
        f"Relaciones Job -> Notebook       : "
        f"{total_notebook_relations}"
    )

    print(
        f"Relaciones Job -> Tabla usada    : "
        f"{total_tables_by_job}"
    )

    print(
        f"Asignaciones Job -> Acción       : "
        f"{total_actions_by_job}"
    )

    print()

    print(
        "Readiness por job:"
    )

    for status in [
        "READY",
        "REQUIRES_IMPLEMENTATION",
        "REVIEW_REQUIRED",
    ]:

        print(
            f" - {status:<30}: "
            f"{readiness_counter.get(status, 0)}"
        )

    print()

    print(
        "Assessment final por job:"
    )

    for status in [
        "READY",
        "IMPLEMENTATION_PENDING",
        "REVIEW_REQUIRED",
    ]:

        print(
            f" - {status:<30}: "
            f"{final_counter.get(status, 0)}"
        )

    print()

    print(
        "Detalle:"
    )

    for row in output_rows:

        print(
            f" - {row['job']}"
            f" | notebooks={row['notebooks_total']}"
            f" | cambios={row['notebooks_requiring_changes']}"
            f" | tablas={row['tables_used_count']}"
            f" | acciones={row['master_actions_count']}"
            f" | estado={row['final_assessment']}"
        )

    print()

    print(
        f"CSV generado  : {OUTPUT_CSV}"
    )

    print(
        f"JSON generado : {OUTPUT_JSON}"
    )

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
