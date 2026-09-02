#!/usr/bin/env python3
from pathlib import Path
from collections import Counter
import csv
import json
import re

INPUT_FILE = Path("output/storage_references.csv")
PRO_CONFIG_FILE = Path("input/config/0.0_Configuration_PROD.json")
UC_CONFIG_FILE = Path("input/config/0.0_Configuration_UC.json")
OUTPUT_FILE = Path("output/storage_migration_analysis.csv")


def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def unique_join(values):
    result = []
    for value in values:
        value = clean(value)
        if value and value not in result:
            result.append(value)
    return " | ".join(result)


def flatten_json(data, prefix=""):
    result = {}

    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                result.update(flatten_json(value, path))

            elif isinstance(value, list):
                for index, item in enumerate(value):
                    list_path = f"{path}[{index}]"

                    if isinstance(item, dict):
                        result.update(flatten_json(item, list_path))
                    else:
                        result[list_path] = item

            else:
                result[path] = value

    return result


def get_json_value(data, path):
    """
    Acceso case-insensitive a rutas JSON tipo A.B.C.
    Conserva el comportamiento endurecido de Herramienta 1.
    """
    if not path:
        return None

    current = data

    for part in path.split("."):
        if not isinstance(current, dict):
            return None

        real_key = next(
            (
                key
                for key in current
                if key.casefold() == part.casefold()
            ),
            None,
        )

        if real_key is None:
            return None

        current = current[real_key]

    return current


CONFIG_PATTERN = re.compile(
    r"""
    parsedConfiguration
    ((?:\.[A-Za-z_][A-Za-z0-9_]*)+)
    """,
    re.VERBOSE,
)


def extract_config_paths(reference):
    paths = []

    for match in CONFIG_PATTERN.finditer(clean(reference)):
        path = match.group(1).lstrip(".")

        if path and path not in paths:
            paths.append(path)

    return paths


def detect_path_type(value):
    value = normalize(value)

    if not value:
        return "EMPTY"
    if value.startswith("/volumes/") or value.startswith("dbfs:/volumes/"):
        return "UC_VOLUME"
    if value.startswith("/mnt/") or value.startswith("dbfs:/mnt/"):
        return "LEGACY_MOUNT"
    if value.startswith("dbfs:/"):
        return "LEGACY_DBFS"
    if value.startswith("abfss://"):
        return "ABFSS"
    if value.startswith("abfs://"):
        return "ABFS"
    if value.startswith("wasbs://"):
        return "WASBS"
    if value.startswith("wasb://"):
        return "WASB"
    if value.startswith("file:/"):
        return "FILE"
    if value.startswith("hdfs:/"):
        return "HDFS"

    return "OTHER"


def basename_from_path(value):
    value = clean(value).replace("\\", "/").rstrip("/")
    return value.split("/")[-1].casefold() if value else ""


def find_uc_candidates(pro_value, uc_flat):
    """
    Fallback conservador heredado de Herramienta 1:
    sólo busca candidatos UC por basename para hardcodes.
    No se usa si la referencia ya está controlada por configuración.
    """
    basename = basename_from_path(pro_value)

    if not basename:
        return []

    candidates = []

    for path, value in uc_flat.items():
        if not isinstance(value, str):
            continue

        if detect_path_type(value) not in {
            "UC_VOLUME",
            "ABFSS",
            "ABFS",
        }:
            continue

        if basename_from_path(value) == basename:
            candidates.append((path, value))

    return candidates


def classify_reference_mode(reference):
    return (
        "CONFIG_DRIVEN"
        if extract_config_paths(reference)
        else "HARDCODED"
    )


