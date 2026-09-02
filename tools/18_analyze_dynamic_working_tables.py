#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json


WORKING_REFERENCES_FILE = Path(
    "output/working_table_references.csv"
)

TABLE_REFERENCES_FILE = Path(
    "output/table_references.csv"
)

UC_CONFIG_FILE = Path(
    "input/config/0.0_Configuration_UC.json"
)

OUTPUT_FILE = Path(
    "output/dynamic_working_tables.csv"
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


def load_json(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:
        return json.load(f)


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


def split_pipe(value):
    return [
        part.strip()
        for part in clean(value).split("|")
        if part.strip()
    ]


def get_case_insensitive(mapping, key):
    if not isinstance(mapping, dict):
        return None

    for real_key, value in mapping.items():
        if str(real_key).casefold() == key.casefold():
            return value

    return None


def get_dynamic_work_schema(uc_config):
    section = get_case_insensitive(
        uc_config,
        "EsquemasTrabajoDbks_UC",
    )

    if not isinstance(section, dict):
        return ""

    return clean(
        get_case_insensitive(
            section,
            "Default",
        )
    )


def build_cell_index(table_rows):
    """
    Paso 14 no necesita conservar cell porque trabaja a nivel de
    reconciliación. Para mantener el contrato documental de Tool 1,
    recuperamos la celda desde Paso 08 cuando existe coincidencia exacta.
    """
    index = defaultdict(set)

    for row in table_rows:
        notebook = normalize(
            row.get("notebook")
        )

        reference = normalize(
            row.get("table_reference")
        )

        cell = clean(
            row.get("cell")
        )

        if (
            notebook
            and reference
            and cell
        ):
            index[
                (notebook, reference)
            ].add(cell)

    return index


def cell_sort_key(value):
    value = clean(value)

    try:
        return (0, int(value))
    except Exception:
        return (1, value.casefold())


def main():
    required = [
        WORKING_REFERENCES_FILE,
        UC_CONFIG_FILE,
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

    working_rows = read_csv(
        WORKING_REFERENCES_FILE
    )

    table_rows = (
        read_csv(TABLE_REFERENCES_FILE)
        if TABLE_REFERENCES_FILE.exists()
        else []
    )

    uc_config = load_json(
        UC_CONFIG_FILE
    )

    work_schema = get_dynamic_work_schema(
        uc_config
    )

    cell_index = build_cell_index(
        table_rows
    )

    # ========================================================
    # Consolidar por notebook + patrón dinámico + variable.
    #
    # Paso 14 puede contener varias tablas materializadas
    # resultantes del mismo patrón:
    #
    #   default.${nombreTablaSinBaseDeDatos}
    #
    # En este paso queremos una acción de migración por patrón,
    # no una acción repetida por cada nombre materializado.
    # ========================================================

    groups = {}

    for row in working_rows:
        notebook = clean(
            row.get("notebook")
        )

        dynamic_reference = clean(
            row.get("dynamic_reference")
        )

        variable = clean(
            row.get("variable")
        )

        if not dynamic_reference:
            continue

        # Este paso cubre el patrón legacy default dinámico.
        if not normalize(
            dynamic_reference
        ).startswith("default.${"):
            continue

        key = (
            normalize(notebook),
            normalize(dynamic_reference),
            variable.casefold(),
        )

        if key not in groups:
            groups[key] = {
                "notebook":
                    notebook,

                "current_expression":
                    dynamic_reference,

                "dynamic_variable":
                    variable,

                "jobs":
                    [],

                "working_tables":
                    [],

                "config_paths":
                    [],

                "trace_statuses":
                    [],

                "migration_scopes":
                    [],

                "notes":
                    [],
            }

        group = groups[key]

        group["jobs"].extend(
            split_pipe(
                row.get("jobs")
            )
        )

        group["working_tables"].append(
            row.get("working_table")
        )

        group["config_paths"].extend(
            split_pipe(
                row.get("config_paths")
            )
        )

        group["trace_statuses"].append(
            row.get("trace_status")
        )

        group["migration_scopes"].append(
            row.get("migration_scope")
        )

        group["notes"].append(
            row.get("notes")
        )

    output_rows = []

    for group in groups.values():
        notebook = group["notebook"]
        current_expression = (
            group["current_expression"]
        )
        variable = (
            group["dynamic_variable"]
        )

        cells = sorted(
            cell_index.get(
                (
                    normalize(notebook),
                    normalize(
                        current_expression
                    ),
                ),
                set(),
            ),
            key=cell_sort_key,
        )

        if work_schema:
            migration_status = (
                "SCHEMA_CONFIGURED"
            )

            target_expression = (
                f"{work_schema}.${{{variable}}}"
            )

            recommended_action = (
                "Reemplazar el esquema dinámico legacy "
                "default por el esquema configurado en "
                "EsquemasTrabajoDbks_UC.Default, conservando "
                "la generación dinámica del nombre de tabla."
            )

        else:
            migration_status = (
                "SCHEMA_CONFIGURATION_REQUIRED"
            )

            target_expression = (
                "[EsquemasTrabajoDbks_UC.Default]."
                f"${{{variable}}}"
            )

            recommended_action = (
                "Agregar EsquemasTrabajoDbks_UC.Default "
                "al JSON UC y utilizar ese valor para construir "
                "la tabla de trabajo dinámica, evitando "
                "hardcodear el catálogo/esquema en el notebook."
            )

        output_rows.append({
            "job":
                unique_join(
                    sorted(
                        unique(
                            group["jobs"]
                        ),
                        key=str.casefold,
                    )
                ),

            "notebook":
                notebook,

            "cell":
                unique_join(cells),

            "working_table_type":
                "DYNAMIC_WORKING_TABLE",

            "current_expression":
                current_expression,

            "dynamic_variable":
                variable,

            "configured_work_schema":
                work_schema,

            "target_expression":
                target_expression,

            "config_path":
                "EsquemasTrabajoDbks_UC.Default",

            "materialized_working_tables":
                unique_join(
                    sorted(
                        unique(
                            group[
                                "working_tables"
                            ]
                        ),
                        key=str.casefold,
                    )
                ),

            "trace_status":
                unique_join(
                    group[
                        "trace_statuses"
                    ]
                ),

            "source_config_paths":
                unique_join(
                    group[
                        "config_paths"
                    ]
                ),

            "migration_scope":
                unique_join(
                    group[
                        "migration_scopes"
                    ]
                ),

            "migration_status":
                migration_status,

            "requires_action":
                "YES",

            "recommended_action":
                recommended_action,

            "source_step":
                "STEP_14_WORKING_TABLE_REFERENCES",
        })

    output_rows.sort(
        key=lambda row: (
            normalize(
                row["notebook"]
            ),
            normalize(
                row["current_expression"]
            ),
            normalize(
                row["dynamic_variable"]
            ),
        )
    )

    fieldnames = [
        "job",
        "notebook",
        "cell",
        "working_table_type",
        "current_expression",
        "dynamic_variable",
        "configured_work_schema",
        "target_expression",
        "config_path",
        "materialized_working_tables",
        "trace_status",
        "source_config_paths",
        "migration_scope",
        "migration_status",
        "requires_action",
        "recommended_action",
        "source_step",
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
        row["migration_status"]
        for row in output_rows
    )

    notebooks_impacted = {
        row["notebook"]
        for row in output_rows
    }

    materialized_tables = set()

    for row in output_rows:
        materialized_tables.update(
            split_pipe(
                row[
                    "materialized_working_tables"
                ]
            )
        )

    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 18"
    )
    print(
        "ANALISIS DE TABLAS DE TRABAJO DINAMICAS"
    )
    print("=" * 72)
    print()

    print(
        f"Relaciones Paso 14 recibidas     : "
        f"{len(working_rows)}"
    )

    print(
        f"Notebooks impactados             : "
        f"{len(notebooks_impacted)}"
    )

    print(
        f"Patrones dinámicos consolidados  : "
        f"{len(output_rows)}"
    )

    print(
        f"Tablas materializadas conocidas  : "
        f"{len(materialized_tables)}"
    )

    print()

    print(
        f"Schema de trabajo configurado    : "
        f"{work_schema or '[NO CONFIGURADO]'}"
    )

    print()

    print(
        "Resumen por estado:"
    )

    for status in sorted(
        status_counter
    ):
        print(
            f" - {status:<36}: "
            f"{status_counter[status]}"
        )

    print()

    print(
        "Patrones que requieren ajuste:"
    )

    if output_rows:
        for row in output_rows:
            print(
                f" - {row['notebook']} "
                f"| {row['current_expression']} "
                f"-> {row['target_expression']}"
            )
    else:
        print(" - Ninguno")

    print()
    print(
        f"Archivo generado: {OUTPUT_FILE}"
    )
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
