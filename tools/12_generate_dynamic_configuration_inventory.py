from pathlib import Path
from collections import Counter, defaultdict
import csv
import re

DYNAMIC_SOURCES_FILE = Path("output/dynamic_variable_sources.csv")
TABLE_RECONCILIATION_FILE = Path("output/table_hive_reconciliation.csv")
OUTPUT_FILE = Path("output/dynamic_configuration_inventory.csv")

OUTPUT_COLUMNS = [
    "notebook",
    "cell",
    "variable",
    "source_type",
    "source_category",
    "source_expression",
    "data_source",
    "migration_scope",
    "config_paths",
    "depends_on_variables",
    "used_by_dynamic_table",
    "dynamic_table_references",
    "trace_required",
    "trace_reason",
    "jobs",
]


def clean(value):
    return str(value or "").strip()


def split_pipe(value):
    return [x.strip() for x in clean(value).split("|") if x.strip()]


def unique(values):
    result = []
    seen = set()
    for value in values:
        value = clean(value)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def join(values):
    return " | ".join(unique(values))


def extract_config_paths(expression):
    """
    Extrae una o varias rutas parsedConfiguration.X.Y.Z.

    Se detiene antes de operadores, llamadas, índices u otros delimitadores.
    """
    pattern = re.compile(
        r'\bparsedConfiguration\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'
    )
    return unique(match.group(1) for match in pattern.finditer(expression))


def extract_identifiers(expression):
    """
    Extrae identificadores candidatos de una expresión derivada.
    No intenta resolverlos todavía; eso corresponde al Paso 13.
    """
    identifiers = re.findall(r'\b[A-Za-z_]\w*\b', expression)

    ignored = {
        "val", "var", "lazy", "true", "false", "null",
        "parsedConfiguration", "String", "Int", "Long", "Double",
        "Float", "Boolean", "List", "Seq", "Array", "Map", "Set",
        "Some", "None", "Option", "toString", "split", "trim",
        "replace", "replaceAll", "substring", "stripPrefix",
        "stripSuffix", "mkString", "asScala", "toList", "collect",
        "map", "flatMap", "filter", "foreach", "getOrElse",
    }

    return unique(x for x in identifiers if x not in ignored)


def is_literal(expression):
    value = clean(expression)
    if len(value) < 2:
        return False
    return (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    )


def classify_source(source_type, expression):
    source_type = clean(source_type)
    expression = clean(expression)

    if source_type == "FUNCTION_PARAMETER":
        return "FUNCTION_PARAMETER"

    if source_type == "ITERATOR_VARIABLE":
        return "ITERATOR_VARIABLE"

    if extract_config_paths(expression):
        # Puede ser acceso directo o una expresión derivada que contiene config.
        if expression.startswith("parsedConfiguration.") and re.fullmatch(
            r'parsedConfiguration\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',
            expression
        ):
            return "CONFIG_PATH"
        return "CONFIG_DERIVED_EXPRESSION"

    if is_literal(expression):
        return "LITERAL"

    if source_type == "DIRECT_ASSIGNMENT":
        return "DERIVED_EXPRESSION"

    return "OTHER"


