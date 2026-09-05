#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv

# ============================================================
# Assessment Workspace - Paso 19 V3
#
# Preserva el contrato ORIGINAL del Paso 19:
#   - mismas entradas
#   - mismas columnas de salida
#   - misma lógica de STORAGE / HARDCODE / SECRET / WORKING_TABLE
#   - misma semántica migration_ready / total_changes
#
# ÚNICO cambio funcional:
#   incorpora los nuevos estados/acciones producidos por
#   Paso 14 V5.1 para objetos faltantes en Unity Catalog.
# ============================================================

JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
TABLE_FINAL_FILE = Path("output/table_hive_reconciliation_final.csv")
STORAGE_ANALYSIS_FILE = Path("output/storage_migration_analysis.csv")
HARDCODES_FILE = Path("output/environment_hardcodes.csv")
SECRETS_FILE = Path("output/secret_usage_analysis.csv")
DYNAMIC_WORKING_TABLES_FILE = Path("output/dynamic_working_tables.csv")
OUTPUT_FILE = Path("output/notebook_migration_backlog.csv")


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def truth(value):
    return normalize(value) in {"true", "1", "yes", "y", "si", "sí"}


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def split_multi_value(value):
    value = clean(value)
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


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


def unique_join(values, separator=" | "):
    return separator.join(unique(values))


required_files = [
    JOB_INVENTORY_FILE,
    TABLE_FINAL_FILE,
    STORAGE_ANALYSIS_FILE,
    HARDCODES_FILE,
    SECRETS_FILE,
    DYNAMIC_WORKING_TABLES_FILE,
]

missing_files = [str(path) for path in required_files if not path.exists()]
if missing_files:
    print("ERROR: faltan archivos requeridos:")
    for path in missing_files:
        print(f" - {path}")
    raise SystemExit(1)


job_rows = read_csv(JOB_INVENTORY_FILE)
table_rows = read_csv(TABLE_FINAL_FILE)
storage_rows = read_csv(STORAGE_ANALYSIS_FILE)
hardcode_rows = read_csv(HARDCODES_FILE)
secret_rows = read_csv(SECRETS_FILE)
dynamic_working_rows = read_csv(DYNAMIC_WORKING_TABLES_FILE)


# ============================================================
# Base notebook -> jobs
# ============================================================

notebook_jobs = defaultdict(set)

for row in job_rows:
    notebook = clean(row.get("notebook"))
    job = clean(row.get("job"))

    if not notebook:
        continue

    if job:
        notebook_jobs[notebook].add(job)


# ============================================================
# Acumulador
# ============================================================

backlog = {}


def get_item(notebook):
    if notebook not in backlog:
        backlog[notebook] = {
            "table_changes": [],
            "working_table_changes": [],
            "storage_changes": [],
            "config_changes": [],
            "hardcode_changes": [],
            "secret_reviews": [],
            "manual_reviews": [],
            "notes": [],
        }
    return backlog[notebook]


for notebook in notebook_jobs:
    get_item(notebook)


# ============================================================
# 1. Tablas Hive / Unity Catalog - Paso 14 V5.1
# ============================================================

