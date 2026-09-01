from pathlib import Path
from collections import defaultdict, Counter
import csv
import re

TABLE_REFERENCES_FILE = Path("output/table_references.csv")
DYNAMIC_SOURCES_FILE = Path("output/dynamic_variable_sources.csv")
HIVE_INVENTORY_FILE = Path("output/hive_table_inventory.csv")
OUTPUT_FILE = Path("output/table_hive_reconciliation.csv")

OUTPUT_COLUMNS = [
    "object_key",
    "source_kind",
    "data_source",
    "table_reference",
    "normalized_reference",
    "schema",
    "tabla",
    "name_format",
    "used_in_code",
    "physical_exists",
    "ddl_available",
    "physical_status",
    "reconciliation_status",
    "occurrences",
    "reference_types",
    "jobs",
    "notebooks",
    "dynamic_variables",
    "dynamic_source_types",
    "dynamic_source_expressions",
    "notes",
]


def clean(value):
    return str(value or "").strip()


def normalize(value):
    return clean(value).replace("`", "").strip().casefold()


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo requerido: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def split_jobs(value):
    return [
        item.strip()
        for item in clean(value).split("|")
        if item.strip()
    ]


def unique_join(values, separator=" | "):
    seen = set()
    result = []

    for value in values:
        value = clean(value)
        if not value:
            continue

        key = value.casefold()
        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return separator.join(result)


def bool_text(value):
    return "true" if str(value).strip().casefold() in {"true", "1", "yes", "si", "sí"} else "false"


def normalize_hive_reference(reference):
    """
    Normalización estricta. No hace matching por basename.

    hive_metastore.schema.table -> schema.table
    schema.table                -> schema.table
    table                       -> table (no se resuelve automáticamente)
    """
    value = clean(reference).replace("`", "")
    parts = [p.strip() for p in value.split(".") if p.strip()]

    if len(parts) == 3 and parts[0].casefold() == "hive_metastore":
        parts = parts[1:]

    return ".".join(parts).casefold()


def extract_dynamic_variables(reference):
    return re.findall(r"\$\{([A-Za-z_]\w*)\}", clean(reference))


