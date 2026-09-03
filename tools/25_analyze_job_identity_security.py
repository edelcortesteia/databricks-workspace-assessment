#!/usr/bin/env python3
from pathlib import Path
from collections import Counter
import csv
import json
import re
import yaml


# ============================================================
# ASSESSMENT WORKSPACE - PASO 25
# IDENTIDAD Y SEGURIDAD DE JOBS
#
# PRO:
#   snapshot/jobs/*.json
#
# UC:
#   input/config/jobs/UC_*.yml
#
# Scope:
#   únicamente los jobs con matching EXACT_NAME del Paso 22.
#
# Base metodológica:
#   Tool 1 / antiguo Paso 24 - Identidad y Seguridad.
#
# Objetivo:
#   - identificar identidad de ejecución PRO;
#   - identificar identidad de ejecución UC;
#   - validar transición hacia Service Principal;
#   - inventariar data_security_mode PRO y UC;
#   - confirmar que UC declara explícitamente modo de seguridad;
#   - conservar evidencia sin pretender validar permisos
#     efectivos sobre Azure/Unity Catalog.
# ============================================================


SNAPSHOT_JOBS_DIR = Path("snapshot/jobs")
UC_JOBS_DIR = Path("input/config/jobs")
MATCHING_FILE = Path("output/job_name_matching.csv")

OUTPUT_FILE = Path(
    "output/job_identity_security_analysis.csv"
)


# ============================================================
# Utilidades
# ============================================================

def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


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


def normalize_yaml_text(text):
    text = text.replace("\t", "  ")

    stripped = text.strip()

    if (
        stripped.startswith('"resources:')
        and stripped.endswith('"')
    ):
        text = stripped[1:-1]

    text = re.sub(
        r':\s*""([^"\r\n]*)""\s*$',
        lambda m:
            ': "' + m.group(1) + '"',
        text,
        flags=re.MULTILINE,
    )

    return text


def load_yaml(path):
    try:
        text = path.read_text(
            encoding="utf-8"
        )

        try:
            data = yaml.safe_load(text)
            parse_mode = "DIRECT"

        except Exception:
            data = yaml.safe_load(
                normalize_yaml_text(text)
            )
            parse_mode = "NORMALIZED"

        return data, parse_mode, ""

    except Exception as e:
        return (
            None,
            "ERROR",
            f"{type(e).__name__}: {e}",
        )


# ============================================================
# Matching Paso 22
# ============================================================

def load_exact_matches():
    if not MATCHING_FILE.exists():
        raise SystemExit(
            "Falta output/job_name_matching.csv. "
            "Ejecuta primero el Paso 22."
        )

    rows = read_csv(MATCHING_FILE)
    result = {}

    for row in rows:
        method = clean(
            row.get("match_method")
            or row.get("matching_method")
            or row.get("method")
        )

        if method != "EXACT_NAME":
            continue

        pro_job = clean(
            row.get("workspace_job")
            or row.get("pro_job")
            or row.get("job")
            or row.get("workspace_name")
        )

        uc_job = clean(
            row.get("uc_job")
            or row.get("matched_uc_job")
            or row.get("uc_name")
        )

        if pro_job and uc_job:
            result[pro_job] = uc_job

    if not result:
        raise SystemExit(
            "No se encontraron matches EXACT_NAME en el Paso 22."
        )

    return result


# ============================================================
# Extracción de job UC
# ============================================================

def extract_uc_job(data):
    if not isinstance(data, dict):
        return "", {}

    resources = data.get(
        "resources",
        {},
    )

    jobs = resources.get(
        "jobs",
        {},
    )

    if not isinstance(jobs, dict):
        return "", {}

    for _, job_data in jobs.items():
        if not isinstance(job_data, dict):
            continue

        return (
            clean(job_data.get("name")),
            job_data,
        )

    return "", {}


# ============================================================
# Identidad
# ============================================================

