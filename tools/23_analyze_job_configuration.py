#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv
import json
import re
import yaml


# ============================================================
# Assessment Workspace - Paso 23
#
# Comparativa de configuración de Jobs:
#
#   PRO = snapshot real del Workspace (snapshot/jobs/*.json)
#   UC  = YAML objetivo (input/config/jobs/UC_*.yml)
#
# Base lógica:
#   Tool 1 / 22_analyze_job_configuration_final.py
#
# Adaptación Tool 2:
#   - PRO ya no se toma de YAML estático;
#   - PRO se toma del snapshot real extraído del Workspace;
#   - se reutiliza el matching cerrado en Paso 22;
#   - sólo se analizan jobs con match EXACT_NAME;
#   - jobs UCX / NOT_FOUND permanecen fuera del alcance de
#     migración, sin desaparecer del inventario del snapshot;
#   - libraries se excluye porque ya fue cerrado en Paso 22;
#   - se reconocen propiedades default/materializadas por la
#     API del Workspace que pueden omitirse en YAML UC sin
#     representar un cambio funcional.
# ============================================================


SNAPSHOT_JOBS_DIR = Path("snapshot/jobs")
UC_JOBS_DIR = Path("input/config/jobs")
MATCHING_FILE = Path("output/job_name_matching.csv")

OUTPUT_FILE = Path(
    "output/job_configuration_migration_analysis.csv"
)


# ============================================================
# Propiedades que NO analizamos aquí
# ============================================================

IGNORED_KEYS = {
    "libraries",
}


# ============================================================
# Reglas de cambios esperados para UC
# ============================================================

EXPECTED_UC_RULES = {
    "spark_version":
        "Actualización de Databricks Runtime para el entorno UC.",

    "data_security_mode":
        "Migración del modo de seguridad legado al modelo compatible con Unity Catalog.",

    "run_as.service_principal_name":
        "Ejecución mediante Service Principal para desacoplar el job de cuentas personales.",

    "spark_env_vars.CV_EXPLOTACION_CONFIG_FILE_PATH":
        "Migración de la ruta de configuración desde mount/DBFS hacia Volume UC.",

    "notebook_path":
        "Migración del notebook a la ruta Workspace utilizada por el entorno UC.",
}


# ============================================================
# Utilidades
# ============================================================

def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize(value):
    return clean(value).lower()


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
        lambda match:
            ': "' + match.group(1) + '"',
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
# Flatten
# ============================================================

def flatten(value, prefix=""):
    result = {}

    if isinstance(value, dict):

        for key, child in value.items():

            if key in IGNORED_KEYS:
                continue

            child_prefix = (
                f"{prefix}.{key}"
                if prefix
                else str(key)
            )

            result.update(
                flatten(
                    child,
                    child_prefix,
                )
            )

        return result

    if isinstance(value, list):

        for index, child in enumerate(value):

            identifier = str(index)

            if isinstance(child, dict):

                for candidate in [
                    "task_key",
                    "job_cluster_key",
                ]:
                    if candidate in child:
                        identifier = (
                            clean(
                                child.get(candidate)
                            )
                            or identifier
                        )
                        break

            child_prefix = (
                f"{prefix}[{identifier}]"
            )

            result.update(
                flatten(
                    child,
                    child_prefix,
                )
            )

        return result

    result[prefix] = value
    return result


# ============================================================
# Extraer UC desde Asset Bundle YAML
# ============================================================

def extract_uc_job(data):
    if not isinstance(data, dict):
        return "", {}

    resources = data.get(
        "resources",
        {}
    )

    jobs = resources.get(
        "jobs",
        {}
    )

    if not isinstance(jobs, dict):
        return "", {}

    for _, job_data in jobs.items():

        if not isinstance(job_data, dict):
            continue

        job_name = clean(
            job_data.get("name")
        )

        return (
            job_name,
            flatten(job_data),
        )

    return "", {}


# ============================================================
# Extraer PRO desde settings reales del Workspace
# ============================================================

def extract_pro_job(data):
    """
    El extractor guarda full.settings.as_dict(), por lo que el
    JSON ya representa directamente la configuración recreable
    del job. No existe wrapper resources.jobs.
    """

    if not isinstance(data, dict):
        return "", {}

    job_name = clean(
        data.get("name")
    )

    return (
        job_name,
        flatten(data),
    )


# ============================================================
# Paths / semántica
# ============================================================

