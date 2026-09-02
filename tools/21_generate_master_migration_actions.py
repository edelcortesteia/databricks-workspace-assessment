#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv


# ============================================================
# Assessment Workspace - Paso 21
# Backlog Maestro de Acciones de Migración
#
# Base lógica: Tool 1 / Paso 20
#   tools/20_generate_master_migration_actions.py
#
# Adaptación Tool 2:
#   - usa snapshot Workspace como fuente autoritativa;
#   - consume Paso 19 y Paso 20 actuales;
#   - usa Paso 18 para working tables dinámicas;
#   - usa Paso 15 para storage;
#   - integra revisiones manuales si existieran;
#   - evita duplicar una misma acción técnica cuando un notebook
#     impacta varios jobs.
# ============================================================


NOTEBOOK_BACKLOG_FILE = Path(
    "output/notebook_migration_backlog.csv"
)

JOB_READINESS_FILE = Path(
    "output/job_migration_readiness.csv"
)

STORAGE_ANALYSIS_FILE = Path(
    "output/storage_migration_analysis.csv"
)

DYNAMIC_WORKING_TABLES_FILE = Path(
    "output/dynamic_working_tables.csv"
)

OUTPUT_FILE = Path(
    "output/master_migration_actions.csv"
)


# ============================================================
# Utilidades
# ============================================================

def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize(value):
    return (
        clean(value)
        .replace("\\", "/")
        .strip()
        .lower()
    )