def main():
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 11")
    print("CRUCE DE REFERENCIAS DE TABLAS VS INVENTARIO HIVE - V2")
    print("=" * 70)
    print()

    refs = read_csv(TABLE_REFERENCES_FILE)
    dynamic_sources = read_csv(DYNAMIC_SOURCES_FILE)
    hive_rows = read_csv(HIVE_INVENTORY_FILE)

    # --------------------------------------------------------
    # Índice físico Hive: match exacto por schema.tabla
    # --------------------------------------------------------
    hive_index = {}
    duplicate_hive_keys = []

    for row in hive_rows:
        full_name = clean(row.get("full_name"))
        if not full_name:
            full_name = f"{clean(row.get('schema'))}.{clean(row.get('tabla'))}"

        key = normalize_hive_reference(full_name)

        if key in hive_index:
            duplicate_hive_keys.append(key)
            continue

        hive_index[key] = row

    if duplicate_hive_keys:
        raise RuntimeError(
            "El inventario físico contiene nombres Hive duplicados tras normalización: "
            + ", ".join(sorted(set(duplicate_hive_keys))[:20])
        )

    # --------------------------------------------------------
    # Índice de orígenes dinámicos por (notebook, variable)
    # --------------------------------------------------------
    source_index = defaultdict(list)

    for row in dynamic_sources:
        notebook = clean(row.get("notebook"))
        variable = clean(row.get("variable"))

        if notebook and variable:
            source_index[(notebook, variable)].append(row)

    # --------------------------------------------------------
    # Agregación de referencias del código
    # --------------------------------------------------------
    ref_groups = {}

    for row in refs:
        notebook = clean(row.get("notebook"))
        table_reference = clean(row.get("table_reference"))
        name_format = clean(row.get("name_format"))
        reference_type = clean(row.get("reference_type"))
        jobs = split_jobs(row.get("jobs"))
        data_source = clean(row.get("data_source")) or "UNKNOWN"

        if name_format == "TEMP_VIEW":
            normalized_reference = normalize(table_reference)
            source_kind = "TEMP_VIEW"
            object_key = f"{data_source}::TEMP_VIEW::{normalized_reference}"

        elif name_format in {"DYNAMIC_VARIABLE", "DYNAMIC_TABLE_EXPRESSION"}:
            normalized_reference = normalize(table_reference)
            source_kind = "DYNAMIC_REFERENCE"
            # Mantener expresión completa; no colapsar distintas expresiones
            # dinámicas que reutilicen la misma variable.
            object_key = f"{data_source}::DYNAMIC::{normalized_reference}"

        else:
            normalized_reference = normalize_hive_reference(table_reference)
            source_kind = "PHYSICAL_REFERENCE"
            object_key = f"{data_source}::PHYSICAL::{normalized_reference}"

        if object_key not in ref_groups:
            ref_groups[object_key] = {
                "object_key": object_key,
                "source_kind": source_kind,
                "data_source": data_source,
                "table_reference": table_reference,
                "normalized_reference": normalized_reference,
                "name_format": name_format,
                "occurrences": 0,
                "reference_types": [],
                "jobs": [],
                "notebooks": [],
                "dynamic_variables": [],
                "dynamic_source_types": [],
                "dynamic_source_expressions": [],
            }

        group = ref_groups[object_key]
        group["occurrences"] += 1
        group["reference_types"].append(reference_type)
        group["jobs"].extend(jobs)
        group["notebooks"].append(notebook)

        if source_kind == "DYNAMIC_REFERENCE":
            variables = extract_dynamic_variables(table_reference)
            group["dynamic_variables"].extend(variables)

            for variable in variables:
                for src in source_index.get((notebook, variable), []):
                    group["dynamic_source_types"].append(
                        clean(src.get("source_type"))
                    )
                    group["dynamic_source_expressions"].append(
                        clean(src.get("source_expression"))
                    )

    output_rows = []
    physical_keys_used = set()

    # --------------------------------------------------------
    # Referencias encontradas en código
    # --------------------------------------------------------
    for key in sorted(ref_groups, key=str.casefold):
        group = ref_groups[key]
        source_kind = group["source_kind"]
        normalized_reference = group["normalized_reference"]

        schema = ""
        table = ""
        physical_exists = "false"
        ddl_available = ""
        physical_status = ""
        notes = ""

        data_source = group["data_source"]

        # --------------------------------------------------------
        # Primero se decide el motor/origen de datos.
        # JDBC es una dependencia externa al Hive Metastore y NO
        # debe compararse contra las 207 tablas físicas Hive.
        # --------------------------------------------------------
        if data_source == "JDBC":
            reconciliation_status = "OUT_OF_SCOPE_JDBC"
            notes = (
                "Referencia de tabla utilizada mediante JDBC. Se conserva como "
                "dependencia externa del job, pero queda fuera del cruce HMS -> UC."
            )

        elif data_source == "UNKNOWN":
            reconciliation_status = "DATA_SOURCE_UNKNOWN_REQUIRES_REVIEW"
            notes = (
                "Se detectó una referencia de tabla, pero el Paso 08 no pudo "
                "determinar de forma segura si pertenece a Spark/Hive o JDBC."
            )

        elif source_kind == "TEMP_VIEW":
            reconciliation_status = "TEMP_VIEW_NO_HIVE_LOOKUP"
            notes = "Vista temporal Spark; no requiere existencia como tabla física Hive."

        elif source_kind == "DYNAMIC_REFERENCE":
            reconciliation_status = "DYNAMIC_PENDING_TRACE"
            notes = (
                "Referencia dinámica Spark/Hive. Paso 09 identifica su origen, "
                "pero el valor físico final se resolverá en la trazabilidad/configuración."
            )

        else:
            parts = [p for p in normalized_reference.split(".") if p]

            if len(parts) == 1:
                reconciliation_status = "UNQUALIFIED_REFERENCE_REQUIRES_REVIEW"
                table = parts[0]
                notes = (
                    "Referencia Spark/Hive de una sola parte. No se hace matching "
                    "por basename para evitar falsos positivos."
                )

            elif len(parts) == 2:
                schema, table = parts
                hive_row = hive_index.get(normalized_reference)

                if hive_row is None:
                    reconciliation_status = "REFERENCED_NOT_FOUND"
                    notes = (
                        "Referencia literal Spark/Hive no encontrada por match exacto "
                        "schema.tabla en el snapshot de Hive Metastore."
                    )

                else:
                    physical_keys_used.add(normalized_reference)
                    physical_exists = "true"
                    ddl_available = bool_text(hive_row.get("ddl_available"))
                    physical_status = clean(hive_row.get("physical_status"))

                    if physical_status == "EXISTS_DDL_UNAVAILABLE":
                        reconciliation_status = "EXISTS_DDL_UNAVAILABLE"
                        notes = (
                            "La tabla existe en Hive, pero el extractor no pudo recuperar "
                            "su DDL."
                        )
                    else:
                        reconciliation_status = "EXISTS_AND_USED"

            else:
                reconciliation_status = "UNSUPPORTED_NAME_FORMAT_REQUIRES_REVIEW"
                notes = "Formato de nombre Spark/Hive no soportado por el cruce estricto."

        output_rows.append({
            "object_key": group["object_key"],
            "source_kind": source_kind,
            "data_source": group["data_source"],
            "table_reference": group["table_reference"],
            "normalized_reference": normalized_reference,
            "schema": schema,
            "tabla": table,
            "name_format": group["name_format"],
            "used_in_code": "true",
            "physical_exists": physical_exists,
            "ddl_available": ddl_available,
            "physical_status": physical_status,
            "reconciliation_status": reconciliation_status,
            "occurrences": group["occurrences"],
            "reference_types": unique_join(group["reference_types"]),
            "jobs": unique_join(group["jobs"]),
            "notebooks": unique_join(group["notebooks"]),
            "dynamic_variables": unique_join(group["dynamic_variables"]),
            "dynamic_source_types": unique_join(group["dynamic_source_types"]),
            "dynamic_source_expressions": unique_join(
                group["dynamic_source_expressions"]
            ),
            "notes": notes,
        })

    # --------------------------------------------------------
    # Tablas físicas que existen, pero no fueron referenciadas
    # literalmente por los notebooks analizados
    # --------------------------------------------------------
    for hive_key in sorted(hive_index, key=str.casefold):
        if hive_key in physical_keys_used:
            continue

        hive_row = hive_index[hive_key]
        schema = clean(hive_row.get("schema"))
        table = clean(hive_row.get("tabla"))
        ddl_available = bool_text(hive_row.get("ddl_available"))
        physical_status = clean(hive_row.get("physical_status"))

        if physical_status == "EXISTS_DDL_UNAVAILABLE":
            reconciliation_status = "EXISTS_DDL_UNAVAILABLE"
            notes = (
                "La tabla existe en Hive y no fue encontrada como referencia literal "
                "en los notebooks en alcance; además, su DDL no estuvo disponible."
            )
        else:
            reconciliation_status = "EXISTS_NOT_USED"
            notes = (
                "La tabla existe físicamente en Hive, pero no fue encontrada como "
                "referencia literal en los notebooks en alcance."
            )

        output_rows.append({
            "object_key": f"PHYSICAL::{hive_key}",
            "source_kind": "PHYSICAL_INVENTORY",
            "data_source": "SPARK_HIVE",
            "table_reference": clean(hive_row.get("full_name")),
            "normalized_reference": hive_key,
            "schema": schema,
            "tabla": table,
            "name_format": "TWO_PART_NAME",
            "used_in_code": "false",
            "physical_exists": "true",
            "ddl_available": ddl_available,
            "physical_status": physical_status,
            "reconciliation_status": reconciliation_status,
            "occurrences": 0,
            "reference_types": "",
            "jobs": "",
            "notebooks": "",
            "dynamic_variables": "",
            "dynamic_source_types": "",
            "dynamic_source_expressions": "",
            "notes": notes,
        })

    output_rows.sort(
        key=lambda row: (
            row["reconciliation_status"].casefold(),
            row["normalized_reference"].casefold(),
            row["source_kind"].casefold(),
        )
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    status_counts = Counter(
        row["reconciliation_status"]
        for row in output_rows
    )

    physical_used = sum(
        1
        for row in output_rows
        if row["source_kind"] == "PHYSICAL_REFERENCE"
        and row["data_source"] == "SPARK_HIVE"
        and row["physical_exists"] == "true"
    )

    referenced_not_found = sum(
        1
        for row in output_rows
        if row["reconciliation_status"] == "REFERENCED_NOT_FOUND"
    )

    jdbc_objects = sum(
        1
        for row in output_rows
        if row["reconciliation_status"] == "OUT_OF_SCOPE_JDBC"
    )

    source_counts = Counter(
        clean(row.get("data_source")) or "UNKNOWN"
        for row in refs
    )

    print("--- Entradas ---")
    print(f"Referencias de código         : {len(refs)}")
    print(f"Orígenes dinámicos            : {len(dynamic_sources)}")
    print(f"Tablas físicas Hive           : {len(hive_rows)}")
    print()
    print("Referencias por origen:")
    for data_source in sorted(source_counts):
        print(f" - {data_source:<20}: {source_counts[data_source]}")
    print()
    print("--- Reconciliación ---")
    print(f"Objetos consolidados          : {len(output_rows)}")
    print(f"Tablas físicas usadas         : {physical_used}")
    print(f"Referencias no encontradas    : {referenced_not_found}")
    print(f"Objetos JDBC fuera de alcance : {jdbc_objects}")
    print()
    print("Resumen por estado:")
    for status in sorted(status_counts):
        print(f" - {status:<38}: {status_counts[status]}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE.resolve()}")
    print(f"Registros generados: {len(output_rows)}")
    print()
    print("=" * 70)
    print("RESULTADO: CRUCE TABLAS VS HIVE GENERADO CORRECTAMENTE")
    print("=" * 70)


if __name__ == "__main__":
    main()