def extract_identity(job_data):
    """
    Retorna:
      identity_type
      identity_value
      identity_display

    Databricks Jobs puede declarar:
      run_as.user_name
      run_as.service_principal_name

    Si no existe run_as explícito:
      NOT_DEFINED
    """

    if not isinstance(job_data, dict):
        return (
            "NOT_DEFINED",
            "",
            "NOT_DEFINED",
        )

    run_as = job_data.get(
        "run_as",
        {},
    )

    if not isinstance(run_as, dict):
        run_as = {}

    service_principal = clean(
        run_as.get(
            "service_principal_name"
        )
    )

    user_name = clean(
        run_as.get(
            "user_name"
        )
    )

    if service_principal:
        return (
            "SERVICE_PRINCIPAL",
            service_principal,
            f"SERVICE_PRINCIPAL: {service_principal}",
        )

    if user_name:
        return (
            "USER",
            user_name,
            f"USER: {user_name}",
        )

    return (
        "NOT_DEFINED",
        "",
        "NOT_DEFINED",
    )


# ============================================================
# Seguridad
# ============================================================

def extract_security_modes(job_data):
    """
    Busca data_security_mode de forma recursiva.

    Esto cubre:
      tasks[].new_cluster.data_security_mode
      job_clusters[].new_cluster.data_security_mode
      otras reubicaciones estructurales futuras.
    """

    modes = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():

                if (
                    clean(key).casefold()
                    == "data_security_mode"
                ):
                    mode = clean(child)

                    if mode:
                        modes.append(mode)

                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(job_data)

    return unique(modes)


# ============================================================
# Clasificación
# ============================================================

def classify_identity(
    pro_type,
    pro_value,
    uc_type,
    uc_value,
):
    if (
        uc_type
        == "SERVICE_PRINCIPAL"
        and uc_value
    ):
        if (
            pro_type
            == "SERVICE_PRINCIPAL"
            and normalize(pro_value)
            == normalize(uc_value)
        ):
            return (
                "IDENTITY_ALIGNED_UC",
                "NO",
                (
                    "El job ya utiliza el mismo Service Principal "
                    "en PRO y UC."
                ),
            )

        return (
            "EXPECTED_UC_IDENTITY_CHANGE",
            "NO",
            (
                "La identidad de ejecución se homologa en UC "
                "mediante Service Principal, desacoplando el job "
                "de identidades personales o implícitas."
            ),
        )

    if uc_type == "USER":
        return (
            "UC_USER_IDENTITY_REVIEW",
            "REVIEW",
            (
                "El job UC continúa configurado con una identidad "
                "de usuario; validar contra el diseño objetivo de "
                "ejecución mediante Service Principal."
            ),
        )

    return (
        "UC_IDENTITY_NOT_DEFINED",
        "REVIEW",
        (
            "No se encontró una identidad run_as explícita en UC."
        ),
    )


def classify_security(
    pro_modes,
    uc_modes,
):
    if not uc_modes:
        return (
            "UC_SECURITY_MODE_NOT_DEFINED",
            "REVIEW",
            (
                "El job UC no declara data_security_mode en la "
                "configuración analizada."
            ),
        )

    # En este Assessment no imponemos un valor genérico distinto
    # al YAML objetivo. Lo importante es confirmar que UC declara
    # explícitamente su modo de seguridad y conservar el valor real.
    return (
        "SECURITY_MODE_ALIGNED_UC",
        "NO",
        (
            "El job UC declara explícitamente data_security_mode. "
            "La validación de este paso confirma configuración "
            "declarada, no permisos efectivos de ejecución."
        ),
    )


# ============================================================
# Carga PRO
# ============================================================

def load_pro_jobs():
    result = {}

    for path in sorted(
        SNAPSHOT_JOBS_DIR.glob(
            "*.json"
        )
    ):
        try:
            data = load_json(path)

        except Exception as e:
            print(
                f"ADVERTENCIA leyendo {path}: {e}"
            )
            continue

        if not isinstance(data, dict):
            continue

        job_name = clean(
            data.get("name")
        )

        if not job_name:
            continue

        result[job_name] = {
            "data": data,
            "source": str(path),
        }

    return result


# ============================================================
# Carga UC
# ============================================================