def classify_migration(
    storage_type,
    reference,
    config_paths,
    pro_values,
    uc_values,
    uc_candidates,
):
    reference_normalized = normalize(reference)

    if (
        reference_normalized.startswith("/volumes/")
        or reference_normalized.startswith("dbfs:/volumes/")
    ):
        return (
            "ALREADY_UC",
            "NO",
            "No requiere ajuste.",
        )

    # Referencia controlada por JSON PRO/UC.
    if config_paths:
        if not pro_values:
            return (
                "CONFIG_PATH_NOT_FOUND_PRO",
                "YES",
                "Revisar la clave de configuración en PRO.",
            )

        if not uc_values:
            return (
                "CONFIG_PATH_NOT_FOUND_UC",
                "YES",
                "Agregar o corregir la clave equivalente en el JSON UC.",
            )

        uc_types = {
            detect_path_type(value)
            for value in uc_values
        }

        if "ABFSS" in uc_types or "ABFS" in uc_types:
            return (
                "CONFIG_DIRECT_ABFSS",
                "YES",
                (
                    "La configuración UC contiene una URI ABFS completa. "
                    "El notebook debe consumir directamente el valor configurado "
                    "sin anteponer dbfs:/ ni reconstruir la ruta."
                ),
            )

        # Regla heredada de Herramienta 1 para construcciones
        # dbfs:/${parsedConfiguration...}
        if (
            storage_type == "DBFS"
            and "parsedconfiguration." in reference_normalized
        ):
            return (
                "CONFIG_ABFSS_URI_REQUIRED",
                "YES",
                (
                    "Completar la clave del JSON UC con la URI abfss:// completa "
                    "y modificar el notebook para usar directamente el valor "
                    "configurado, eliminando el prefijo dbfs:/."
                ),
            )

        if "UC_VOLUME" in uc_types:
            return (
                "CONFIG_MIGRATED_TO_VOLUME",
                "YES",
                (
                    "La configuración UC contiene un Volume. "
                    "Modificar el notebook para consumir directamente "
                    "la ruta configurada."
                ),
            )

        if (
            "LEGACY_MOUNT" in uc_types
            or "LEGACY_DBFS" in uc_types
        ):
            return (
                "LEGACY_PATH_REMAINS_IN_UC_CONFIG",
                "YES",
                (
                    "La configuración UC todavía contiene una ruta legacy. "
                    "Definir el destino UC correspondiente."
                ),
            )

        return (
            "CONFIG_REQUIRES_REVIEW",
            "YES",
            (
                "La clave existe en UC, pero su valor no fue reconocido "
                "como Volume ni URI ABFS completa."
            ),
        )

    # Ruta legacy hardcodeada al JSON de configuración.
    if (
        storage_type == "MOUNT"
        and reference_normalized.endswith("/0.0_configuration.json")
    ):
        return (
            "ENV_CONFIG_PATH_REQUIRED",
            "YES",
            (
                "Eliminar la ruta /mnt hardcodeada y obtener el archivo "
                "de configuración mediante CV_EXPLOTACION_CONFIG_FILE_PATH."
            ),
        )

    if storage_type == "MOUNT":
        if len(uc_candidates) == 1:
            return (
                "HARDCODED_MOUNT_UC_CANDIDATE",
                "YES",
                (
                    "Existe un posible equivalente UC en el JSON. "
                    "Validar el candidato y sustituir el hardcode."
                ),
            )

        if len(uc_candidates) > 1:
            return (
                "HARDCODED_MOUNT_MULTIPLE_UC_CANDIDATES",
                "YES",
                (
                    "Se encontraron varios candidatos UC. "
                    "Requiere revisión manual."
                ),
            )

        return (
            "HARDCODED_LEGACY_MOUNT",
            "YES",
            "Ruta /mnt hardcodeada. Identificar Volume o ruta UC equivalente.",
        )

    if storage_type == "DBFS":
        return (
            "HARDCODED_LEGACY_DBFS",
            "YES",
            "Referencia DBFS legacy. Identificar destino UC equivalente.",
        )

    return (
        "REQUIRES_REVIEW",
        "YES",
        "Revisar manualmente la referencia.",
    )


def infer_storage_type(storage_type, reference):
    storage_type = clean(storage_type).upper()

    if storage_type:
        return storage_type

    value = normalize(reference)

    if value.startswith("dbfs:/"):
        return "DBFS"
    if value.startswith("/mnt/"):
        return "MOUNT"
    if value.startswith("/volumes/"):
        return "VOLUME"
    if value.startswith("abfss://") or value.startswith("abfs://"):
        return "ABFS"
    if value.startswith("wasbs://") or value.startswith("wasb://"):
        return "WASB"

    return "OTHER"