def simplify_property_path(property_path):
    value = property_path

    value = re.sub(
        r'^tasks\[[^\]]+\]\.',
        '',
        value,
    )

    value = re.sub(
        r'^job_clusters\[[^\]]+\]\.new_cluster\.',
        '',
        value,
    )

    value = re.sub(
        r'^new_cluster\.',
        '',
        value,
    )

    if value.startswith("run_as."):
        return value

    if value.endswith(
        "notebook_task.notebook_path"
    ):
        return "notebook_path"

    match = re.search(
        r'spark_env_vars\.([^\.]+)$',
        value,
    )

    if match:
        return (
            "spark_env_vars."
            + match.group(1)
        )

    match = re.search(
        r'spark_conf\.(.+)$',
        value,
    )

    if match:
        return (
            "spark_conf."
            + match.group(1)
        )

    parts = value.split(".")

    if parts:
        return parts[-1]

    return value


def semantic_property_key(property_path):
    """
    Permite reconocer reubicaciones estructurales como:

      PRO: tasks[task].new_cluster.spark_version
      UC : job_clusters[key].new_cluster.spark_version

    cuando conservan el mismo valor.
    """

    value = property_path

    match = re.match(
        r'^tasks\[[^\]]+\]\.new_cluster\.(.+)$',
        value,
    )

    if match:
        return (
            "cluster."
            + match.group(1)
        )

    match = re.match(
        r'^job_clusters\[[^\]]+\]\.new_cluster\.(.+)$',
        value,
    )

    if match:
        return (
            "cluster."
            + match.group(1)
        )

    match = re.match(
        r'^new_cluster\.(.+)$',
        value,
    )

    if match:
        return (
            "cluster."
            + match.group(1)
        )

    match = re.match(
        r'^tasks\[[^\]]+\]\.notebook_task\.(.+)$',
        value,
    )

    if match:
        return (
            "notebook_task."
            + match.group(1)
        )

    match = re.match(
        r'^tasks\[[^\]]+\]\.(.+)$',
        value,
    )

    if match:
        return (
            "task."
            + match.group(1)
        )

    return value


def serialize_value(value):
    if value is None:
        return ""

    if isinstance(value, bool):
        return str(value).lower()

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
        )

    return clean(value)


# ============================================================
# Clasificación - misma semántica final de Tool 1
# + reglas Workspace JSON detectadas en PRO real
# ============================================================