def main():
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 12")
    print("INVENTARIO DE CONFIGURACION Y VARIABLES DINAMICAS - V2")
    print("=" * 70)
    print()

    if not DYNAMIC_SOURCES_FILE.exists():
        raise FileNotFoundError(f"No existe: {DYNAMIC_SOURCES_FILE}")

    if not TABLE_RECONCILIATION_FILE.exists():
        raise FileNotFoundError(f"No existe: {TABLE_RECONCILIATION_FILE}")

    with DYNAMIC_SOURCES_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        source_rows = list(csv.DictReader(f))

    with TABLE_RECONCILIATION_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        reconciliation_rows = list(csv.DictReader(f))

    # Variables realmente usadas por referencias dinámicas de tablas.
    #
    # DYNAMIC_PENDING_TRACE  => Spark/Hive: sí entra a HMS -> UC.
    # OUT_OF_SCOPE_JDBC      => dependencia JDBC: se conserva en el assessment,
    #                           pero no se cruza contra Hive.
    dynamic_usage = defaultdict(lambda: {
        "references": [],
        "jobs": [],
        "data_sources": [],
        "reconciliation_statuses": [],
    })

    for row in reconciliation_rows:
        reconciliation_status = clean(row.get("reconciliation_status"))

        if reconciliation_status not in {
            "DYNAMIC_PENDING_TRACE",
            "OUT_OF_SCOPE_JDBC",
        }:
            continue

        refs = split_pipe(row.get("dynamic_variables"))
        if not refs:
            # OUT_OF_SCOPE_JDBC también contiene referencias literales;
            # el Paso 12 sólo trabaja variables dinámicas.
            continue

        notebooks = split_pipe(row.get("notebooks"))
        table_reference = clean(row.get("table_reference"))
        jobs = split_pipe(row.get("jobs"))
        data_source = clean(row.get("data_source")) or "UNKNOWN"

        # El Paso 11 consolida expresiones entre notebooks. Para no inventar
        # relaciones, sólo asociamos una variable con notebooks donde el Paso 09
        # realmente tenga un origen para esa variable.
        for notebook in notebooks:
            for variable in refs:
                dynamic_usage[(notebook, variable)]["references"].append(
                    table_reference
                )
                dynamic_usage[(notebook, variable)]["jobs"].extend(jobs)
                dynamic_usage[(notebook, variable)]["data_sources"].append(
                    data_source
                )
                dynamic_usage[(notebook, variable)][
                    "reconciliation_statuses"
                ].append(reconciliation_status)

    known_variables_by_notebook = defaultdict(set)
    for row in source_rows:
        notebook = clean(row.get("notebook"))
        variable = clean(row.get("variable"))
        if notebook and variable:
            known_variables_by_notebook[notebook].add(variable)

    output_rows = []
    duplicate_keys = set()
    seen = set()

    for row in source_rows:
        notebook = clean(row.get("notebook"))
        variable = clean(row.get("variable"))
        source_type = clean(row.get("source_type"))
        expression = clean(row.get("source_expression"))
        cell = clean(row.get("cell"))
        jobs = split_pipe(row.get("jobs"))
        source_data_source = clean(row.get("data_source")) or "UNKNOWN"

        category = classify_source(source_type, expression)
        config_paths = extract_config_paths(expression)

        candidate_identifiers = extract_identifiers(expression)
        depends_on = [
            identifier
            for identifier in candidate_identifiers
            if identifier != variable
            and identifier in known_variables_by_notebook.get(notebook, set())
        ]

        usage = dynamic_usage.get((notebook, variable))
        used_by_dynamic_table = usage is not None

        usage_data_sources = (
            unique(usage["data_sources"])
            if usage
            else []
        )

        # Paso 09 es la fuente primaria para data_source.
        # Paso 11 sirve como validación contextual de la referencia dinámica.
        combined_data_sources = unique(
            [source_data_source] + usage_data_sources
        )
        data_source = join(combined_data_sources) or "UNKNOWN"

        if data_source == "SPARK_HIVE":
            migration_scope = "HMS_TO_UC"
        elif data_source == "JDBC":
            migration_scope = "OUT_OF_SCOPE_JDBC"
        elif "SPARK_HIVE" in combined_data_sources and "JDBC" in combined_data_sources:
            migration_scope = "MIXED_REQUIRES_REVIEW"
        else:
            migration_scope = "REQUIRES_REVIEW"

        if category == "CONFIG_PATH":
            trace_required = False
            trace_reason = "DIRECT_CONFIG_PATH"
        elif category == "LITERAL":
            trace_required = False
            trace_reason = "DIRECT_LITERAL"
        elif category == "CONFIG_DERIVED_EXPRESSION":
            trace_required = True
            trace_reason = "TRANSFORM_CONFIG_VALUE"
        elif category == "DERIVED_EXPRESSION":
            trace_required = True
            trace_reason = "TRACE_DERIVED_EXPRESSION"
        elif category == "FUNCTION_PARAMETER":
            trace_required = True
            trace_reason = "TRACE_FUNCTION_ARGUMENT"
        elif category == "ITERATOR_VARIABLE":
            trace_required = True
            trace_reason = "TRACE_ITERATOR_COLLECTION"
        else:
            trace_required = True
            trace_reason = "REVIEW_SOURCE"

        key = (
            notebook.casefold(),
            variable.casefold(),
            source_type.casefold(),
            expression.casefold(),
            data_source.casefold(),
        )

        if key in seen:
            duplicate_keys.add(key)
            continue
        seen.add(key)

        output_rows.append({
            "notebook": notebook,
            "cell": cell,
            "variable": variable,
            "source_type": source_type,
            "source_category": category,
            "source_expression": expression,
            "data_source": data_source,
            "migration_scope": migration_scope,
            "config_paths": join(config_paths),
            "depends_on_variables": join(depends_on),
            "used_by_dynamic_table": str(used_by_dynamic_table).lower(),
            "dynamic_table_references": (
                join(usage["references"]) if usage else ""
            ),
            "trace_required": str(trace_required).lower(),
            "trace_reason": trace_reason,
            "jobs": join(jobs + (usage["jobs"] if usage else [])),
        })

    output_rows.sort(
        key=lambda r: (
            r["notebook"].casefold(),
            r["variable"].casefold(),
            int(r["cell"]) if r["cell"].isdigit() else 0,
            r["source_category"].casefold(),
        )
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    categories = Counter(row["source_category"] for row in output_rows)
    data_sources = Counter(row["data_source"] for row in output_rows)
    migration_scopes = Counter(row["migration_scope"] for row in output_rows)
    trace_reasons = Counter(
        row["trace_reason"]
        for row in output_rows
        if row["trace_required"] == "true"
    )

    used_rows = [
        row for row in output_rows
        if row["used_by_dynamic_table"] == "true"
    ]
    used_pairs = {
        (row["notebook"], row["variable"])
        for row in used_rows
    }
    direct_config_pairs = {
        (row["notebook"], row["variable"])
        for row in used_rows
        if row["source_category"] == "CONFIG_PATH"
    }
    trace_pairs = {
        (row["notebook"], row["variable"])
        for row in used_rows
        if row["trace_required"] == "true"
    }

    active_hive_pairs = {
        (row["notebook"], row["variable"])
        for row in used_rows
        if row["data_source"] == "SPARK_HIVE"
    }

    active_jdbc_pairs = {
        (row["notebook"], row["variable"])
        for row in used_rows
        if row["data_source"] == "JDBC"
    }

    print("--- Entradas ---")
    print(f"Orígenes dinámicos leídos      : {len(source_rows)}")
    print(f"Objetos reconciliados leídos   : {len(reconciliation_rows)}")
    print()
    print("--- Inventario ---")
    print(f"Registros generados            : {len(output_rows)}")
    print(f"Duplicados omitidos            : {len(duplicate_keys)}")
    print(f"Usos Notebook+Variable activos : {len(used_pairs)}")
    print(f" - SPARK_HIVE / HMS->UC        : {len(active_hive_pairs)}")
    print(f" - JDBC / fuera de alcance     : {len(active_jdbc_pairs)}")
    print(f"Config directa en usos activos : {len(direct_config_pairs)}")
    print(f"Usos activos requieren trace   : {len(trace_pairs)}")
    print()

    print("Resumen por data_source:")
    for data_source in sorted(data_sources):
        print(f" - {data_source:<30}: {data_sources[data_source]}")

    print()
    print("Resumen por migration_scope:")
    for scope in sorted(migration_scopes):
        print(f" - {scope:<30}: {migration_scopes[scope]}")

    print()
    print("Resumen por categoría:")
    for category in sorted(categories):
        print(f" - {category:<30}: {categories[category]}")

    if trace_reasons:
        print()
        print("Razones de trazabilidad:")
        for reason in sorted(trace_reasons):
            print(f" - {reason:<30}: {trace_reasons[reason]}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE.resolve()}")
    print()
    print("=" * 70)
    print("RESULTADO: INVENTARIO DINAMICO GENERADO CORRECTAMENTE")
    print("=" * 70)


if __name__ == "__main__":
    main()
