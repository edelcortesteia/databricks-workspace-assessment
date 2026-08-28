from pathlib import Path
from collections import Counter
import csv

INPUT_FILE = Path("snapshot/ddl/inventario_tablas_csv/inventario_tablas.csv")
OUTPUT_FILE = Path("output/hive_table_inventory.csv")

EXPECTED_INPUT_COLUMNS = ["schema", "tabla", "tiene_ddl"]
OUTPUT_COLUMNS = [
    "schema",
    "tabla",
    "full_name",
    "ddl_available",
    "physical_status",
]


def parse_bool(value):
    normalized = str(value or "").strip().casefold()

    if normalized in {"true", "1", "yes", "si", "sí"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False

    raise ValueError(f"Valor no reconocido para tiene_ddl: {value!r}")


def main():
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 10")
    print("INVENTARIO FISICO DE TABLAS HIVE")
    print("=" * 70)
    print()

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            "No existe el inventario generado por el extractor:\n"
            f"{INPUT_FILE.resolve()}"
        )

    with INPUT_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        missing_columns = [
            column
            for column in EXPECTED_INPUT_COLUMNS
            if column not in (reader.fieldnames or [])
        ]

        if missing_columns:
            raise RuntimeError(
                "El inventario Hive no contiene las columnas esperadas: "
                + ", ".join(missing_columns)
            )

        input_rows = list(reader)

    output_rows = []
    seen = set()
    duplicates = 0
    invalid_rows = []

    for row_number, row in enumerate(input_rows, start=2):
        schema = (row.get("schema") or "").strip()
        table = (row.get("tabla") or "").strip()
        raw_ddl = (row.get("tiene_ddl") or "").strip()

        if not schema or not table:
            invalid_rows.append(
                (row_number, schema, table, "schema/tabla vacio")
            )
            continue

        try:
            ddl_available = parse_bool(raw_ddl)
        except ValueError:
            invalid_rows.append(
                (row_number, schema, table, f"tiene_ddl={raw_ddl!r}")
            )
            continue

        # Hive/Spark no distingue nombres por mayúsculas/minúsculas en el
        # escenario normal. La llave casefold evita duplicados accidentales
        # sin alterar el nombre original exportado por el extractor.
        key = (schema.casefold(), table.casefold())

        if key in seen:
            duplicates += 1
            continue

        seen.add(key)

        output_rows.append({
            "schema": schema,
            "tabla": table,
            "full_name": f"{schema}.{table}",
            "ddl_available": str(ddl_available).lower(),
            "physical_status": (
                "EXISTS_WITH_DDL"
                if ddl_available
                else "EXISTS_DDL_UNAVAILABLE"
            ),
        })

    if invalid_rows:
        examples = "\n".join(
            f" - fila {n}: schema={s!r}, tabla={t!r}, {reason}"
            for n, s, t, reason in invalid_rows[:20]
        )

        raise RuntimeError(
            "Se encontraron filas inválidas en el inventario Hive.\n"
            f"Filas inválidas: {len(invalid_rows)}\n"
            f"{examples}"
        )

    output_rows.sort(
        key=lambda row: (
            row["schema"].casefold(),
            row["tabla"].casefold(),
        )
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    status_counts = Counter(
        row["physical_status"]
        for row in output_rows
    )
    schemas = {
        row["schema"].casefold(): row["schema"]
        for row in output_rows
    }

    print("--- Fuente ---")
    print(f"Archivo extractor             : {INPUT_FILE}")
    print(f"Registros leidos              : {len(input_rows)}")
    print()
    print("--- Inventario normalizado ---")
    print(f"Tablas fisicas unicas         : {len(output_rows)}")
    print(f"Schemas unicos                : {len(schemas)}")
    print(f"Duplicados omitidos           : {duplicates}")
    print(f"Filas invalidas               : {len(invalid_rows)}")
    print()
    print("Resumen por estado fisico:")
    for status in sorted(status_counts):
        print(f" - {status:<24}: {status_counts[status]}")

    print()
    print("Resumen por schema:")
    schema_counts = Counter(row["schema"] for row in output_rows)
    for schema in sorted(schema_counts, key=str.casefold):
        print(f" - {schema:<30}: {schema_counts[schema]}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE.resolve()}")
    print(f"Registros generados: {len(output_rows)}")
    print()
    print("=" * 70)
    print("RESULTADO: INVENTARIO FISICO HIVE GENERADO CORRECTAMENTE")
    print("=" * 70)


if __name__ == "__main__":
    main()