def main():
    required = [
        INPUT_FILE,
        PRO_CONFIG_FILE,
        UC_CONFIG_FILE,
    ]

    missing = [str(path) for path in required if not path.exists()]

    if missing:
        print("ERROR: faltan archivos requeridos:")
        for path in missing:
            print(f" - {path}")
        raise SystemExit(1)

    rows = read_csv(INPUT_FILE)
    pro_config = load_json(PRO_CONFIG_FILE)
    uc_config = load_json(UC_CONFIG_FILE)
    uc_flat = flatten_json(uc_config)

    output_rows = []

    for row in rows:
        storage_type = infer_storage_type(
            row.get("storage_type")
            or row.get("reference_type")
            or row.get("finding_type")
            or row.get("type"),
            row.get("storage_reference")
            or row.get("reference")
            or row.get("value"),
        )

        reference = clean(
            row.get("storage_reference")
            or row.get("reference")
            or row.get("value")
        )

        notebook = clean(
            row.get("notebook")
            or row.get("notebook_path")
        )

        cell = clean(
            row.get("cell")
            or row.get("cell_index")
        )

        jobs = clean(
            row.get("jobs")
            or row.get("job")
        )

        occurrences = clean(row.get("occurrences"))

        source = clean(
            row.get("source")
            or row.get("code")
            or row.get("expression")
        )

        # Compatibilidad con el contrato actual del Paso 07:
        # finding_type,value.
        if not reference:
            for _, value in row.items():
                candidate = clean(value)
                normalized = normalize(candidate)

                if (
                    normalized.startswith("dbfs:/")
                    or normalized.startswith("/mnt/")
                    or normalized.startswith("/volumes/")
                    or normalized.startswith("abfss://")
                    or normalized.startswith("abfs://")
                    or "parsedconfiguration." in normalized
                ):
                    reference = candidate
                    break

        config_paths = extract_config_paths(reference)
        reference_mode = classify_reference_mode(reference)

        pro_values = []
        uc_values = []

        for config_path in config_paths:
            pro_value = get_json_value(
                pro_config,
                config_path,
            )

            uc_value = get_json_value(
                uc_config,
                config_path,
            )

            if pro_value is not None:
                pro_values.append(str(pro_value))

            if uc_value is not None:
                uc_values.append(str(uc_value))

        uc_candidates = (
            []
            if config_paths
            else find_uc_candidates(reference, uc_flat)
        )

        candidate_paths = [
            path
            for path, _ in uc_candidates
        ]

        candidate_values = [
            value
            for _, value in uc_candidates
        ]

        (
            migration_status,
            requires_action,
            recommended_action,
        ) = classify_migration(
            storage_type,
            reference,
            config_paths,
            pro_values,
            uc_values,
            uc_candidates,
        )

        output_rows.append({
            "notebook": notebook,
            "cell": cell,
            "jobs": jobs,
            "storage_type": storage_type,
            "storage_reference": reference,
            "reference_mode": reference_mode,
            "config_path": unique_join(config_paths),
            "pro_value": unique_join(pro_values),
            "uc_value": unique_join(uc_values),
            "uc_candidate_config_path": unique_join(candidate_paths),
            "uc_candidate_value": unique_join(candidate_values),
            "migration_status": migration_status,
            "requires_action": requires_action,
            "recommended_action": recommended_action,
            "occurrences": occurrences,
            "source": source,
        })

    status_order = {
        "CONFIG_DIRECT_ABFSS": 1,
        "CONFIG_ABFSS_URI_REQUIRED": 2,
        "ENV_CONFIG_PATH_REQUIRED": 3,
        "CONFIG_MIGRATED_TO_VOLUME": 4,
        "HARDCODED_MOUNT_UC_CANDIDATE": 5,
        "HARDCODED_MOUNT_MULTIPLE_UC_CANDIDATES": 6,
        "LEGACY_PATH_REMAINS_IN_UC_CONFIG": 7,
        "HARDCODED_LEGACY_MOUNT": 8,
        "HARDCODED_LEGACY_DBFS": 9,
        "CONFIG_PATH_NOT_FOUND_UC": 10,
        "CONFIG_PATH_NOT_FOUND_PRO": 11,
        "CONFIG_REQUIRES_REVIEW": 12,
        "REQUIRES_REVIEW": 13,
        "ALREADY_UC": 14,
    }

    output_rows.sort(
        key=lambda row: (
            status_order.get(row["migration_status"], 99),
            normalize(row["notebook"]),
            normalize(row["storage_reference"]),
        )
    )

    fieldnames = [
        "notebook",
        "cell",
        "jobs",
        "storage_type",
        "storage_reference",
        "reference_mode",
        "config_path",
        "pro_value",
        "uc_value",
        "uc_candidate_config_path",
        "uc_candidate_value",
        "migration_status",
        "requires_action",
        "recommended_action",
        "occurrences",
        "source",
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

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
        writer.writerows(output_rows)

    status_counter = Counter(
        row["migration_status"]
        for row in output_rows
    )

    type_counter = Counter(
        row["storage_type"]
        for row in output_rows
    )

    action_counter = Counter(
        row["requires_action"]
        for row in output_rows
    )

    print("=" * 72)
    print("ASSESSMENT WORKSPACE - PASO 15")
    print("ANALISIS DE MIGRACION DE STORAGE PRO -> UNITY CATALOG")
    print("=" * 72)
    print()
    print(f"Referencias analizadas           : {len(output_rows)}")
    print()

    print("Resumen por tipo:")
    for storage_type in sorted(type_counter):
        print(
            f" - {storage_type:<30}: "
            f"{type_counter[storage_type]}"
        )

    print()
    print("Resumen por estado de migracion:")
    for status in sorted(
        status_counter,
        key=lambda value: (
            status_order.get(value, 99),
            value,
        ),
    ):
        print(
            f" - {status:<38}: "
            f"{status_counter[status]}"
        )

    print()
    print("Resumen de acciones:")
    for action in sorted(action_counter):
        print(
            f" - {action:<30}: "
            f"{action_counter[action]}"
        )

    print()
    print("Referencias que requieren accion:")

    pending_rows = [
        row
        for row in output_rows
        if row["requires_action"] == "YES"
    ]

    if pending_rows:
        for row in pending_rows:
            print(
                f" - {row['notebook']} "
                f"| {row['storage_type']} "
                f"| {row['migration_status']}"
            )
    else:
        print(" - Ninguna")

    print()
    print(f"Archivo generado: {OUTPUT_FILE}")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()