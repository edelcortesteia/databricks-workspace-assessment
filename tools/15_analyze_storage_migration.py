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


def split_multi(value):
    return [item.strip() for item in clean(value).split("|") if item.strip()]


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


def get_json_values(data, path):
    """
    Acceso case-insensitive y compatible con listas.

    Ejemplo:
      StorageMountList.MountPoint
    devuelve los MountPoint de todos los elementos de StorageMountList.
    """
    if not path:
        return []

    parts = [part for part in path.split(".") if part]

    def walk(current, index):
        if index >= len(parts):
            if isinstance(current, list):
                values = []
                for item in current:
                    values.extend(walk(item, index))
                return values
            return [current]

        part = parts[index]

        if isinstance(current, list):
            values = []
            for item in current:
                values.extend(walk(item, index))
            return values

        if not isinstance(current, dict):
            return []

        real_key = next(
            (key for key in current if key.casefold() == part.casefold()),
            None,
        )
        if real_key is None:
            return []

        return walk(current[real_key], index + 1)

    return [value for value in walk(data, 0) if value is not None]


CONFIG_PATTERN = re.compile(
    r"parsedConfiguration((?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
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
    basename = basename_from_path(pro_value)
    if not basename:
        return []
    candidates = []
    for path, value in uc_flat.items():
        if not isinstance(value, str):
            continue
        if detect_path_type(value) not in {"UC_VOLUME", "ABFSS", "ABFS"}:
            continue
        if basename_from_path(value) == basename:
            candidates.append((path, value))
    return candidates


def classify_reference_mode(reference, config_paths):
    return "CONFIG_DRIVEN" if config_paths else "HARDCODED"


def classify_migration(storage_type, reference, config_paths, pro_values, uc_values, uc_candidates):
    reference_normalized = normalize(reference)

    if reference_normalized.startswith("/volumes/") or reference_normalized.startswith("dbfs:/volumes/"):
        return "ALREADY_UC", "NO", "No requiere ajuste."

    if config_paths:
        if not pro_values:
            return "CONFIG_PATH_NOT_FOUND_PRO", "YES", "Revisar la clave de configuración en PRO."
        if not uc_values:
            return "CONFIG_PATH_NOT_FOUND_UC", "YES", "Agregar o corregir la clave equivalente en el JSON UC."

        uc_types = {detect_path_type(value) for value in uc_values}

        # Hallazgo específico incorporado después de la auditoría de StorageMountList.MountPoint.
        if storage_type == "DYNAMIC_DBFS_PREFIX" and ({"ABFSS", "ABFS"} & uc_types):
            return (
                "CONFIG_DBFS_PREFIX_INCOMPATIBLE_WITH_ABFSS",
                "YES",
                (
                    "La clave UC ya contiene una URI ABFS/ABFSS completa, pero el notebook "
                    "construye o busca un prefijo dbfs:${variable}. Ajustar la lógica para no "
                    "anteponer ni buscar 'dbfs:' sobre una URI ABFSS. Si el valor se usa para "
                    "derivar BlobName, conservar BlobName como ruta relativa al contenedor y "
                    "construir BlobPath explícitamente sin depender de replaceFirst(dbfs:<mount>)."
                ),
            )

        if "ABFSS" in uc_types or "ABFS" in uc_types:
            return (
                "CONFIG_DIRECT_ABFSS",
                "YES",
                (
                    "La configuración UC contiene una URI ABFS completa. El notebook debe "
                    "consumir directamente el valor configurado sin anteponer dbfs:/ ni "
                    "reconstruir la ruta con supuestos de mount legacy."
                ),
            )

        if storage_type == "DBFS" and "parsedconfiguration." in reference_normalized:
            return (
                "CONFIG_ABFSS_URI_REQUIRED",
                "YES",
                (
                    "Completar la clave del JSON UC con la URI abfss:// completa y modificar "
                    "el notebook para usar directamente el valor configurado, eliminando el "
                    "prefijo dbfs:/."
                ),
            )

        if "UC_VOLUME" in uc_types:
            return (
                "CONFIG_MIGRATED_TO_VOLUME",
                "YES",
                "La configuración UC contiene un Volume. Modificar el notebook para consumir directamente la ruta configurada.",
            )

        if "LEGACY_MOUNT" in uc_types or "LEGACY_DBFS" in uc_types:
            return (
                "LEGACY_PATH_REMAINS_IN_UC_CONFIG",
                "YES",
                "La configuración UC todavía contiene una ruta legacy. Definir el destino UC correspondiente.",
            )

        return (
            "CONFIG_REQUIRES_REVIEW",
            "YES",
            "La clave existe en UC, pero su valor no fue reconocido como Volume ni URI ABFS completa.",
        )

    if storage_type == "MOUNT" and reference_normalized.endswith("/0.0_configuration.json"):
        return (
            "ENV_CONFIG_PATH_REQUIRED",
            "YES",
            "Eliminar la ruta /mnt hardcodeada y obtener el archivo de configuración mediante CV_EXPLOTACION_CONFIG_FILE_PATH.",
        )

    if storage_type == "MOUNT":
        if len(uc_candidates) == 1:
            return "HARDCODED_MOUNT_UC_CANDIDATE", "YES", "Existe un posible equivalente UC en el JSON. Validar el candidato y sustituir el hardcode."
        if len(uc_candidates) > 1:
            return "HARDCODED_MOUNT_MULTIPLE_UC_CANDIDATES", "YES", "Se encontraron varios candidatos UC. Requiere revisión manual."
        return "HARDCODED_LEGACY_MOUNT", "YES", "Ruta /mnt hardcodeada. Identificar Volume o ruta UC equivalente."

    if storage_type in {"DBFS", "DYNAMIC_DBFS_PREFIX"}:
        return "HARDCODED_LEGACY_DBFS", "YES", "Referencia DBFS legacy. Identificar destino UC equivalente."

    return "REQUIRES_REVIEW", "YES", "Revisar manualmente la referencia."


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
    if re.search(r"(?i)dbfs:\s*\$|[\"']dbfs:[\"']\s*\+", clean(reference)):
        return "DYNAMIC_DBFS_PREFIX"
    return "OTHER"


def main():
    required = [INPUT_FILE, PRO_CONFIG_FILE, UC_CONFIG_FILE]
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
        reference = clean(row.get("storage_reference") or row.get("reference") or row.get("value"))
        storage_type = infer_storage_type(
            row.get("storage_type") or row.get("reference_type") or row.get("finding_type") or row.get("type"),
            reference,
        )
        notebook = clean(row.get("notebook") or row.get("notebook_path"))
        cell = clean(row.get("cell") or row.get("cell_index"))
        jobs = clean(row.get("jobs") or row.get("job"))
        occurrences = clean(row.get("occurrences"))
        source = clean(row.get("source") or row.get("code") or row.get("expression"))
        line_numbers = clean(row.get("line_numbers") or row.get("line"))

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

        config_paths = unique(
            split_multi(row.get("config_path"))
            + extract_config_paths(reference)
            + extract_config_paths(source)
        )
        reference_mode = classify_reference_mode(reference, config_paths)

        pro_values = []
        uc_values = []
        for config_path in config_paths:
            pro_values.extend(str(value) for value in get_json_values(pro_config, config_path))
            uc_values.extend(str(value) for value in get_json_values(uc_config, config_path))

        uc_candidates = [] if config_paths else find_uc_candidates(reference, uc_flat)
        candidate_paths = [path for path, _ in uc_candidates]
        candidate_values = [value for _, value in uc_candidates]

        migration_status, requires_action, recommended_action = classify_migration(
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
            "line_numbers": line_numbers,
            "source": source,
        })

    status_order = {
        "CONFIG_DBFS_PREFIX_INCOMPATIBLE_WITH_ABFSS": 1,
        "CONFIG_DIRECT_ABFSS": 2,
        "CONFIG_ABFSS_URI_REQUIRED": 3,
        "ENV_CONFIG_PATH_REQUIRED": 4,
        "CONFIG_MIGRATED_TO_VOLUME": 5,
        "HARDCODED_MOUNT_UC_CANDIDATE": 6,
        "HARDCODED_MOUNT_MULTIPLE_UC_CANDIDATES": 7,
        "LEGACY_PATH_REMAINS_IN_UC_CONFIG": 8,
        "HARDCODED_LEGACY_MOUNT": 9,
        "HARDCODED_LEGACY_DBFS": 10,
        "CONFIG_PATH_NOT_FOUND_UC": 11,
        "CONFIG_PATH_NOT_FOUND_PRO": 12,
        "CONFIG_REQUIRES_REVIEW": 13,
        "REQUIRES_REVIEW": 14,
        "ALREADY_UC": 15,
    }

    output_rows.sort(key=lambda row: (
        status_order.get(row["migration_status"], 99),
        normalize(row["notebook"]),
        normalize(row["storage_reference"]),
    ))

    fieldnames = [
        "notebook", "cell", "jobs", "storage_type", "storage_reference",
        "reference_mode", "config_path", "pro_value", "uc_value",
        "uc_candidate_config_path", "uc_candidate_value", "migration_status",
        "requires_action", "recommended_action", "occurrences", "line_numbers", "source",
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    status_counter = Counter(row["migration_status"] for row in output_rows)
    type_counter = Counter(row["storage_type"] for row in output_rows)
    action_counter = Counter(row["requires_action"] for row in output_rows)

    print("=" * 72)
    print("ASSESSMENT WORKSPACE - PASO 15 V2")
    print("ANALISIS DE MIGRACION DE STORAGE PRO -> UNITY CATALOG")
    print("=" * 72)
    print(f"Referencias analizadas           : {len(output_rows)}")
    print("\nResumen por tipo:")
    for storage_type in sorted(type_counter):
        print(f" - {storage_type:<40}: {type_counter[storage_type]}")
    print("\nResumen por estado de migracion:")
    for status in sorted(status_counter, key=lambda value: (status_order.get(value, 99), value)):
        print(f" - {status:<48}: {status_counter[status]}")
    print("\nResumen de acciones:")
    for action in sorted(action_counter):
        print(f" - {action:<30}: {action_counter[action]}")
    print("\nReferencias que requieren accion:")
    pending_rows = [row for row in output_rows if row["requires_action"] == "YES"]
    if pending_rows:
        for row in pending_rows:
            print(f" - {row['notebook']} | {row['storage_type']} | {row['migration_status']}")
    else:
        print(" - Ninguna")
    print(f"\nArchivo generado: {OUTPUT_FILE}")
    print("=" * 72)


if __name__ == "__main__":
    main()
