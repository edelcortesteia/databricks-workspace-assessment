# Databricks notebook source
# ============================================================
# INVENTARIO DE TABLAS EN UNITY CATALOG
# ============================================================
#
# Objetivo:
#   Extraer todas las tablas visibles para el usuario/SP que
#   ejecuta el notebook en Unity Catalog.
#
# Salida:
#   CSV con:
#     catalog
#     schema
#     table
#     full_table_name
#     table_type
#     data_source_format
#
# ============================================================

from pyspark.sql import functions as F
from functools import reduce

OUTPUT_PATH = "/dbfs/FileStore/assessment_uc/uc_tables_inventory.csv"

# ------------------------------------------------------------
# 1. Obtener catálogos disponibles
# ------------------------------------------------------------

catalogs_df = spark.sql("SHOW CATALOGS")

catalogs = [
    row.catalog
    for row in catalogs_df.collect()
    if row.catalog not in ("system",)
]

print(f"Catálogos encontrados: {len(catalogs)}")

for catalog in catalogs:
    print(f" - {catalog}")


# ------------------------------------------------------------
# 2. Recorrer catálogos y schemas
# ------------------------------------------------------------

inventory = []

for catalog in catalogs:

    try:
        schemas_df = spark.sql(f"SHOW SCHEMAS IN `{catalog}`")

        schemas = [
            row.databaseName
            for row in schemas_df.collect()
            if row.databaseName not in ("information_schema",)
        ]

    except Exception as e:
        print(f"[WARN] No se pudieron obtener schemas de {catalog}: {e}")
        continue

    print(f"\nCATALOG: {catalog}")
    print(f"Schemas encontrados: {len(schemas)}")

    for schema in schemas:

        try:
            tables_df = spark.sql(
                f"SHOW TABLES IN `{catalog}`.`{schema}`"
            )

            for row in tables_df.collect():

                table_name = row.tableName

                full_table_name = (
                    f"{catalog}.{schema}.{table_name}"
                )

                inventory.append({
                    "catalog": catalog,
                    "schema": schema,
                    "table": table_name,
                    "full_table_name": full_table_name
                })

        except Exception as e:
            print(
                f"[WARN] No se pudieron obtener tablas de "
                f"{catalog}.{schema}: {e}"
            )


# ------------------------------------------------------------
# 3. Crear DataFrame
# ------------------------------------------------------------

if len(inventory) == 0:
    raise Exception(
        "No se encontraron tablas visibles en Unity Catalog."
    )

inventory_df = spark.createDataFrame(inventory)


# ------------------------------------------------------------
# 4. Enriquecer usando system.information_schema.tables
# ------------------------------------------------------------

try:

    info_schema_df = spark.sql("""
        SELECT
            table_catalog AS catalog,
            table_schema  AS schema,
            table_name    AS table,
            table_type,
            data_source_format
        FROM system.information_schema.tables
    """)

    inventory_df = (
        inventory_df
        .join(
            info_schema_df,
            on=["catalog", "schema", "table"],
            how="left"
        )
    )

except Exception as e:

    print(
        "[WARN] No fue posible consultar "
        "system.information_schema.tables"
    )
    print(e)

    inventory_df = (
        inventory_df
        .withColumn("table_type", F.lit(None).cast("string"))
        .withColumn(
            "data_source_format",
            F.lit(None).cast("string")
        )
    )


# ------------------------------------------------------------
# 5. Normalización
# ------------------------------------------------------------

inventory_df = (
    inventory_df
    .withColumn(
        "catalog_normalized",
        F.lower(F.col("catalog"))
    )
    .withColumn(
        "schema_normalized",
        F.lower(F.col("schema"))
    )
    .withColumn(
        "table_normalized",
        F.lower(F.col("table"))
    )
    .withColumn(
        "full_table_name_normalized",
        F.lower(F.col("full_table_name"))
    )
    .select(
        "catalog",
        "schema",
        "table",
        "full_table_name",
        "table_type",
        "data_source_format",
        "catalog_normalized",
        "schema_normalized",
        "table_normalized",
        "full_table_name_normalized"
    )
    .orderBy(
        "catalog",
        "schema",
        "table"
    )
)


# ------------------------------------------------------------
# 6. Estadísticas
# ------------------------------------------------------------

total_tables = inventory_df.count()

print("\n========================================")
print(" INVENTARIO UNITY CATALOG")
print("========================================")
print(f"Total tablas encontradas : {total_tables}")
print("========================================")

display(inventory_df)


# ------------------------------------------------------------
# 7. Guardar CSV
# ------------------------------------------------------------

(
    inventory_df
    .coalesce(1)
    .write
    .mode("overwrite")
    .option("header", True)
    .csv(OUTPUT_PATH.replace("/dbfs", "dbfs:"))
)

print(f"\nInventario generado en:")
print(OUTPUT_PATH)