def classify_change(
    property_path,
    pro_value,
    uc_value,
):
    simplified = (
        simplify_property_path(
            property_path
        )
    )

    pro_text = serialize_value(
        pro_value
    )

    uc_text = serialize_value(
        uc_value
    )

    # --------------------------------------------------------
    # Sin cambio
    # --------------------------------------------------------

    if (
        pro_value is not None
        and uc_value is not None
        and pro_text == uc_text
    ):
        return (
            "UNCHANGED",
            "NO",
            "Sin cambio.",
        )

    # --------------------------------------------------------
    # Defaults materializados por Workspace API
    #
    # El snapshot real puede devolver estas propiedades de
    # forma explícita aunque el YAML objetivo no las declare.
    # Su omisión no representa una diferencia funcional.
    # --------------------------------------------------------

    if (
        simplified == "disabled"
        and pro_value is not None
        and uc_value is None
        and pro_text.lower() == "false"
    ):
        return (
            "DEFAULT_WORKSPACE_PROPERTY_IGNORED",
            "NO",
            (
                "disabled=false materializado por Workspace; "
                "su omisión en UC no modifica el comportamiento "
                "funcional de la task."
            ),
        )

    if (
        simplified == "run_if"
        and pro_value is not None
        and uc_value is None
        and pro_text.upper() == "ALL_SUCCESS"
    ):
        return (
            "DEFAULT_WORKSPACE_PROPERTY_IGNORED",
            "NO",
            (
                "run_if=ALL_SUCCESS materializado por Workspace; "
                "corresponde al comportamiento normal de ejecución "
                "y puede omitirse en el YAML UC."
            ),
        )

    # --------------------------------------------------------
    # performance_target detectado en snapshot PRO real
    #
    # No forma parte de los requisitos funcionales documentados
    # para la migración. Se conserva como evidencia pero no como
    # acción de implementación.
    # --------------------------------------------------------

    if (
        simplified == "performance_target"
        and pro_value is not None
        and uc_value is None
        and pro_text.upper() == "PERFORMANCE_OPTIMIZED"
    ):
        return (
            "WORKSPACE_PROPERTY_IGNORED",
            "NO",
            (
                "performance_target=PERFORMANCE_OPTIMIZED está "
                "materializado en la configuración real PRO y no "
                "declarado en el YAML UC; no se identifica como "
                "requisito funcional de esta migración."
            ),
        )

    # --------------------------------------------------------
    # Propiedades explícitamente esperadas
    # --------------------------------------------------------

    if simplified in EXPECTED_UC_RULES:
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            EXPECTED_UC_RULES[
                simplified
            ],
        )

    # --------------------------------------------------------
    # Compute
    # --------------------------------------------------------

    if simplified in {
        "node_type_id",
        "driver_node_type_id",
    }:
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Actualización intencional del tipo de cómputo "
                "manteniendo capacidad equivalente para UC."
            ),
        )

    if simplified == "spark_version":
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            EXPECTED_UC_RULES[
                "spark_version"
            ],
        )

    if simplified == "data_security_mode":
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            EXPECTED_UC_RULES[
                "data_security_mode"
            ],
        )

    # --------------------------------------------------------
    # Run As
    # --------------------------------------------------------

    if simplified in {
        "user_name",
        "run_as.user_name",
        "run_as.service_principal_name",
        "service_principal_name",
    }:
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Cambio intencional de identidad de ejecución "
                "hacia Service Principal en UC."
            ),
        )

    # --------------------------------------------------------
    # Topología de cluster
    # --------------------------------------------------------

    if simplified == "job_cluster_key":
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Cambio estructural esperado: UC referencia un "
                "job_cluster mediante job_cluster_key."
            ),
        )

    # --------------------------------------------------------
    # Queue
    # --------------------------------------------------------

    if (
        simplified == "enabled"
        and "queue." in property_path
    ):
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            "Configuración de queue incorporada en UC.",
        )

    # --------------------------------------------------------
    # Spark conf
    # --------------------------------------------------------

    if simplified.startswith(
        "spark_conf."
    ):
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Ajuste intencional de Spark configuration "
                "asociado a la homologación del runtime UC."
            ),
        )

    # --------------------------------------------------------
    # Variables de ambiente
    # --------------------------------------------------------

    if simplified.startswith(
        "spark_env_vars."
    ):
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Cambio intencional de variable de ambiente "
                "para el entorno UC."
            ),
        )

    # --------------------------------------------------------
    # Notificaciones
    # --------------------------------------------------------

    if (
        property_path.startswith(
            "email_notifications."
        )
        or ".email_notifications."
        in property_path
        or property_path.startswith(
            "webhook_notifications."
        )
        or ".webhook_notifications."
        in property_path
        or property_path.startswith(
            "notification_settings."
        )
        or ".notification_settings."
        in property_path
    ):
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Actualización intencional de notificaciones "
                "y destinatarios para la operación en UC."
            ),
        )

    # --------------------------------------------------------
    # Tags
    # --------------------------------------------------------

    if "custom_tags." in property_path:
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Tag de identificación/trazabilidad incorporado "
                "o ajustado para el entorno UC."
            ),
        )

    # --------------------------------------------------------
    # Health
    # --------------------------------------------------------

    if (
        property_path.startswith("health.")
        or ".health." in property_path
    ):
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Regla de health/monitoreo incorporada en UC "
                "como mejora operativa."
            ),
        )

    # --------------------------------------------------------
    # Existing cluster -> compute definido en job
    # --------------------------------------------------------

    if simplified == "existing_cluster_id":
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "El uso de existing_cluster_id en PRO se reemplaza "
                "por cómputo administrado/definido en el job UC."
            ),
        )

    # --------------------------------------------------------
    # cluster_name vacío legado
    # --------------------------------------------------------

    if (
        simplified == "cluster_name"
        and (
            pro_value is None
            or serialize_value(
                pro_value
            ) == ""
        )
    ):
        return (
            "EMPTY_LEGACY_PROPERTY_IGNORED",
            "NO",
            (
                "cluster_name vacío en configuración legacy; "
                "su ausencia en UC no representa pérdida "
                "de configuración."
            ),
        )

    # --------------------------------------------------------
    # Diferencias operativas/configurativas conocidas
    # --------------------------------------------------------

    operational_properties = {
        "pause_status",
        "quartz_cron_expression",
        "timezone_id",
        "max_concurrent_runs",
        "max_retries",
        "min_retry_interval_millis",
        "timeout_seconds",
        "value",
        "num_workers",
        "runtime_engine",
        "enable_elastic_disk",
        "availability",
        "first_on_demand",
        "spot_bid_max_price",
        "retry_on_timeout",
        "format",
    }

    if simplified in operational_properties:
        return (
            "EXPECTED_UC_CHANGE",
            "NO",
            (
                "Diferencia operativa/configurativa considerada "
                "parte de la homologación definida para UC."
            ),
        )

    # --------------------------------------------------------
    # Sólo PRO
    # --------------------------------------------------------

    if (
        pro_value is not None
        and uc_value is None
    ):
        return (
            "REMOVED_IN_UC",
            "REVIEW",
            (
                "Propiedad existente en PRO y ausente en UC. "
                "Revisar si no corresponde a una reubicación "
                "estructural o decisión de diseño."
            ),
        )

    # --------------------------------------------------------
    # Sólo UC
    # --------------------------------------------------------

    if (
        pro_value is None
        and uc_value is not None
    ):
        return (
            "ADDED_IN_UC",
            "REVIEW",
            (
                "Propiedad nueva en UC. Revisar si no corresponde "
                "a una reubicación estructural o decisión de diseño."
            ),
        )

    # --------------------------------------------------------
    # Cambio genérico real
    # --------------------------------------------------------

    return (
        "VALUE_CHANGED",
        "REVIEW",
        (
            "La propiedad cambió entre PRO y UC y no coincide "
            "con una regla de homologación conocida."
        ),
    )