for row in table_rows:
    if not truth(row.get("usada_en_notebook")):
        continue

    notebooks = split_multi_value(row.get("notebooks"))
    if not notebooks:
        continue

    pro_table = clean(row.get("tabla_pro"))
    uc_table = clean(row.get("tabla_uc"))
    reconciliation_status = clean(row.get("reconciliation_status"))
    mapping_status = clean(row.get("fuente_mapeo"))
    migration_action = clean(row.get("migration_action"))
    object_type = clean(row.get("object_type_pro")) or "TABLE"
    trace_statuses = clean(row.get("trace_statuses"))
    config_path = clean(row.get("configuracion_json_uc"))

    for notebook in notebooks:
        item = get_item(notebook)

        # ----------------------------------------------------
        # NUEVO V3: tabla usada, mapeada, pero ausente en UC
        # ----------------------------------------------------
        if (
            reconciliation_status == "EXISTS_AND_USED_UC_NOT_FOUND"
            or migration_action == "REGISTER_OR_MIGRATE_TO_UC"
        ):
            item["table_changes"].append(
                f"{pro_table} -> {uc_table or '[SIN UC]'} "
                f"[REGISTER_OR_MIGRATE_TO_UC]"
            )
            item["notes"].append(
                f"{pro_table} es una tabla {object_type} utilizada "
                f"funcionalmente y no fue encontrada en el inventario "
                f"físico de Unity Catalog. Solicitar su registro/migración "
                f"como {uc_table or '[destino UC por definir]'}."
            )
            continue

        # ----------------------------------------------------
        # NUEVO V3: VIEW usada y ausente en UC
        # ----------------------------------------------------
        if (
            reconciliation_status == "USED_VIEW_UC_NOT_FOUND"
            or migration_action == "CREATE_VIEW_IN_UC"
        ):
            item["table_changes"].append(
                f"{pro_table} -> {uc_table or '[SIN UC]'} "
                f"[CREATE_VIEW_IN_UC]"
            )
            item["notes"].append(
                f"{pro_table} es una VIEW persistente utilizada "
                f"funcionalmente y no existe en UC. Recrear la vista como "
                f"{uc_table or '[destino UC por definir]'} y actualizar "
                f"la configuración correspondiente."
            )
            continue

        # ----------------------------------------------------
        # Tabla referenciada pero no presente físicamente en PRO
        # ----------------------------------------------------
        if reconciliation_status == "REFERENCED_NOT_FOUND":
            item["table_changes"].append(
                f"{pro_table} -> {uc_table or '[SIN UC]'} "
                f"[REFERENCED_NOT_FOUND]"
            )
            item["manual_reviews"].append(
                f"Tabla Hive no encontrada: {pro_table}"
            )
            item["notes"].append(
                f"Validar la referencia {pro_table}; el código la utiliza "
                f"pero no fue encontrada en el snapshot físico de Hive."
            )
            continue

        # ----------------------------------------------------
        # Mapping UC ambiguo
        # ----------------------------------------------------
        if (
            mapping_status == "AMBIGUOUS_UC_MAPPING"
            or reconciliation_status == "EXISTS_AND_USED_AMBIGUOUS_UC_MAPPING"
            or migration_action == "RESOLVE_UC_MAPPING"
        ):
            item["table_changes"].append(
                f"{pro_table} -> {uc_table or '[AMBIGUO]'} "
                f"[AMBIGUOUS_UC_MAPPING]"
            )
            item["manual_reviews"].append(
                f"Mapping UC ambiguo: {pro_table}"
            )
            item["notes"].append(
                f"Resolver manualmente el destino UC de {pro_table}; "
                f"se detectaron múltiples candidatos de configuración."
            )
            continue

        # ----------------------------------------------------
        # Tabla usada sin mapping UC
        # ----------------------------------------------------
        if (
            mapping_status == "NO_UC_MAPPING"
            or reconciliation_status == "EXISTS_AND_USED_NO_UC_MAPPING"
            or migration_action == "DEFINE_UC_MAPPING"
        ):
            item["table_changes"].append(
                f"{pro_table} -> [SIN UC] [NO_UC_MAPPING]"
            )
            item["notes"].append(
                f"Definir el destino Unity Catalog de {pro_table}; "
                f"la tabla existe/está referenciada en PRO pero el análisis "
                f"no encontró mapping UC en la configuración objetivo."
            )
            continue

        unresolved_trace = any(
            status and not status.startswith("RESOLVED_")
            for status in split_multi_value(trace_statuses)
        )

        if unresolved_trace:
            item["manual_reviews"].append(
                f"Tabla dinámica: {pro_table}"
            )
            item["notes"].append(
                f"Revisar manualmente la resolución dinámica de "
                f"{pro_table}; existe una traza activa no completamente resuelta."
            )

        if config_path:
            pass


# ============================================================
# 2. Storage - Paso 15
# ============================================================