def load_uc_jobs():
    result = {}

    yaml_files = []

    for pattern in [
        "UC_*.yml",
        "UC_*.yaml",
    ]:
        yaml_files.extend(
            UC_JOBS_DIR.glob(pattern)
        )

    for path in sorted(yaml_files):

        data, parse_mode, error = (
            load_yaml(path)
        )

        if data is None:
            print(
                f"ADVERTENCIA leyendo {path}: {error}"
            )
            continue

        job_name, job_data = (
            extract_uc_job(data)
        )

        if not job_name:
            continue

        result[job_name] = {
            "data": job_data,
            "source": str(path),
            "parse_mode": parse_mode,
        }

    return result


# ============================================================
# Main
# ============================================================

def main():

    exact_matches = (
        load_exact_matches()
    )

    pro_jobs = load_pro_jobs()
    uc_jobs = load_uc_jobs()

    missing_pro = [
        job
        for job
        in exact_matches
        if job not in pro_jobs
    ]

    missing_uc = [
        uc_job
        for uc_job
        in exact_matches.values()
        if uc_job not in uc_jobs
    ]

    if missing_pro:
        raise SystemExit(
            "Jobs EXACT_NAME no encontrados "
            "en snapshot PRO:\n - "
            + "\n - ".join(missing_pro)
        )

    if missing_uc:
        raise SystemExit(
            "Jobs EXACT_NAME no encontrados "
            "en YAML UC:\n - "
            + "\n - ".join(missing_uc)
        )

    output_rows = []

    for pro_job in sorted(
        exact_matches,
        key=str.casefold,
    ):
        uc_job = exact_matches[
            pro_job
        ]

        pro_data = pro_jobs[
            pro_job
        ]["data"]

        uc_data = uc_jobs[
            uc_job
        ]["data"]

        (
            pro_identity_type,
            pro_identity_value,
            pro_identity_display,
        ) = extract_identity(
            pro_data
        )

        (
            uc_identity_type,
            uc_identity_value,
            uc_identity_display,
        ) = extract_identity(
            uc_data
        )

        pro_modes = (
            extract_security_modes(
                pro_data
            )
        )

        uc_modes = (
            extract_security_modes(
                uc_data
            )
        )

        (
            identity_status,
            identity_action,
            identity_notes,
        ) = classify_identity(
            pro_identity_type,
            pro_identity_value,
            uc_identity_type,
            uc_identity_value,
        )

        (
            security_status,
            security_action,
            security_notes,
        ) = classify_security(
            pro_modes,
            uc_modes,
        )

        requires_action = (
            "REVIEW"
            if (
                identity_action
                == "REVIEW"
                or security_action
                == "REVIEW"
            )
            else "NO"
        )

        migration_status = (
            identity_status
            if requires_action == "NO"
            else (
                identity_status
                + " | "
                + security_status
            )
        )

        output_rows.append({
            "job":
                pro_job,

            "uc_job":
                uc_job,

            "identity_pro":
                pro_identity_display,

            "identity_type_pro":
                pro_identity_type,

            "identity_value_pro":
                pro_identity_value,

            "identity_uc":
                uc_identity_display,

            "identity_type_uc":
                uc_identity_type,

            "identity_value_uc":
                uc_identity_value,

            "security_mode_pro":
                unique_join(
                    pro_modes
                )
                or "NOT_DECLARED",

            "security_mode_uc":
                unique_join(
                    uc_modes
                )
                or "NOT_DECLARED",

            "identity_status":
                identity_status,

            "security_status":
                security_status,

            "migration_status":
                migration_status,

            "requires_action":
                requires_action,

            "identity_notes":
                identity_notes,

            "security_notes":
                security_notes,

            "pro_source":
                pro_jobs[
                    pro_job
                ]["source"],

            "uc_yaml":
                uc_jobs[
                    uc_job
                ]["source"],

            "uc_parse_mode":
                uc_jobs[
                    uc_job
                ]["parse_mode"],
        })

    # --------------------------------------------------------
    # Orden
    # --------------------------------------------------------

    output_rows.sort(
        key=lambda row:
            normalize(
                row["job"]
            )
    )

    # --------------------------------------------------------
    # CSV
    # --------------------------------------------------------

    fieldnames = [
        "job",
        "uc_job",
        "identity_pro",
        "identity_type_pro",
        "identity_value_pro",
        "identity_uc",
        "identity_type_uc",
        "identity_value_uc",
        "security_mode_pro",
        "security_mode_uc",
        "identity_status",
        "security_status",
        "migration_status",
        "requires_action",
        "identity_notes",
        "security_notes",
        "pro_source",
        "uc_yaml",
        "uc_parse_mode",
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

    # --------------------------------------------------------
    # Resumen
    # --------------------------------------------------------

    pro_identity_counter = Counter(
        row[
            "identity_type_pro"
        ]
        for row in output_rows
    )

    uc_identity_counter = Counter(
        row[
            "identity_type_uc"
        ]
        for row in output_rows
    )

    identity_status_counter = Counter(
        row[
            "identity_status"
        ]
        for row in output_rows
    )

    security_status_counter = Counter(
        row[
            "security_status"
        ]
        for row in output_rows
    )

    action_counter = Counter(
        row[
            "requires_action"
        ]
        for row in output_rows
    )

    jobs_with_review = [
        row["job"]
        for row in output_rows
        if row[
            "requires_action"
        ] == "REVIEW"
    ]

    jobs_uc_sp = sum(
        1
        for row in output_rows
        if row[
            "identity_type_uc"
        ] == "SERVICE_PRINCIPAL"
    )

    jobs_uc_security = sum(
        1
        for row in output_rows
        if row[
            "security_mode_uc"
        ] != "NOT_DECLARED"
    )

    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 25"
    )
    print(
        "IDENTIDAD Y SEGURIDAD DE JOBS"
    )
    print("=" * 72)
    print()

    print(
        f"Jobs Workspace en snapshot       : "
        f"{len(pro_jobs)}"
    )

    print(
        f"Jobs UC disponibles              : "
        f"{len(uc_jobs)}"
    )

    print(
        f"Jobs en alcance (EXACT_NAME)     : "
        f"{len(output_rows)}"
    )

    print(
        f"Jobs fuera de alcance / sin UC   : "
        f"{len(pro_jobs) - len(output_rows)}"
    )

    print()

    print(
        "Identidad PRO:"
    )

    for status in sorted(
        pro_identity_counter
    ):
        print(
            f" - {status:<32}: "
            f"{pro_identity_counter[status]}"
        )

    print()

    print(
        "Identidad UC:"
    )

    for status in sorted(
        uc_identity_counter
    ):
        print(
            f" - {status:<32}: "
            f"{uc_identity_counter[status]}"
        )

    print()

    print(
        f"Jobs UC con Service Principal    : "
        f"{jobs_uc_sp}/{len(output_rows)}"
    )

    print(
        f"Jobs UC con data_security_mode   : "
        f"{jobs_uc_security}/{len(output_rows)}"
    )

    print()

    print(
        "Resumen identidad:"
    )

    for status in sorted(
        identity_status_counter
    ):
        print(
            f" - {status:<32}: "
            f"{identity_status_counter[status]}"
        )

    print()

    print(
        "Resumen seguridad:"
    )

    for status in sorted(
        security_status_counter
    ):
        print(
            f" - {status:<32}: "
            f"{security_status_counter[status]}"
        )

    print()

    print(
        "Resumen de acciones:"
    )

    for action in sorted(
        action_counter
    ):
        print(
            f" - {action:<32}: "
            f"{action_counter[action]}"
        )

    print()

    print(
        f"Jobs con revisión pendiente      : "
        f"{len(jobs_with_review)}"
    )

    if jobs_with_review:
        print()
        print(
            "Jobs con pendientes:"
        )

        for job in sorted(
            jobs_with_review,
            key=str.casefold,
        ):
            print(
                f" - {job}"
            )

    print()
    print(
        "Nota: este paso valida configuración declarada; "
        "no prueba permisos efectivos del Service Principal "
        "sobre Azure ni Unity Catalog."
    )

    print()

    print(
        f"Archivo generado: "
        f"{OUTPUT_FILE}"
    )

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
