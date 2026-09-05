# Databricks notebook source
# ============================================================
# VALIDACIÓN DE OBJETOS REFERENCIADOS EN PRO
# Controles Volumétricos - Assessment PRO -> Unity Catalog
# ============================================================
#
# OBJETIVO
#   Obtener metadata técnica de 7 objetos utilizados por notebooks
#   funcionales, pero que no aparecieron en el inventario Hive
#   utilizado durante el assessment.
#
# IMPORTANTE
#   - SCRIPT DE SOLO LECTURA.
#   - NO crea, modifica, elimina ni registra objetos.
#   - NO ejecuta INSERT / UPDATE / DELETE / CREATE / DROP / ALTER.
#   - Puede ejecutarse en el Workspace PRO con una identidad que
#     tenga permisos de lectura/metadata sobre los objetos.
#
# RESULTADO
#   Para cada objeto intenta obtener:
#     * existencia / accesibilidad
#     * tipo reportado por Spark
#     * provider
#     * location
#     * database/schema
#     * SHOW CREATE TABLE completo
#     * DESCRIBE TABLE EXTENDED completo
#
# ============================================================

from pyspark.sql import Row
from pyspark.sql import functions as F
import re

# COMMAND ----------

OBJECTS_TO_VALIDATE = [
    "cfdi_conciliacion.conteos_plata",
    "cfdi_metadata.cancelaciones",
    "cfdi_plata_33.conteos_plata",
    "cfdi_plata_33.platacomprobante",
    "cfdi_plata_40.platacomprobante",
    "cfdi_nvomod_oro_negocio.concepto33",
    "cfdi_nvomod_oro_negocio.concepto40",
]

# COMMAND ----------

def quote_table_name(full_name: str) -> str:
    parts = full_name.split(".")
    if len(parts) not in (2, 3):
        raise ValueError(f"Nombre no esperado: {full_name}")

    safe = re.compile(r"^[A-Za-z0-9_]+$")
    for part in parts:
        if not safe.match(part):
            raise ValueError(f"Identificador no válido: {full_name}")

    return ".".join(f"`{p}`" for p in parts)


def describe_extended_as_dict(table_name: str):
    quoted = quote_table_name(table_name)
    rows = spark.sql(f"DESCRIBE TABLE EXTENDED {quoted}").collect()

    raw_lines = []
    metadata = {}

    for row in rows:
        d = row.asDict()
        col_name = str(d.get("col_name") or "").strip()
        data_type = str(d.get("data_type") or "").strip()
        comment = str(d.get("comment") or "").strip()

        raw_lines.append(" | ".join([col_name, data_type, comment]).rstrip(" |"))

        normalized = col_name.lower().replace(" ", "").replace("_", "")
        if normalized in {
            "type",
            "provider",
            "location",
            "database",
            "catalog",
            "owner",
            "tableproperties",
        }:
            metadata[col_name] = data_type

    return metadata, "\n".join(raw_lines)


def show_create_table(table_name: str):
    quoted = quote_table_name(table_name)
    rows = spark.sql(f"SHOW CREATE TABLE {quoted}").collect()

    values = []
    for row in rows:
        d = row.asDict()
        if d:
            values.append(str(next(iter(d.values()))))

    return "\n".join(values)


def get_ci(meta, wanted):
    for k, v in meta.items():
        if k.strip().lower() == wanted.lower():
            return v
    return ""

# COMMAND ----------

results = []
detail_output = []

for table_name in OBJECTS_TO_VALIDATE:
    print("=" * 100)
    print(f"VALIDANDO: {table_name}")
    print("=" * 100)

    status = "OK"
    error_type = ""
    error_message = ""
    object_type = ""
    provider = ""
    location = ""
    database = ""
    catalog = ""
    owner = ""
    ddl = ""
    describe_text = ""

    try:
        metadata, describe_text = describe_extended_as_dict(table_name)

        object_type = get_ci(metadata, "Type")
        provider = get_ci(metadata, "Provider")
        location = get_ci(metadata, "Location")
        database = get_ci(metadata, "Database")
        catalog = get_ci(metadata, "Catalog")
        owner = get_ci(metadata, "Owner")

        try:
            ddl = show_create_table(table_name)
        except Exception as ddl_exc:
            ddl = f"[SHOW CREATE TABLE ERROR] {type(ddl_exc).__name__}: {str(ddl_exc)}"

    except Exception as exc:
        status = "ERROR"
        error_type = type(exc).__name__
        error_message = str(exc)

    row_data = dict(
        object_name=table_name,
        status=status,
        object_type=object_type,
        provider=provider,
        location=location,
        database=database,
        catalog=catalog,
        owner=owner,
        error_type=error_type,
        error_message=error_message,
        show_create_table=ddl,
        describe_extended=describe_text,
    )

    results.append(Row(**row_data))
    detail_output.append(row_data)

# COMMAND ----------

result_df = spark.createDataFrame(results)

display(
    result_df.select(
        "object_name",
        "status",
        "object_type",
        "provider",
        "location",
        "database",
        "catalog",
        "owner",
        "error_type",
        "error_message",
    )
)

# COMMAND ----------

display(result_df)

# COMMAND ----------

print("\n" + "=" * 120)
print("RESULTADO VALIDACIÓN OBJETOS PRO")
print("=" * 120)

for item in detail_output:
    print(f"\nOBJECT: {item['object_name']}")
    print(f"STATUS: {item['status']}")
    print(f"TYPE: {item['object_type'] or '<NO IDENTIFICADO>'}")
    print(f"PROVIDER: {item['provider'] or '<NO IDENTIFICADO>'}")
    print(f"LOCATION: {item['location'] or '<NO IDENTIFICADA>'}")
    print(f"DATABASE: {item['database'] or '<NO IDENTIFICADA>'}")
    print(f"CATALOG: {item['catalog'] or '<NO IDENTIFICADO>'}")

    if item["status"] != "OK":
        print(f"ERROR_TYPE: {item['error_type']}")
        print(f"ERROR: {item['error_message']}")
    else:
        print("SHOW CREATE TABLE:")
        print(item["show_create_table"] or "<SIN DDL>")

    print("-" * 120)

# COMMAND ----------

total = result_df.count()
ok = result_df.filter(F.col("status") == "OK").count()
errors = total - ok

print("\n" + "=" * 80)
print("RESUMEN")
print("=" * 80)
print(f"Objetos solicitados      : {total}")
print(f"Objetos accesibles       : {ok}")
print(f"Objetos con error        : {errors}")
print("=" * 80)