for row in storage_rows:
    notebook = clean(row.get("notebook"))
    if not notebook:
        continue

    if clean(row.get("requires_action")) != "YES":
        continue

    item = get_item(notebook)

    migration_status = clean(row.get("migration_status"))
    reference = clean(row.get("storage_reference"))
    action = clean(row.get("recommended_action"))

    item["storage_changes"].append(
        f"{reference} [{migration_status}]"
    )

    if action:
        item["notes"].append(action)

    if migration_status == "CONFIG_ABFSS_URI_REQUIRED":
        config_path = clean(row.get("config_path"))
        if config_path:
            item["config_changes"].append(
                f"{config_path} -> URI ABFSS completa"
            )

    if migration_status == "ENV_CONFIG_PATH_REQUIRED":
        item["config_changes"].append(
            "Usar CV_EXPLOTACION_CONFIG_FILE_PATH"
        )


# ============================================================
# 3. Hardcodes nuevos - Paso 16
# ============================================================

for row in hardcode_rows:
    notebook = clean(row.get("notebook"))
    if not notebook:
        continue

    if clean(row.get("already_covered_by")):
        continue

    requires_action = clean(row.get("requires_action"))
    if requires_action not in {"YES", "REVIEW"}:
        continue

    item = get_item(notebook)

    hardcode_type = clean(row.get("hardcode_type"))
    value = clean(row.get("hardcoded_value"))
    recommended_action = clean(row.get("recommended_action"))

    item["hardcode_changes"].append(
        f"{hardcode_type}: {value}"
    )

    if requires_action == "REVIEW":
        item["manual_reviews"].append(
            f"Hardcode: {hardcode_type}"
        )

    if recommended_action:
        item["notes"].append(recommended_action)


# ============================================================
# 4. Secrets - Paso 17
# ============================================================

for row in secret_rows:
    notebook = clean(row.get("notebook"))
    if not notebook:
        continue

    if clean(row.get("requires_review")) != "YES":
        continue

    item = get_item(notebook)

    scope_expr = clean(row.get("scope_expression"))
    key_expr = clean(row.get("key_expression"))

    item["secret_reviews"].append(
        f"{scope_expr or '[scope]'} / {key_expr or '[key]'}"
    )
    item["manual_reviews"].append(
        "Secret no resuelto"
    )
    item["notes"].append(
        "Revisar la resolución del Secret Scope/key; el análisis "
        "no logró determinar completamente la referencia utilizada "
        "por el notebook."
    )


# ============================================================
# 5. Working tables dinámicas - Paso 18
# ============================================================

for row in dynamic_working_rows:
    notebook = clean(row.get("notebook"))
    if not notebook:
        continue

    if clean(row.get("requires_action")) != "YES":
        continue

    item = get_item(notebook)

    current_expression = clean(row.get("current_expression"))
    target_expression = clean(row.get("target_expression"))
    config_path = clean(row.get("config_path"))
    configured_work_schema = clean(row.get("configured_work_schema"))
    migration_status = clean(row.get("migration_status"))

    item["working_table_changes"].append(
        f"{current_expression} -> {target_expression}"
    )

    if config_path:
        if migration_status == "SCHEMA_CONFIGURATION_REQUIRED":
            item["config_changes"].append(
                f"{config_path} -> u_impin_convol.cv_work"
            )
        else:
            item["config_changes"].append(
                f"{config_path}"
                + (
                    f" -> {configured_work_schema}"
                    if configured_work_schema
                    else ""
                )
            )

    item["notes"].append(
        "Agregar/usar EsquemasTrabajoDbks_UC.Default en el JSON UC "
        "apuntando a u_impin_convol.cv_work y modificar el notebook "
        "para reemplazar el esquema hardcodeado default por la clave "
        "de configuración al construir dinámicamente el nombre de la tabla."
    )


# ============================================================
# Salida - contrato original
# ============================================================

output_rows = []

