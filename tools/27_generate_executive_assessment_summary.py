#!/usr/bin/env python3
from pathlib import Path
from collections import Counter
import csv
import json


# ============================================================
# ASSESSMENT WORKSPACE - PASO 27
# CONSOLIDADO EJECUTIVO DEL ASSESSMENT
#
# Regla de diseño:
#   - NO recalcula análisis técnicos.
#   - NO reinterpreta notebooks/tablas/jobs.
#   - Sólo consolida outputs ya validados de Tool 2.
#
# Salidas:
#   output/assessment_executive_summary.csv
#   output/assessment_executive_summary.json
#
# Ajustes V3:
#   - Paso 19 usa migration_ready = YES / NO.
#   - Paso 14 V5.1 aporta existencia física real en UC y acciones
#     REGISTER_OR_MIGRATE_TO_UC / CREATE_VIEW_IN_UC.
#   - Paso 20 usa job_readiness = READY /
#     REQUIRES_IMPLEMENTATION / REVIEW_REQUIRED.
#   - Las revisiones de librerías se contabilizan sólo para
#     los 11 jobs EXACT_NAME; los jobs UCX sin homólogo UC se
#     conservan como evidencia OUT_OF_SCOPE, no como blocker.
# ============================================================


OUTPUT_DIR = Path("output")

SUMMARY_CSV = (
    OUTPUT_DIR
    / "assessment_executive_summary.csv"
)

SUMMARY_JSON = (
    OUTPUT_DIR
    / "assessment_executive_summary.json"
)


# ============================================================
# Fuentes oficiales
# ============================================================