def read_csv(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        return list(csv.DictReader(f))


def split_multi_value(value):
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


def unique_join(values):
    return " | ".join(
        unique(values)
    )


# ============================================================
# Validar entradas
# ============================================================

required_files = [
    NOTEBOOK_BACKLOG_FILE,
    JOB_READINESS_FILE,
    STORAGE_ANALYSIS_FILE,
    DYNAMIC_WORKING_TABLES_FILE,
]

missing_files = [
    str(path)
    for path in required_files
    if not path.exists()
]

if missing_files:
    print(
        "ERROR: faltan archivos requeridos:"
    )

    for path in missing_files:
        print(f" - {path}")

    raise SystemExit(1)


# ============================================================
# Cargar
# ============================================================

notebook_rows = read_csv(
    NOTEBOOK_BACKLOG_FILE
)

job_rows = read_csv(
    JOB_READINESS_FILE
)

storage_rows = read_csv(
    STORAGE_ANALYSIS_FILE
)

dynamic_rows = read_csv(
    DYNAMIC_WORKING_TABLES_FILE
)


# ============================================================
# Índices notebook -> jobs
# ============================================================

notebook_to_jobs = defaultdict(
    set
)

for row in notebook_rows:
    notebook = clean(
        row.get("notebook")
    )

    if not notebook:
        continue

    for job in split_multi_value(
        row.get("job")
    ):
        notebook_to_jobs[
            notebook
        ].add(job)


# ============================================================
# Acumulador maestro
# ============================================================

actions = {}


def get_action(
    action_key,
    action_type,
    title,
):
    if action_key not in actions:
        actions[
            action_key
        ] = {
            "action_type":
                action_type,

            "title":
                title,

            "description":
                "",

            "affected_notebooks":
                set(),

            "affected_jobs":
                set(),

            "config_change":
                "",

            "code_change":
                "",

            "target_value":
                "",

            "priority":
                "",

            "status":
                "PENDING_IMPLEMENTATION",

            "source_steps":
                set(),

            "notes":
                set(),
        }

    return actions[
        action_key
    ]


def add_notebook_and_jobs(
    action,
    notebook,
):
    notebook = clean(
        notebook
    )

    if not notebook:
        return

    action[
        "affected_notebooks"
    ].add(
        notebook
    )

    for job in notebook_to_jobs.get(
        notebook,
        []
    ):
        action[
            "affected_jobs"
        ].add(
            job
        )


# ============================================================
# ACCIÓN 1 - Schema dinámico cv_work
#
# Consolida los 3 notebooks del Paso 18 en UNA sola acción de
# arquitectura/configuración porque el cambio técnico es común:
#
#   EsquemasTrabajoDbks_UC.Default
#       -> u_impin_convol.cv_work
#
# La modificación de código se aplica en cada notebook afectado,
# pero el backlog maestro no duplica la decisión técnica.
# ============================================================

for row in dynamic_rows:
    if (
        clean(
            row.get("requires_action")
        )
        != "YES"
    ):
        continue

    notebook = clean(
        row.get("notebook")
    )

    config_path = clean(
        row.get("config_path")
    )

    configured_schema = clean(
        row.get(
            "configured_work_schema"
        )
    )

    target_schema = (
        configured_schema
        or "u_impin_convol.cv_work"
    )

    action_key = (
        "DYNAMIC_WORK_SCHEMA::"
        + normalize(
            config_path
            or "EsquemasTrabajoDbks_UC.Default"
        )
    )

    action = get_action(
        action_key,
        "WORK_SCHEMA",
        "Configurar schema dinámico de trabajo cv_work",
    )

    action[
        "description"
    ] = (
        "Sustituir el uso dinámico del esquema legacy "
        "default por un schema de trabajo gobernado por "
        "Unity Catalog."
    )

    action[
        "config_change"
    ] = (
        "Agregar/usar "
        "EsquemasTrabajoDbks_UC.Default"
    )

    action[
        "code_change"
    ] = (
        "Construir las tablas dinámicas usando "
        "EsquemasTrabajoDbks_UC.Default en lugar de "
        "default.${nombreTablaSinBaseDeDatos}."
    )

    action[
        "target_value"
    ] = target_schema

    action[
        "priority"
    ] = "HIGH"

    action[
        "source_steps"
    ].update(
        {
            "STEP_18",
            "STEP_19",
            "STEP_20",
        }
    )

    add_notebook_and_jobs(
        action,
        notebook,
    )


# ============================================================
# ACCIONES - Storage
# ============================================================

for row in storage_rows:
    if (
        clean(
            row.get("requires_action")
        )
        != "YES"
    ):
        continue

    notebook = clean(
        row.get("notebook")
    )

    migration_status = clean(
        row.get("migration_status")
    )

    config_path = clean(
        row.get("config_path")
    )

    # --------------------------------------------------------
    # Cedulas - completar ABFSS
    # --------------------------------------------------------

    if (
        migration_status
        == "CONFIG_ABFSS_URI_REQUIRED"
    ):
        action_key = (
            "STORAGE_ABFSS::"
            + normalize(
                config_path
            )
        )

        action = get_action(
            action_key,
            "STORAGE",
            "Migrar ruta Cedulas de DBFS a ABFSS",
        )

        action[
            "description"
        ] = (
            "La ruta de Cedulas debe dejar de construirse "
            "mediante dbfs:/ y pasar a utilizar una URI "
            "ABFSS completa."
        )

        action[
            "config_change"
        ] = (
            f"Actualizar {config_path} con URI "
            f"abfss:// completa."
        )

        action[
            "code_change"
        ] = (
            "Eliminar el prefijo dbfs:/ del notebook y "
            "consumir directamente el valor configurado."
        )

        uc_value = clean(
            row.get("uc_value")
        )

        if (
            uc_value
            and "@" in uc_value
        ):
            target = (
                uc_value
                .lstrip("/")
            )

            if not target.lower().startswith(
                "abfss://"
            ):
                target = (
                    "abfss://"
                    + target
                )

            action[
                "target_value"
            ] = target

        action[
            "priority"
        ] = "HIGH"

        action[
            "source_steps"
        ].update(
            {
                "STEP_15",
                "STEP_19",
                "STEP_20",
            }
        )

        add_notebook_and_jobs(
            action,
            notebook,
        )

    # --------------------------------------------------------
    # Config path - variable de entorno
    # --------------------------------------------------------

    elif (
        migration_status
        == "ENV_CONFIG_PATH_REQUIRED"
    ):
        action_key = (
            "ENV_CONFIG_PATH::"
            "CV_EXPLOTACION_CONFIG_FILE_PATH"
        )

        action = get_action(
            action_key,
            "CONFIG_PATH",
            "Eliminar hardcode del archivo de configuración",
        )

        action[
            "description"
        ] = (
            "Eliminar la ruta /mnt hardcodeada al archivo "
            "0.0_Configuration.json y utilizar la variable "
            "de entorno del job."
        )

        action[
            "config_change"
        ] = (
            "Garantizar que el job exponga "
            "CV_EXPLOTACION_CONFIG_FILE_PATH."
        )

        action[
            "code_change"
        ] = (
            'Usar sys.env("CV_EXPLOTACION_CONFIG_FILE_PATH").'
        )

        action[
            "target_value"
        ] = (
            "CV_EXPLOTACION_CONFIG_FILE_PATH"
        )

        action[
            "priority"
        ] = "HIGH"

        action[
            "source_steps"
        ].update(
            {
                "STEP_15",
                "STEP_16",
                "STEP_19",
                "STEP_20",
            }
        )

        add_notebook_and_jobs(
            action,
            notebook,
        )


# ============================================================
# ACCIONES - Revisiones manuales
#
# En UAT actualmente esperamos 0.
# Se conserva para que el mismo script funcione en PRO si aparece
# alguna referencia no resuelta.
# ============================================================

for row in notebook_rows:
    manual_reviews = clean(
        row.get("manual_reviews")
    )

    secret_reviews = clean(
        row.get("secret_reviews")
    )

    notebook = clean(
        row.get("notebook")
    )

    if manual_reviews:
        action_key = (
            "MANUAL_REVIEW::"
            + normalize(notebook)
            + "::"
            + normalize(manual_reviews)
        )

        action = get_action(
            action_key,
            "MANUAL_REVIEW",
            "Revisar referencia dinámica no resuelta",
        )

        action[
            "description"
        ] = (
            "El assessment no logró demostrar completamente "
            "el valor final de una referencia dinámica."
        )

        action[
            "code_change"
        ] = (
            "Revisar manualmente la llamada y confirmar "
            "el objeto real utilizado antes de modificar."
        )

        action[
            "priority"
        ] = "LOW"

        action[
            "status"
        ] = "MANUAL_REVIEW_PENDING"

        action[
            "source_steps"
        ].update(
            {
                "STEP_13",
                "STEP_19",
                "STEP_20",
            }
        )

        action[
            "notes"
        ].add(
            manual_reviews
        )

        add_notebook_and_jobs(
            action,
            notebook,
        )

    if secret_reviews:
        action_key = (
            "SECRET_REVIEW::"
            + normalize(notebook)
            + "::"
            + normalize(secret_reviews)
        )

        action = get_action(
            action_key,
            "SECRET_REVIEW",
            "Revisar referencia a Secret Scope/key",
        )

        action[
            "description"
        ] = (
            "El análisis de secrets no logró resolver "
            "completamente el scope o key utilizado."
        )

        action[
            "code_change"
        ] = (
            "Confirmar scope/key real y validar su "
            "disponibilidad en el workspace destino."
        )

        action[
            "priority"
        ] = "MEDIUM"

        action[
            "status"
        ] = "MANUAL_REVIEW_PENDING"

        action[
            "source_steps"
        ].update(
            {
                "STEP_17",
                "STEP_19",
                "STEP_20",
            }
        )

        action[
            "notes"
        ].add(
            secret_reviews
        )

        add_notebook_and_jobs(
            action,
            notebook,
        )


# ============================================================
# Validación de consistencia con readiness
#
# Un job REQUIRES_IMPLEMENTATION debe estar cubierto por al
# menos una acción maestra. Si no, no inferimos la causa:
# generamos una acción REVIEW para no perder el pendiente.
# ============================================================

jobs_covered_by_actions = set()

for action in actions.values():
    jobs_covered_by_actions.update(
        action[
            "affected_jobs"
        ]
    )

for row in job_rows:
    if (
        clean(
            row.get("job_readiness")
        )
        != "REQUIRES_IMPLEMENTATION"
    ):
        continue

    job = clean(
        row.get("job")
    )

    if not job:
        continue

    if job in jobs_covered_by_actions:
        continue

    action_key = (
        "JOB_READINESS_GAP::"
        + normalize(job)
    )

    action = get_action(
        action_key,
        "READINESS_REVIEW",
        f"Revisar pendiente no consolidado del job {job}",
    )

    action[
        "description"
    ] = (
        "El job figura como REQUIRES_IMPLEMENTATION en "
        "Paso 20 pero ninguno de sus pendientes quedó "
        "cubierto por una acción maestra conocida."
    )

    action[
        "priority"
    ] = "MEDIUM"

    action[
        "status"
    ] = "MANUAL_REVIEW_PENDING"

    action[
        "source_steps"
    ].update(
        {
            "STEP_19",
            "STEP_20",
            "STEP_21",
        }
    )

    action[
        "affected_jobs"
    ].add(
        job
    )

    action[
        "notes"
    ].add(
        clean(
            row.get("blocking_reason")
        )
    )


# ============================================================
# Convertir acciones a filas
# ============================================================

output_rows = []

for action_key, data in actions.items():
    affected_notebooks = sorted(
        data[
            "affected_notebooks"
        ],
        key=str.casefold,
    )

    affected_jobs = sorted(
        data[
            "affected_jobs"
        ],
        key=str.casefold,
    )

    output_rows.append({
        "action_key":
            action_key,

        "change_type":
            data[
                "action_type"
            ],

        "title":
            data[
                "title"
            ],

        "description":
            data[
                "description"
            ],

        "affected_notebooks":
            unique_join(
                affected_notebooks
            ),

        "affected_notebook_count":
            len(
                affected_notebooks
            ),

        "affected_jobs":
            unique_join(
                affected_jobs
            ),

        "affected_job_count":
            len(
                affected_jobs
            ),

        "config_change":
            data[
                "config_change"
            ],

        "code_change":
            data[
                "code_change"
            ],

        "target_value":
            data[
                "target_value"
            ],

        "priority":
            data[
                "priority"
            ],

        "status":
            data[
                "status"
            ],

        "source_steps":
            unique_join(
                sorted(
                    data[
                        "source_steps"
                    ]
                )
            ),

        "notes":
            unique_join(
                sorted(
                    data[
                        "notes"
                    ]
                )
            ),
    })


# ============================================================
# Prioridad
# ============================================================

PRIORITY_ORDER = {
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}

output_rows.sort(
    key=lambda row: (
        PRIORITY_ORDER.get(
            row[
                "priority"
            ],
            99,
        ),

        -int(
            row[
                "affected_job_count"
            ]
        ),

        normalize(
            row[
                "change_type"
            ]
        ),

        normalize(
            row[
                "title"
            ]
        ),
    )
)


# ============================================================
# Action ID
# ============================================================

for index, row in enumerate(
    output_rows,
    start=1,
):
    row[
        "action_id"
    ] = (
        f"ACT-{index:03d}"
    )


# ============================================================
# CSV
# ============================================================

fieldnames = [
    "action_id",
    "action_key",
    "change_type",
    "title",
    "description",
    "affected_notebooks",
    "affected_notebook_count",
    "affected_jobs",
    "affected_job_count",
    "config_change",
    "code_change",
    "target_value",
    "priority",
    "status",
    "source_steps",
    "notes",
]

OUTPUT_FILE.parent.mkdir(
    parents=True,
    exist_ok=True,
)

with OUTPUT_FILE.open(
    "w",
    newline="",
    encoding="utf-8-sig",
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=fieldnames,
    )

    writer.writeheader()
    writer.writerows(
        output_rows
    )


# ============================================================
# Resumen
# ============================================================

type_counter = Counter(
    row[
        "change_type"
    ]
    for row
    in output_rows
)

priority_counter = Counter(
    row[
        "priority"
    ]
    for row
    in output_rows
)

status_counter = Counter(
    row[
        "status"
    ]
    for row
    in output_rows
)

affected_jobs = {
    job
    for row in output_rows
    for job in split_multi_value(
        row.get("affected_jobs")
    )
}

affected_notebooks = {
    notebook
    for row in output_rows
    for notebook in split_multi_value(
        row.get("affected_notebooks")
    )
}


print("=" * 72)
print(
    "ASSESSMENT WORKSPACE - PASO 21"
)
print(
    "BACKLOG MAESTRO DE ACCIONES DE MIGRACION"
)
print("=" * 72)
print()

print(
    f"Acciones únicas identificadas    : "
    f"{len(output_rows)}"
)

print(
    f"Jobs impactados                  : "
    f"{len(affected_jobs)}"
)

print(
    f"Notebooks impactados             : "
    f"{len(affected_notebooks)}"
)

print()

print(
    "Resumen por tipo:"
)

for change_type in sorted(
    type_counter
):
    print(
        f" - {change_type:<28}: "
        f"{type_counter[change_type]}"
    )

print()

print(
    "Resumen por prioridad:"
)

for priority in [
    "HIGH",
    "MEDIUM",
    "LOW",
]:
    print(
        f" - {priority:<28}: "
        f"{priority_counter.get(priority, 0)}"
    )

print()

print(
    "Resumen por estado:"
)

for status in sorted(
    status_counter
):
    print(
        f" - {status:<28}: "
        f"{status_counter[status]}"
    )

print()

print(
    "Acciones:"
)

for row in output_rows:
    print(
        f" - "
        f"{row['action_id']}"
        f" | "
        f"{row['change_type']}"
        f" | "
        f"{row['title']}"
        f" | jobs="
        f"{row['affected_job_count']}"
        f" | notebooks="
        f"{row['affected_notebook_count']}"
    )

print()

print(
    f"Archivo generado: "
    f"{OUTPUT_FILE}"
)

print()
print("=" * 72)


if __name__ == "__main__":
    pass