for notebook, data in backlog.items():
    jobs = sorted(
        notebook_jobs.get(notebook, []),
        key=str.casefold,
    )

    table_changes = unique(data["table_changes"])
    working_changes = unique(data["working_table_changes"])
    storage_changes = unique(data["storage_changes"])
    config_changes = unique(data["config_changes"])
    hardcode_changes = unique(data["hardcode_changes"])
    secret_reviews = unique(data["secret_reviews"])
    manual_reviews = unique(data["manual_reviews"])
    notes = unique(data["notes"])

    # Mantiene la regla original:
    # config_changes NO suma por separado.
    total_changes = (
        len(table_changes)
        + len(working_changes)
        + len(storage_changes)
        + len(hardcode_changes)
        + len(secret_reviews)
        + len(manual_reviews)
    )

    migration_ready = "YES" if total_changes == 0 else "NO"

    output_rows.append({
        "job": unique_join(jobs),
        "notebook": notebook,
        "table_changes": unique_join(table_changes),
        "working_table_changes": unique_join(working_changes),
        "storage_changes": unique_join(storage_changes),
        "config_changes": unique_join(config_changes),
        "hardcode_changes": unique_join(hardcode_changes),
        "secret_reviews": unique_join(secret_reviews),
        "manual_reviews": unique_join(manual_reviews),
        "total_changes": total_changes,
        "migration_ready": migration_ready,
        "notes": unique_join(notes),
    })


output_rows.sort(
    key=lambda row: (
        row["migration_ready"] == "YES",
        -int(row["total_changes"]),
        normalize(row["notebook"]),
    )
)


fieldnames = [
    "job",
    "notebook",
    "table_changes",
    "working_table_changes",
    "storage_changes",
    "config_changes",
    "hardcode_changes",
    "secret_reviews",
    "manual_reviews",
    "total_changes",
    "migration_ready",
    "notes",
]

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

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
    writer.writerows(output_rows)


# ============================================================
# Resumen
# ============================================================

ready_counter = Counter(
    row["migration_ready"]
    for row in output_rows
)

changes_counter = Counter()

for row in output_rows:
    if clean(row["table_changes"]):
        changes_counter["TABLE"] += 1
    if clean(row["working_table_changes"]):
        changes_counter["WORKING_TABLE"] += 1
    if clean(row["storage_changes"]):
        changes_counter["STORAGE"] += 1
    if clean(row["config_changes"]):
        changes_counter["CONFIG"] += 1
    if clean(row["hardcode_changes"]):
        changes_counter["HARDCODE"] += 1
    if clean(row["secret_reviews"]):
        changes_counter["SECRET_REVIEW"] += 1
    if clean(row["manual_reviews"]):
        changes_counter["MANUAL_REVIEW"] += 1


print("=" * 72)
print("ASSESSMENT WORKSPACE - PASO 19 V3")
print("BACKLOG CONSOLIDADO DE MIGRACION POR NOTEBOOK")
print("=" * 72)
print()

print(f"Notebooks incluidos             : {len(output_rows)}")
print()
print("Estado de preparación:")
print(f" - READY                        : {ready_counter.get('YES', 0)}")
print(f" - REQUIERE CAMBIOS             : {ready_counter.get('NO', 0)}")
print()
print("Notebooks impactados por tipo:")

for change_type in [
    "TABLE",
    "WORKING_TABLE",
    "STORAGE",
    "CONFIG",
    "HARDCODE",
    "SECRET_REVIEW",
    "MANUAL_REVIEW",
]:
    print(
        f" - {change_type:<28}: "
        f"{changes_counter.get(change_type, 0)}"
    )

print()
pending_rows = [
    row for row in output_rows
    if row["migration_ready"] == "NO"
]

print("Notebooks que requieren atención:")

if pending_rows:
    for row in pending_rows:
        print(
            f" - {row['notebook']} "
            f"| cambios={row['total_changes']}"
        )
else:
    print(" - Ninguno")

print()
print(f"Archivo generado: {OUTPUT_FILE}")
print()
print("=" * 72)