SOURCES = {
    "notebooks":
        OUTPUT_DIR / "notebooks.csv",

    "job_notebook_inventory":
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

    "job_matching":
        OUTPUT_DIR / "job_name_matching.csv",

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
        "true",
        "1",
        "yes",
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


def counter(rows, field):
    return Counter(
        clean(
            row.get(field)
        )
        for row in rows
        if clean(
            row.get(field)
        )
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


def count_actionable(
    rows,
    fields=(
        "requires_action",
        "requires_review",
    ),
):
    total = 0

    for row in rows:

        value = ""

        for field in fields:

            candidate = clean(
                row.get(field)
            )

            if candidate:
                value = candidate
                break

        if norm(value) in {
            "yes",
            "review",
            "true",
        }:
            total += 1

    return total


def add_metric(
    metrics,
    section,
    metric,
    value,
    status="INFO",
    source="",
    notes="",
):
    metrics.append({
        "section":
            section,

        "metric":
            metric,

        "value":
            value,

        "status":
            status,

        "source":
            source,

        "notes":
            notes,
    })


# ============================================================
# Validación
# ============================================================

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

    metrics = []

    # ========================================================
    # 1. ALCANCE
    # ========================================================

    matching_rows = data[
        "job_matching"
    ]

    match_field = detect_field(
        matching_rows,
        [
            "match_method",
            "matching_method",
            "method",
        ],
    )

    workspace_job_field = detect_field(
        matching_rows,
        [
            "workspace_job",
            "pro_job",
            "job",
            "workspace_name",
        ],
    )

    exact_job_names = set()

    for row in matching_rows:

        if (
            match_field
            and norm(
                row.get(match_field)
            )
            == "exact_name"
        ):

            job_name = clean(
                row.get(
                    workspace_job_field
                )
            )

            if job_name:
                exact_job_names.add(
                    job_name
                )

    total_jobs = len(
        matching_rows
    )

    exact_jobs = len(
        exact_job_names
    )

    out_scope_jobs = (
        total_jobs
        - exact_jobs
    )

    notebook_rows = data[
        "notebooks"
    ]

    job_notebook_rows = data[
        "job_notebook_inventory"
    ]

    job_notebook_field = detect_field(
        job_notebook_rows,
        [
            "notebook",
            "workspace_path",
            "path",
        ],
    )

    scoped_notebooks = (
        len({
            clean(
                row.get(
                    job_notebook_field
                )
            )
            for row in job_notebook_rows
            if clean(
                row.get(
                    job_notebook_field
                )
            )
        })
        if job_notebook_field
        else 0
    )

    add_metric(
        metrics,
        "ALCANCE",
        "Jobs detectados en Workspace",
        total_jobs,
        "INFO",
        "job_name_matching.csv",
    )

    add_metric(
        metrics,
        "ALCANCE",
        "Jobs en alcance de migración",
        exact_jobs,
        "OK",
        "job_name_matching.csv",
        (
            "Jobs con matching EXACT_NAME "
            "contra definición UC."
        ),
    )

    add_metric(
        metrics,
        "ALCANCE",
        "Jobs fuera de alcance",
        out_scope_jobs,
        "INFO",
        "job_name_matching.csv",
        (
            "Conservados como evidencia del "
            "Workspace; no forman parte de la migración."
        ),
    )

    add_metric(
        metrics,
        "ALCANCE",
        "Notebooks en snapshot",
        len(
            notebook_rows
        ),
        "INFO",
        "notebooks.csv",
    )

    add_metric(
        metrics,
        "ALCANCE",
        "Notebooks utilizados por jobs en alcance",
        scoped_notebooks,
        "INFO",
        "job_notebook_inventory.csv",
    )

    # ========================================================
    # 2. DATOS
    # ========================================================

    table_rows = data[
        "tables"
    ]

    def table_is_used(row):
        return truth(
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

    used_tables = sum(
        1
        for row in table_rows
        if table_is_used(row)
    )

    no_uc_mapping_used = sum(
        1
        for row in table_rows
        if (
            table_is_used(row)
            and norm(
                row.get(
                    "mapping_status"
                )
                or row.get(
                    "fuente_mapeo"
                )
            )
            == "no_uc_mapping"
        )
    )

    # Paso 14 V5.1: estado físico real de objetos funcionales en UC.
    used_uc_found = sum(
        1
        for row in table_rows
        if (
            table_is_used(row)
            and clean(
                row.get(
                    "reconciliation_status"
                )
            )
            == "EXISTS_AND_USED_UC_FOUND"
        )
    )

    used_uc_missing_tables = sum(
        1
        for row in table_rows
        if (
            table_is_used(row)
            and (
                clean(
                    row.get(
                        "reconciliation_status"
                    )
                )
                == "EXISTS_AND_USED_UC_NOT_FOUND"
                or clean(
                    row.get(
                        "migration_action"
                    )
                )
                == "REGISTER_OR_MIGRATE_TO_UC"
            )
        )
    )

    used_uc_missing_views = sum(
        1
        for row in table_rows
        if (
            table_is_used(row)
            and (
                clean(
                    row.get(
                        "reconciliation_status"
                    )
                )
                == "USED_VIEW_UC_NOT_FOUND"
                or clean(
                    row.get(
                        "migration_action"
                    )
                )
                == "CREATE_VIEW_IN_UC"
            )
        )
    )

    used_uc_missing_total = (
        used_uc_missing_tables
        + used_uc_missing_views
    )

    add_metric(
        metrics,
        "DATOS",
        "Tablas físicas Hive inventariadas",
        len(
            table_rows
        ),
        "INFO",
        "table_hive_reconciliation_final.csv",
    )

    add_metric(
        metrics,
        "DATOS",
        "Tablas Hive utilizadas",
        used_tables,
        (
            "OK"
            if no_uc_mapping_used == 0
            else "ATTENTION"
        ),
        "table_hive_reconciliation_final.csv",
    )

    add_metric(
        metrics,
        "DATOS",
        "Objetos funcionales encontrados en UC",
        used_uc_found,
        (
            "OK"
            if used_uc_missing_total == 0
            else "ATTENTION"
        ),
        "table_hive_reconciliation_final.csv",
        (
            "Objetos usados por notebooks funcionales cuyo destino "
            "UC fue encontrado físicamente en el inventario del workspace."
        ),
    )

    add_metric(
        metrics,
        "DATOS",
        "Objetos funcionales ausentes en UC",
        used_uc_missing_total,
        (
            "ATTENTION"
            if used_uc_missing_total
            else "OK"
        ),
        "table_hive_reconciliation_final.csv",
        (
            "Incluye tablas externas pendientes de registro/migración "
            "y vistas persistentes pendientes de recreación."
        ),
    )

    add_metric(
        metrics,
        "DATOS",
        "Tablas externas ausentes en UC",
        used_uc_missing_tables,
        (
            "ATTENTION"
            if used_uc_missing_tables
            else "OK"
        ),
        "table_hive_reconciliation_final.csv",
        (
            "Acción esperada: REGISTER_OR_MIGRATE_TO_UC."
        ),
    )

    add_metric(
        metrics,
        "DATOS",
        "Views persistentes ausentes en UC",
        used_uc_missing_views,
        (
            "ATTENTION"
            if used_uc_missing_views
            else "OK"
        ),
        "table_hive_reconciliation_final.csv",
        (
            "Acción esperada: CREATE_VIEW_IN_UC."
        ),
    )

    add_metric(
        metrics,
        "DATOS",
        "Tablas usadas sin mapping UC",
        no_uc_mapping_used,
        (
            "OK"
            if no_uc_mapping_used == 0
            else "ATTENTION"
        ),
        "table_hive_reconciliation_final.csv",
    )

    add_metric(
        metrics,
        "DATOS",
        "Dependencias JDBC externas",
        len(
            data[
                "external_jdbc"
            ]
        ),
        "INFO",
        "external_jdbc_dependencies.csv",
        (
            "Dependencias externas conservadas fuera "
            "del alcance HMS -> UC."
        ),
    )

    # ========================================================
    # 3. STORAGE
    # ========================================================

    storage_rows = data[
        "storage"
    ]

    storage_actions = count_actionable(
        storage_rows
    )

    add_metric(
        metrics,
        "INFRAESTRUCTURA",
        "Referencias storage analizadas",
        len(
            storage_rows
        ),
        (
            "ATTENTION"
            if storage_actions
            else "OK"
        ),
        "storage_migration_analysis.csv",
    )

    add_metric(
        metrics,
        "INFRAESTRUCTURA",
        "Referencias storage con cambio requerido",
        storage_actions,
        (
            "ATTENTION"
            if storage_actions
            else "OK"
        ),
        "storage_migration_analysis.csv",
    )

    # ========================================================
    # 4. SECRETS
    # ========================================================

    secret_rows = data[
        "secrets"
    ]

    secret_reviews = count_actionable(
        secret_rows
    )

    add_metric(
        metrics,
        "SEGURIDAD",
        "Referencias a secrets",
        len(
            secret_rows
        ),
        (
            "ATTENTION"
            if secret_reviews
            else "OK"
        ),
        "secret_usage_analysis.csv",
    )

    add_metric(
        metrics,
        "SEGURIDAD",
        "Secrets con revisión pendiente",
        secret_reviews,
        (
            "ATTENTION"
            if secret_reviews
            else "OK"
        ),
        "secret_usage_analysis.csv",
    )

    # ========================================================
    # 5. WORKING TABLES
    # ========================================================

    working_rows = data[
        "working_tables"
    ]

    working_actions = count_actionable(
        working_rows
    )

    if working_actions == 0:

        working_actions = sum(
            1
            for row in working_rows
            if norm(
                row.get(
                    "migration_status"
                )
            )
            in {
                "schema_configuration_required",
                "requires_implementation",
            }
        )

    add_metric(
        metrics,
        "DATOS",
        "Patrones de working tables dinámicas",
        len(
            working_rows
        ),
        (
            "ATTENTION"
            if working_actions
            else "OK"
        ),
        "dynamic_working_tables.csv",
    )

    add_metric(
        metrics,
        "DATOS",
        "Working tables con implementación requerida",
        working_actions,
        (
            "ATTENTION"
            if working_actions
            else "OK"
        ),
        "dynamic_working_tables.csv",
    )

    # ========================================================
    # 6. BACKLOG DE NOTEBOOKS - PASO 19
    #
    # Contrato real:
    #   migration_ready = YES / NO
    # ========================================================

    backlog_rows = data[
        "notebook_backlog"
    ]

    backlog_ready_counter = counter(
        backlog_rows,
        "migration_ready",
    )

    notebooks_ready = (
        backlog_ready_counter.get(
            "YES",
            0,
        )
    )

    notebooks_changes = (
        backlog_ready_counter.get(
            "NO",
            0,
        )
    )

    add_metric(
        metrics,
        "NOTEBOOKS",
        "Notebooks evaluados en backlog",
        len(
            backlog_rows
        ),
        "INFO",
        "notebook_migration_backlog.csv",
    )

    add_metric(
        metrics,
        "NOTEBOOKS",
        "Notebooks READY",
        notebooks_ready,
        "OK",
        "notebook_migration_backlog.csv",
    )

    add_metric(
        metrics,
        "NOTEBOOKS",
        "Notebooks que requieren cambios",
        notebooks_changes,
        (
            "ATTENTION"
            if notebooks_changes
            else "OK"
        ),
        "notebook_migration_backlog.csv",
    )

    change_columns = [
        (
            "table_changes",
            "TABLE",
        ),
        (
            "working_table_changes",
            "WORKING_TABLE",
        ),
        (
            "storage_changes",
            "STORAGE",
        ),
        (
            "config_changes",
            "CONFIG",
        ),
        (
            "hardcode_changes",
            "HARDCODE",
        ),
        (
            "secret_reviews",
            "SECRET_REVIEW",
        ),
        (
            "manual_reviews",
            "MANUAL_REVIEW",
        ),
    ]

    for field, label in change_columns:

        impacted = sum(
            1
            for row in backlog_rows
            if clean(
                row.get(field)
            )
        )

        add_metric(
            metrics,
            "NOTEBOOKS",
            f"Notebooks impactados - {label}",
            impacted,
            (
                "ATTENTION"
                if impacted
                else "OK"
            ),
            "notebook_migration_backlog.csv",
        )

    # ========================================================
    # 7. READINESS DE JOBS - PASO 20
    #
    # Contrato real:
    #   job_readiness
    # ========================================================

    readiness_rows = data[
        "job_readiness"
    ]

    readiness_counter = counter(
        readiness_rows,
        "job_readiness",
    )

    add_metric(
        metrics,
        "JOBS",
        "Jobs evaluados en readiness",
        len(
            readiness_rows
        ),
        "INFO",
        "job_migration_readiness.csv",
    )

    for status in [
        "READY",
        "REQUIRES_IMPLEMENTATION",
        "REVIEW_REQUIRED",
    ]:

        value = readiness_counter.get(
            status,
            0,
        )

        add_metric(
            metrics,
            "JOBS",
            f"Readiness - {status}",
            value,
            (
                "OK"
                if status == "READY"
                or (
                    status
                    == "REVIEW_REQUIRED"
                    and value == 0
                )
                else (
                    "ATTENTION"
                    if value
                    else "OK"
                )
            ),
            "job_migration_readiness.csv",
        )

    # ========================================================
    # 8. ACCIONES MAESTRAS - PASO 21
    # ========================================================

    master_rows = data[
        "master_actions"
    ]

    implementation_actions = len(
        master_rows
    )

    add_metric(
        metrics,
        "ACCIONES",
        "Acciones maestras de migración",
        implementation_actions,
        (
            "ATTENTION"
            if implementation_actions
            else "OK"
        ),
        "master_migration_actions.csv",
        (
            "Backlog consolidado de implementación; "
            "no son revisiones manuales."
        ),
    )

    master_status_field = detect_field(
        master_rows,
        [
            "status",
            "migration_status",
            "estado",
        ],
    )

    if master_status_field:

        for status, value in sorted(
            counter(
                master_rows,
                master_status_field,
            ).items()
        ):

            add_metric(
                metrics,
                "ACCIONES",
                f"Acciones - {status}",
                value,
                (
                    "ATTENTION"
                    if value
                    else "OK"
                ),
                "master_migration_actions.csv",
            )

    # ========================================================
    # 9. LIBRERÍAS - PASO 22
    #
    # Importante:
    #   15 jobs UCX sin homólogo UC deben conservarse como
    #   evidencia, pero NO cuentan como blockers del scope.
    # ========================================================

    library_rows = data[
        "libraries"
    ]

    library_job_field = detect_field(
        library_rows,
        [
            "job",
            "workspace_job",
            "pro_job",
        ],
    )

    in_scope_library_rows = [
        row
        for row in library_rows
        if clean(
            row.get(
                library_job_field
            )
        )
        in exact_job_names
    ]

    out_scope_library_rows = [
        row
        for row in library_rows
        if clean(
            row.get(
                library_job_field
            )
        )
        not in exact_job_names
    ]

    library_reviews_scope = (
        count_actionable(
            in_scope_library_rows
        )
    )

    add_metric(
        metrics,
        "JOBS",
        "Relaciones job -> librería analizadas",
        len(
            library_rows
        ),
        "INFO",
        "job_library_migration_analysis.csv",
    )

    add_metric(
        metrics,
        "JOBS",
        "Relaciones de librerías en alcance",
        len(
            in_scope_library_rows
        ),
        "INFO",
        "job_library_migration_analysis.csv",
    )

    add_metric(
        metrics,
        "JOBS",
        "Registros de librerías fuera de alcance",
        len(
            out_scope_library_rows
        ),
        "INFO",
        "job_library_migration_analysis.csv",
        (
            "Incluye evidencia de jobs UCX sin homólogo UC."
        ),
    )

    add_metric(
        metrics,
        "JOBS",
        "Librerías en alcance con revisión/acción",
        library_reviews_scope,
        (
            "ATTENTION"
            if library_reviews_scope
            else "OK"
        ),
        "job_library_migration_analysis.csv",
    )

    # ========================================================
    # 10. CONFIGURACIÓN - PASO 23
    # ========================================================

    config_rows = data[
        "job_configuration"
    ]

    config_reviews = count_actionable(
        config_rows
    )

    add_metric(
        metrics,
        "JOBS",
        "Propiedades de configuración analizadas",
        len(
            config_rows
        ),
        (
            "ATTENTION"
            if config_reviews
            else "OK"
        ),
        "job_configuration_migration_analysis.csv",
    )

    add_metric(
        metrics,
        "JOBS",
        "Configuración con revisión pendiente",
        config_reviews,
        (
            "ATTENTION"
            if config_reviews
            else "OK"
        ),
        "job_configuration_migration_analysis.csv",
    )

    # ========================================================
    # 11. RUNTIME PARAMS - PASO 24
    # ========================================================

    runtime_rows = data[
        "runtime_parameters"
    ]

    runtime_reviews = count_actionable(
        runtime_rows
    )

    add_metric(
        metrics,
        "JOBS",
        "Parámetros/variables de ejecución",
        len(
            runtime_rows
        ),
        (
            "ATTENTION"
            if runtime_reviews
            else "OK"
        ),
        "job_runtime_parameters_analysis.csv",
    )

    add_metric(
        metrics,
        "JOBS",
        "Parámetros con revisión pendiente",
        runtime_reviews,
        (
            "ATTENTION"
            if runtime_reviews
            else "OK"
        ),
        "job_runtime_parameters_analysis.csv",
    )

    # ========================================================
    # 12. IDENTIDAD / SEGURIDAD - PASO 25
    # ========================================================

    identity_rows = data[
        "identity_security"
    ]

    identity_reviews = count_actionable(
        identity_rows
    )

    uc_sp = sum(
        1
        for row in identity_rows
        if norm(
            row.get(
                "identity_type_uc"
            )
        )
        == "service_principal"
    )

    security_declared = sum(
        1
        for row in identity_rows
        if norm(
            row.get(
                "security_mode_uc"
            )
        )
        not in {
            "",
            "not_declared",
        }
    )

    add_metric(
        metrics,
        "SEGURIDAD",
        "Jobs UC con Service Principal",
        uc_sp,
        (
            "OK"
            if uc_sp
            == len(identity_rows)
            else "ATTENTION"
        ),
        "job_identity_security_analysis.csv",
    )

    add_metric(
        metrics,
        "SEGURIDAD",
        "Jobs UC con data_security_mode",
        security_declared,
        (
            "OK"
            if security_declared
            == len(identity_rows)
            else "ATTENTION"
        ),
        "job_identity_security_analysis.csv",
    )

    add_metric(
        metrics,
        "SEGURIDAD",
        "Identidad/seguridad con revisión pendiente",
        identity_reviews,
        (
            "ATTENTION"
            if identity_reviews
            else "OK"
        ),
        "job_identity_security_analysis.csv",
    )

    # ========================================================
    # 13. ALERTAS / OPERACIÓN - PASO 26
    # ========================================================

    notification_rows = data[
        "notifications"
    ]

    notification_reviews = count_actionable(
        notification_rows
    )

    add_metric(
        metrics,
        "OPERACION",
        "Jobs evaluados en alertas/operación",
        len(
            notification_rows
        ),
        "INFO",
        "job_notifications_operation_analysis.csv",
    )

    add_metric(
        metrics,
        "OPERACION",
        "Alertas/operación con revisión pendiente",
        notification_reviews,
        (
            "ATTENTION"
            if notification_reviews
            else "OK"
        ),
        "job_notifications_operation_analysis.csv",
    )

    for field, label in [
        (
            "notification_status",
            "Notificaciones",
        ),
        (
            "health_status",
            "Health",
        ),
        (
            "migration_status",
            "Operación",
        ),
    ]:

        for status, value in sorted(
            counter(
                notification_rows,
                field,
            ).items()
        ):

            add_metric(
                metrics,
                "OPERACION",
                f"{label} - {status}",
                value,
                (
                    "ATTENTION"
                    if norm(status)
                    == "requires_review"
                    else "OK"
                ),
                "job_notifications_operation_analysis.csv",
            )

    # ========================================================
    # 14. CIERRE GLOBAL
    #
    # Reviews = dudas/manual review aún abiertas.
    # Implementation = cambios ya identificados y documentados.
    # ========================================================

    readiness_reviews = (
        readiness_counter.get(
            "REVIEW_REQUIRED",
            0,
        )
    )

    manual_reviews = (
        secret_reviews
        + library_reviews_scope
        + config_reviews
        + runtime_reviews
        + identity_reviews
        + notification_reviews
        + readiness_reviews
    )

    if manual_reviews > 0:

        overall_status = (
            "REVIEW_REQUIRED"
        )

    elif implementation_actions > 0:

        overall_status = (
            "IMPLEMENTATION_PENDING"
        )

    else:

        overall_status = (
            "READY"
        )

    add_metric(
        metrics,
        "CIERRE",
        "Revisiones manuales pendientes",
        manual_reviews,
        (
            "ATTENTION"
            if manual_reviews
            else "OK"
        ),
        (
            "Outputs de secrets, librerías en alcance, "
            "readiness, configuración, runtime params, "
            "identidad y operación"
        ),
        (
            "No incluye acciones de implementación "
            "ya identificadas en el Paso 21."
        ),
    )

    add_metric(
        metrics,
        "CIERRE",
        "Acciones de implementación pendientes",
        implementation_actions,
        (
            "ATTENTION"
            if implementation_actions
            else "OK"
        ),
        "master_migration_actions.csv",
    )

    add_metric(
        metrics,
        "CIERRE",
        "Estado global del assessment",
        overall_status,
        (
            "ATTENTION"
            if overall_status
            != "READY"
            else "OK"
        ),
        "Consolidado Paso 27",
        (
            "IMPLEMENTATION_PENDING significa que "
            "el assessment técnico está cerrado sin "
            "revisiones manuales, pero existen cambios "
            "de implementación ya documentados."
        ),
    )

    # ========================================================
    # CSV
    # ========================================================

    SUMMARY_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "section",
        "metric",
        "value",
        "status",
        "source",
        "notes",
    ]

    with SUMMARY_CSV.open(
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
            metrics
        )

    # ========================================================
    # JSON
    # ========================================================

    json_payload = {
        "assessment_status":
            overall_status,

        "jobs_workspace":
            total_jobs,

        "jobs_in_scope":
            exact_jobs,

        "jobs_out_of_scope":
            out_scope_jobs,

        "notebooks_snapshot":
            len(
                notebook_rows
            ),

        "notebooks_in_scope":
            scoped_notebooks,

        "notebooks_ready":
            notebooks_ready,

        "notebooks_requiring_changes":
            notebooks_changes,

        "jobs_ready":
            readiness_counter.get(
                "READY",
                0,
            ),

        "jobs_requiring_implementation":
            readiness_counter.get(
                "REQUIRES_IMPLEMENTATION",
                0,
            ),

        "jobs_review_required":
            readiness_counter.get(
                "REVIEW_REQUIRED",
                0,
            ),

        "manual_reviews_pending":
            manual_reviews,

        "implementation_actions_pending":
            implementation_actions,

        "functional_objects_used":
            used_tables,

        "functional_objects_found_in_uc":
            used_uc_found,

        "functional_objects_missing_in_uc":
            used_uc_missing_total,

        "external_tables_missing_in_uc":
            used_uc_missing_tables,

        "persistent_views_missing_in_uc":
            used_uc_missing_views,

        "metrics":
            metrics,

        "sources": {
            name:
                str(path)

            for name, path
            in SOURCES.items()
        },
    }

    with SUMMARY_JSON.open(
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
    # CONSOLA
    # ========================================================

    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 27"
    )
    print(
        "CONSOLIDADO EJECUTIVO"
    )
    print("=" * 72)
    print()

    print("--- Alcance ---")
    print(
        f"Jobs detectados en Workspace     : "
        f"{total_jobs}"
    )
    print(
        f"Jobs en alcance                  : "
        f"{exact_jobs}"
    )
    print(
        f"Jobs fuera de alcance            : "
        f"{out_scope_jobs}"
    )
    print(
        f"Notebooks en snapshot            : "
        f"{len(notebook_rows)}"
    )
    print(
        f"Notebooks utilizados             : "
        f"{scoped_notebooks}"
    )
    print()

    print("--- Datos / infraestructura ---")
    print(
        f"Tablas Hive inventariadas        : "
        f"{len(table_rows)}"
    )
    print(
        f"Tablas Hive utilizadas           : "
        f"{used_tables}"
    )
    print(
        f"Objetos funcionales en UC        : "
        f"{used_uc_found}"
    )
    print(
        f"Objetos funcionales ausentes UC  : "
        f"{used_uc_missing_total}"
    )
    print(
        f" - Tablas externas ausentes      : "
        f"{used_uc_missing_tables}"
    )
    print(
        f" - Views persistentes ausentes   : "
        f"{used_uc_missing_views}"
    )
    print(
        f"Dependencias JDBC externas       : "
        f"{len(data['external_jdbc'])}"
    )
    print(
        f"Referencias storage              : "
        f"{len(storage_rows)}"
    )
    print(
        f"Patrones working tables          : "
        f"{len(working_rows)}"
    )
    print()

    print("--- Backlog de notebooks ---")
    print(
        f"Notebooks READY                  : "
        f"{notebooks_ready}"
    )
    print(
        f"Notebooks que requieren cambios  : "
        f"{notebooks_changes}"
    )
    print()

    print("--- Readiness de jobs ---")
    print(
        f"Jobs READY                       : "
        f"{readiness_counter.get('READY', 0)}"
    )
    print(
        f"Jobs REQUIRES_IMPLEMENTATION     : "
        f"{readiness_counter.get('REQUIRES_IMPLEMENTATION', 0)}"
    )
    print(
        f"Jobs REVIEW_REQUIRED             : "
        f"{readiness_counter.get('REVIEW_REQUIRED', 0)}"
    )
    print(
        f"Acciones maestras                : "
        f"{implementation_actions}"
    )
    print()

    print("--- Validaciones finales ---")
    print(
        f"Librerías scope - reviews        : "
        f"{library_reviews_scope}"
    )
    print(
        f"Config job - reviews             : "
        f"{config_reviews}"
    )
    print(
        f"Runtime params - reviews         : "
        f"{runtime_reviews}"
    )
    print(
        f"Identity/security - reviews      : "
        f"{identity_reviews}"
    )
    print(
        f"Notifications - reviews          : "
        f"{notification_reviews}"
    )
    print(
        f"Secrets - reviews                : "
        f"{secret_reviews}"
    )
    print()

    print("--- Cierre ---")
    print(
        f"Revisiones manuales pendientes   : "
        f"{manual_reviews}"
    )
    print(
        f"Acciones implementación pendientes: "
        f"{implementation_actions}"
    )
    print(
        f"Estado global                    : "
        f"{overall_status}"
    )
    print()

    print(
        f"CSV generado  : {SUMMARY_CSV}"
    )
    print(
        f"JSON generado : {SUMMARY_JSON}"
    )
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