# ============================================================
# Cargar matching del Paso 22
# ============================================================

if not MATCHING_FILE.exists():
    raise FileNotFoundError(
        "No existe output/job_name_matching.csv. "
        "Ejecuta primero el Paso 22."
    )

matching_rows = read_csv(
    MATCHING_FILE
)

exact_matches = {}

for row in matching_rows:
    method = clean(
        row.get("match_method")
        or row.get("matching_method")
        or row.get("method")
    )

    if method != "EXACT_NAME":
        continue

    pro_name = clean(
        row.get("workspace_job")
        or row.get("pro_job")
        or row.get("job")
        or row.get("workspace_name")
    )

    uc_name = clean(
        row.get("uc_job")
        or row.get("matched_uc_job")
        or row.get("uc_name")
    )

    if pro_name and uc_name:
        exact_matches[pro_name] = uc_name


if not exact_matches:
    raise RuntimeError(
        "Paso 22 no contiene matches EXACT_NAME utilizables."
    )


# ============================================================
# Cargar jobs PRO reales del snapshot
# ============================================================

pro_jobs = {}
pro_paths = {}

for path in sorted(
    SNAPSHOT_JOBS_DIR.glob("*.json")
):
    try:
        data = load_json(path)
    except Exception as e:
        print(
            f"ADVERTENCIA: no se pudo leer {path}: {e}"
        )
        continue

    job_name, config = extract_pro_job(
        data
    )

    if not job_name:
        continue

    pro_jobs[job_name] = config
    pro_paths[job_name] = str(path)


# ============================================================
# Cargar YAML UC
# ============================================================

uc_jobs = {}
uc_paths = {}
uc_parse_modes = {}

yaml_files = []

for pattern in [
    "UC_*.yml",
    "UC_*.yaml",
]:
    yaml_files.extend(
        UC_JOBS_DIR.glob(pattern)
    )

for path in sorted(yaml_files):

    data, parse_mode, error = load_yaml(
        path
    )

    if data is None:
        print(
            f"ERROR leyendo YAML UC: {path}"
        )
        print(f"  {error}")
        continue

    job_name, config = extract_uc_job(
        data
    )

    if not job_name:
        continue

    uc_jobs[job_name] = config
    uc_paths[job_name] = str(path)
    uc_parse_modes[job_name] = (
        parse_mode
    )


# ============================================================
# Validar los matches EXACT_NAME
# ============================================================

missing_pro = [
    name
    for name in exact_matches
    if name not in pro_jobs
]

missing_uc = [
    uc_name
    for uc_name in exact_matches.values()
    if uc_name not in uc_jobs
]

if missing_pro:
    raise RuntimeError(
        "Jobs EXACT_NAME del Paso 22 no encontrados "
        "en snapshot PRO:\n - "
        + "\n - ".join(missing_pro)
    )

if missing_uc:
    raise RuntimeError(
        "Jobs EXACT_NAME del Paso 22 no encontrados "
        "en YAML UC:\n - "
        + "\n - ".join(missing_uc)
    )


# ============================================================
# Comparar
# ============================================================

output_rows = []


def append_output_row(
    pro_job,
    uc_job,
    pro_path,
    uc_path,
    pro_value,
    uc_value,
    migration_status,
    requires_action,
    recommended_action,
):
    property_path = (
        uc_path
        or pro_path
    )

    output_rows.append({
        "job":
            pro_job,

        "uc_job":
            uc_job,

        "property_path":
            property_path,

        "pro_property_path":
            pro_path,

        "uc_property_path":
            uc_path,

        "property":
            simplify_property_path(
                property_path
            ),

        "semantic_property":
            semantic_property_key(
                property_path
            ),

        "pro_value":
            serialize_value(
                pro_value
            ),

        "uc_value":
            serialize_value(
                uc_value
            ),

        "migration_status":
            migration_status,

        "requires_action":
            requires_action,

        "recommended_action":
            recommended_action,

        "pro_source":
            pro_paths.get(
                pro_job,
                "",
            ),

        "uc_yaml":
            uc_paths.get(
                uc_job,
                "",
            ),

        "pro_parse_mode":
            "WORKSPACE_JSON",

        "uc_parse_mode":
            uc_parse_modes.get(
                uc_job,
                "",
            ),
    })


for pro_job in sorted(
    exact_matches,
    key=str.casefold,
):
    uc_job = exact_matches[
        pro_job
    ]

    pro_config = pro_jobs[
        pro_job
    ]

    uc_config = uc_jobs[
        uc_job
    ]

    matched_pro = set()
    matched_uc = set()

    # --------------------------------------------------------
    # 1. Match por path exacto
    # --------------------------------------------------------

    exact_paths = (
        set(pro_config.keys())
        & set(uc_config.keys())
    )

    for property_path in sorted(
        exact_paths
    ):
        pro_value = pro_config[
            property_path
        ]

        uc_value = uc_config[
            property_path
        ]

        (
            migration_status,
            requires_action,
            recommended_action,
        ) = classify_change(
            property_path,
            pro_value,
            uc_value,
        )

        append_output_row(
            pro_job,
            uc_job,
            property_path,
            property_path,
            pro_value,
            uc_value,
            migration_status,
            requires_action,
            recommended_action,
        )

        matched_pro.add(
            property_path
        )

        matched_uc.add(
            property_path
        )

    # --------------------------------------------------------
    # 2. Reubicaciones estructurales
    #
    # Mismo semantic_property + mismo valor.
    # --------------------------------------------------------

    pro_remaining = [
        path
        for path in pro_config
        if path not in matched_pro
    ]

    uc_remaining = [
        path
        for path in uc_config
        if path not in matched_uc
    ]

    uc_index = defaultdict(list)

    for uc_path in uc_remaining:

        key = (
            semantic_property_key(
                uc_path
            ),
            serialize_value(
                uc_config[
                    uc_path
                ]
            ),
        )

        uc_index[
            key
        ].append(
            uc_path
        )

    for pro_path in pro_remaining:

        key = (
            semantic_property_key(
                pro_path
            ),
            serialize_value(
                pro_config[
                    pro_path
                ]
            ),
        )

        candidates = [
            path
            for path in uc_index.get(
                key,
                [],
            )
            if path not in matched_uc
        ]

        if not candidates:
            continue

        uc_path = candidates[0]

        append_output_row(
            pro_job,
            uc_job,
            pro_path,
            uc_path,
            pro_config[
                pro_path
            ],
            uc_config[
                uc_path
            ],
            "STRUCTURAL_RELOCATION",
            "NO",
            (
                "La propiedad conserva el mismo valor y sólo "
                "cambió de ubicación estructural "
                "(por ejemplo task.new_cluster -> job_clusters)."
            ),
        )

        matched_pro.add(
            pro_path
        )

        matched_uc.add(
            uc_path
        )

    # --------------------------------------------------------
    # 3. Restantes sólo PRO
    # --------------------------------------------------------

    for pro_path in sorted(
        path
        for path in pro_config
        if path not in matched_pro
    ):
        pro_value = pro_config[
            pro_path
        ]

        (
            migration_status,
            requires_action,
            recommended_action,
        ) = classify_change(
            pro_path,
            pro_value,
            None,
        )

        append_output_row(
            pro_job,
            uc_job,
            pro_path,
            "",
            pro_value,
            None,
            migration_status,
            requires_action,
            recommended_action,
        )

    # --------------------------------------------------------
    # 4. Restantes sólo UC
    # --------------------------------------------------------

    for uc_path in sorted(
        path
        for path in uc_config
        if path not in matched_uc
    ):
        uc_value = uc_config[
            uc_path
        ]

        (
            migration_status,
            requires_action,
            recommended_action,
        ) = classify_change(
            uc_path,
            None,
            uc_value,
        )

        append_output_row(
            pro_job,
            uc_job,
            "",
            uc_path,
            None,
            uc_value,
            migration_status,
            requires_action,
            recommended_action,
        )


# ============================================================
# Orden
# ============================================================

STATUS_ORDER = {
    "VALUE_CHANGED": 1,
    "REMOVED_IN_UC": 2,
    "ADDED_IN_UC": 3,
    "EXPECTED_UC_CHANGE": 4,
    "EMPTY_LEGACY_PROPERTY_IGNORED": 5,
    "DEFAULT_WORKSPACE_PROPERTY_IGNORED": 6,
    "WORKSPACE_PROPERTY_IGNORED": 7,
    "STRUCTURAL_RELOCATION": 8,
    "UNCHANGED": 9,
}


output_rows.sort(
    key=lambda row: (
        STATUS_ORDER.get(
            row[
                "migration_status"
            ],
            99,
        ),
        normalize(
            row[
                "job"
            ]
        ),
        normalize(
            row[
                "property_path"
            ]
        ),
    )
)


# ============================================================
# CSV
# ============================================================

OUTPUT_FILE.parent.mkdir(
    parents=True,
    exist_ok=True,
)

fieldnames = [
    "job",
    "uc_job",
    "property_path",
    "pro_property_path",
    "uc_property_path",
    "property",
    "semantic_property",
    "pro_value",
    "uc_value",
    "migration_status",
    "requires_action",
    "recommended_action",
    "pro_source",
    "uc_yaml",
    "pro_parse_mode",
    "uc_parse_mode",
]

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
    writer.writerows(
        output_rows
    )


# ============================================================
# Resumen
# ============================================================

status_counter = Counter(
    row[
        "migration_status"
    ]
    for row in output_rows
)

action_counter = Counter(
    row[
        "requires_action"
    ]
    for row in output_rows
)

jobs_with_actions = {
    row[
        "job"
    ]
    for row in output_rows
    if row[
        "requires_action"
    ]
    in {
        "YES",
        "REVIEW",
    }
}


print("=" * 72)
print(
    "ASSESSMENT WORKSPACE - PASO 23"
)
print(
    "CONFIGURACION DE JOBS WORKSPACE PRO -> UNITY CATALOG"
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
    f"{len(exact_matches)}"
)

print(
    f"Jobs fuera de alcance / sin UC   : "
    f"{len(pro_jobs) - len(exact_matches)}"
)

print(
    f"Propiedades analizadas           : "
    f"{len(output_rows)}"
)

print()

print(
    "Resumen por estado:"
)

for status in sorted(
    status_counter,
    key=lambda value: (
        STATUS_ORDER.get(
            value,
            99,
        ),
        value,
    ),
):
    print(
        f" - {status:<34}: "
        f"{status_counter[status]}"
    )

print()

print(
    "Resumen de acciones:"
)

for action in sorted(
    action_counter
):
    print(
        f" - {action:<34}: "
        f"{action_counter[action]}"
    )

print()

print(
    f"Jobs con revisión pendiente      : "
    f"{len(jobs_with_actions)}"
)

print(
    f"Reubicaciones estructurales      : "
    f"{status_counter.get('STRUCTURAL_RELOCATION', 0)}"
)

print(
    f"Propiedades legacy vacías ignoradas: "
    f"{status_counter.get('EMPTY_LEGACY_PROPERTY_IGNORED', 0)}"
)

print(
    f"Defaults Workspace ignorados     : "
    f"{status_counter.get('DEFAULT_WORKSPACE_PROPERTY_IGNORED', 0)}"
)

print(
    f"Propiedades Workspace ignoradas  : "
    f"{status_counter.get('WORKSPACE_PROPERTY_IGNORED', 0)}"
)

if jobs_with_actions:
    print()
    print(
        "Jobs con pendientes:"
    )

    for job in sorted(
        jobs_with_actions,
        key=str.casefold,
    ):
        print(
            f" - {job}"
        )

print()

print(
    f"Archivo generado: "
    f"{OUTPUT_FILE}"
)

print()
print("=" * 72)
