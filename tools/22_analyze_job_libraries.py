#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
from difflib import SequenceMatcher
import csv
import json
import re
import yaml


# ============================================================
# Assessment Workspace - Paso 22 V2
#
# Comparativa REAL:
#
#   Workspace UAT/PRO (snapshot/jobs/*.json)
#                vs
#   definición objetivo UC (input/config/jobs/UC_*.yml)
#
# Reproduce el contrato y las reglas del Paso 21 de Tool 1:
#   output/job_library_migration_analysis.csv
#
# Además genera:
#   output/job_name_matching.csv
#
# para dejar auditado cómo se emparejó cada job Workspace con
# su homólogo lógico UC.
# ============================================================


ROOT = Path(__file__).resolve().parents[1]

JOBS_DIR = ROOT / "snapshot" / "jobs"
JOBS_INDEX_FILE = ROOT / "snapshot" / "jobs_index.json"

CONFIG_JOBS_DIR = ROOT / "input" / "config" / "jobs"

OUTPUT_FILE = (
    ROOT / "output" / "job_library_migration_analysis.csv"
)

MATCH_OUTPUT_FILE = (
    ROOT / "output" / "job_name_matching.csv"
)


# ============================================================
# Baseline UC acordado - mismo criterio Tool 1
# ============================================================

EXPECTED_MAVEN = {
    "com.github.jsurfer:jsurfer-gson":
        "1.6.4",

    "com.microsoft.azure:applicationinsights-core":
        "2.6.4",

    "com.azure:azure-storage-blob":
        "12.24.1",

    "com.microsoft.azure:azure-storage":
        "8.6.6",

    "org.postgresql:postgresql":
        "42.5.3",

    "org.scalaj:scalaj-http_2.12":
        "2.4.2",

    "com.crealytics:spark-excel_2.12":
        "0.13.5",
}

EXPECTED_PARSER = "parser_2.12-0.2.jar"

OBSOLETE_JARS = {
    "customcfdi-gson_2_8_6-10451.jar",
}


# ============================================================
# Alias UAT conocidos
#
# Son equivalencias funcionales, no fuzzy matching.
# Esta tabla puede crecer si otro UAT usa nombres artificiales.
#
# Para PRO, normalmente no será necesaria porque sus nombres ya
# están homologados con los YAML UC.
# ============================================================

RAW_KNOWN_JOB_ALIASES = {
    "job_mediano":
        "Cron-COV-Explotacion-Cedulas_M",

    "job_grande":
        "Cron-COV-Explotacion-Cedulas_G",

    "job_pequenio":
        "Cron-COV-Explotacion-Cedulas_CH",

    "job_pequeño":
        "Cron-COV-Explotacion-Cedulas_CH",

    "job_cedulas":
        "Cron-COV-Explotacion-Cedulas",

    "job_coordinadorrn004":
        "Cron-COV-Explotacion-UltimosRecibidos",
}


# ============================================================
# Utilidades generales
# ============================================================

def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize(value):
    return clean(value).lower()


def normalize_name(value):
    value = clean(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def compact_name(value):
    return re.sub(
        r"[^a-z0-9]+",
        "",
        clean(value).casefold()
    )


# Las claves de aliases se normalizan con la misma función que
# el nombre recibido desde Workspace. Esto evita que:
#
#   job_mediano
#       !=
#   job mediano
#
# por una diferencia artificial de "_" vs espacio.
KNOWN_JOB_ALIASES = {
    normalize_name(source):
        target
    for source, target
    in RAW_KNOWN_JOB_ALIASES.items()
}


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


def unique_join(values):
    return " | ".join(unique(values))


def load_json(path):
    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def library_basename(value):
    value = clean(value)

    if not value:
        return ""

    return (
        value
        .replace("\\", "/")
        .rstrip("/")
        .split("/")[-1]
    )


def parse_maven_coordinate(coordinate):
    coordinate = clean(coordinate)
    parts = coordinate.split(":")

    if len(parts) < 3:
        return coordinate, ""

    return (
        ":".join(parts[:-1]),
        parts[-1]
    )


def get_logical_job_name(path):
    name = path.stem

    for prefix in (
        "PRO_",
        "UAT_",
        "UC_",
    ):
        if name.upper().startswith(prefix):
            return name[len(prefix):]

    return name


# ============================================================
# Normalización de YAML histórico - heredada Tool 1
# ============================================================

def normalize_yaml_text(text):
    text = text.replace(
        "\t",
        "  "
    )

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
        flags=re.MULTILINE
    )

    return text


def load_yaml(path):
    text = path.read_text(
        encoding="utf-8"
    )

    try:
        return (
            yaml.safe_load(text),
            "DIRECT",
            ""
        )

    except Exception:
        try:
            normalized_text = (
                normalize_yaml_text(text)
            )

            return (
                yaml.safe_load(
                    normalized_text
                ),
                "NORMALIZED",
                ""
            )

        except Exception as exc:
            return (
                None,
                "ERROR",
                (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )
            )


# ============================================================
# Canonicalizar JARs exportados por Databricks
#
# En Workspace pueden aparecer:
#
#   UUID-parser_2_12_0_1-hash.jar
#
# mientras en YAML:
#
#   parser_2.12-0.1.jar
#
# Para comparar semánticamente necesitamos recuperar el nombre
# lógico del componente.
# ============================================================

def canonical_jar_name(value):
    base = library_basename(value)
    low = base.casefold()
    compact = compact_name(base)

    # parserCV2V
    if "parsercv2v" in compact:
        return "parserCV2V.jar"

    # parser 2.12 0.2
    if (
        "parser21202" in compact
        or "parsercovol21202" in compact
    ):
        return "parser_2.12-0.2.jar"

    # parser 2.12 0.1
    if (
        "parser21201" in compact
        or "parsercovol21201" in compact
    ):
        return "parser_2.12-0.1.jar"

    # custom cfdi gson
    if (
        "customcfdi" in compact
        and "gson" in compact
    ):
        return (
            "customcfdi-gson_2_8_6-10451.jar"
        )

    return base


# ============================================================
# Extraer librerías del Workspace JSON
# ============================================================

def extract_workspace_library(
    library,
    task_key,
):
    if not isinstance(
        library,
        dict
    ):
        return None

    if "maven" in library:
        maven = library.get(
            "maven",
            {}
        ) or {}

        coordinate = clean(
            maven.get(
                "coordinates"
            )
        )

        if not coordinate:
            return None

        (
            library_name,
            version
        ) = parse_maven_coordinate(
            coordinate
        )

        return {
            "task_key":
                task_key,

            "library_type":
                "MAVEN",

            "library_name":
                library_name,

            "version":
                version,

            "value":
                coordinate,
        }

    if "jar" in library:
        jar = clean(
            library.get(
                "jar"
            )
        )

        if not jar:
            return None

        return {
            "task_key":
                task_key,

            "library_type":
                "JAR",

            "library_name":
                canonical_jar_name(
                    jar
                ),

            "version":
                "",

            # Se conserva el valor físico real del Workspace.
            "value":
                jar,
        }

    if "pypi" in library:
        pypi = library.get(
            "pypi",
            {}
        ) or {}

        package = clean(
            pypi.get(
                "package"
            )
        )

        if not package:
            return None

        return {
            "task_key":
                task_key,

            "library_type":
                "PYPI",

            "library_name":
                package,

            "version":
                "",

            "value":
                package,
        }

    return None


def extract_workspace_job(
    path,
    job_id_by_file,
):
    data = load_json(path)

    if not isinstance(
        data,
        dict
    ):
        raise TypeError(
            "El archivo de job no contiene "
            "un objeto JSON."
        )

    job_name = clean(
        data.get(
            "name"
        )
    )

    if not job_name:
        stem = path.stem
        parts = stem.split(
            "_",
            1
        )

        job_name = (
            parts[1]
            if len(parts) == 2
            else stem
        )

    libraries = []
    notebook_targets = []

    tasks = data.get(
        "tasks",
        []
    )

    if not isinstance(
        tasks,
        list
    ):
        tasks = []

    for task in tasks:
        if not isinstance(
            task,
            dict
        ):
            continue

        task_key = clean(
            task.get(
                "task_key"
            )
        )

        notebook_task = (
            task.get(
                "notebook_task"
            )
            or {}
        )

        if isinstance(
            notebook_task,
            dict
        ):
            notebook_path = clean(
                notebook_task.get(
                    "notebook_path"
                )
            )

            if notebook_path:
                notebook_targets.append(
                    notebook_path
                )

        task_libraries = task.get(
            "libraries",
            []
        )

        if not isinstance(
            task_libraries,
            list
        ):
            task_libraries = []

        for library in task_libraries:
            extracted = (
                extract_workspace_library(
                    library,
                    task_key
                )
            )

            if extracted:
                libraries.append(
                    extracted
                )

    return {
        "job":
            job_name,

        "job_id":
            job_id_by_file.get(
                path.name.casefold(),
                ""
            ),

        "source_file":
            str(path),

        "libraries":
            libraries,

        "notebook_targets":
            unique(
                notebook_targets
            ),
    }


def load_job_id_map():
    result = {}

    if not JOBS_INDEX_FILE.exists():
        return result

    try:
        data = load_json(
            JOBS_INDEX_FILE
        )

    except Exception:
        return result

    if not isinstance(
        data,
        list
    ):
        return result

    for item in data:
        if not isinstance(
            item,
            dict
        ):
            continue

        file_value = clean(
            item.get(
                "file"
            )
        )

        if not file_value:
            continue

        result[
            library_basename(
                file_value
            ).casefold()
        ] = clean(
            item.get(
                "job_id"
            )
        )

    return result


# ============================================================
# Extraer librerías del YAML UC
# ============================================================

def extract_uc_job_from_yaml(path):
    (
        data,
        parse_mode,
        error
    ) = load_yaml(
        path
    )

    if data is None:
        return None, error

    if not isinstance(
        data,
        dict
    ):
        return None, (
            "YAML no contiene un objeto."
        )

    resources = data.get(
        "resources",
        {}
    )

    jobs = (
        resources.get(
            "jobs",
            {}
        )
        if isinstance(
            resources,
            dict
        )
        else {}
    )

    if not isinstance(
        jobs,
        dict
    ):
        jobs = {}

    logical_name = (
        get_logical_job_name(
            path
        )
    )

    extracted_candidates = []

    for _, job_data in jobs.items():
        if not isinstance(
            job_data,
            dict
        ):
            continue

        job_name = clean(
            job_data.get(
                "name"
            )
        )

        libraries = []
        notebook_targets = []

        tasks = job_data.get(
            "tasks",
            []
        )

        if not isinstance(
            tasks,
            list
        ):
            tasks = []

        for task in tasks:
            if not isinstance(
                task,
                dict
            ):
                continue

            task_key = clean(
                task.get(
                    "task_key"
                )
            )

            notebook_task = (
                task.get(
                    "notebook_task"
                )
                or {}
            )

            if isinstance(
                notebook_task,
                dict
            ):
                notebook_path = clean(
                    notebook_task.get(
                        "notebook_path"
                    )
                )

                if notebook_path:
                    notebook_targets.append(
                        notebook_path
                    )

            for library in task.get(
                "libraries",
                []
            ):
                if not isinstance(
                    library,
                    dict
                ):
                    continue

                if "maven" in library:
                    maven = (
                        library.get(
                            "maven",
                            {}
                        )
                        or {}
                    )

                    coordinate = clean(
                        maven.get(
                            "coordinates"
                        )
                    )

                    if coordinate:
                        (
                            library_name,
                            version
                        ) = (
                            parse_maven_coordinate(
                                coordinate
                            )
                        )

                        libraries.append({
                            "task_key":
                                task_key,

                            "library_type":
                                "MAVEN",

                            "library_name":
                                library_name,

                            "version":
                                version,

                            "value":
                                coordinate,
                        })

                elif "jar" in library:
                    jar = clean(
                        library.get(
                            "jar"
                        )
                    )

                    if jar:
                        libraries.append({
                            "task_key":
                                task_key,

                            "library_type":
                                "JAR",

                            "library_name":
                                canonical_jar_name(
                                    jar
                                ),

                            "version":
                                "",

                            "value":
                                jar,
                        })

                elif "pypi" in library:
                    pypi = (
                        library.get(
                            "pypi",
                            {}
                        )
                        or {}
                    )

                    package = clean(
                        pypi.get(
                            "package"
                        )
                    )

                    if package:
                        libraries.append({
                            "task_key":
                                task_key,

                            "library_type":
                                "PYPI",

                            "library_name":
                                package,

                            "version":
                                "",

                            "value":
                                package,
                        })

        extracted_candidates.append({
            "logical_job":
                logical_name,

            "yaml_job_name":
                job_name or logical_name,

            "source_file":
                str(path),

            "parse_mode":
                parse_mode,

            "libraries":
                libraries,

            "notebook_targets":
                unique(
                    notebook_targets
                ),
        })

    # Normalmente un job por YAML.
    if not extracted_candidates:
        return {
            "logical_job":
                logical_name,

            "yaml_job_name":
                logical_name,

            "source_file":
                str(path),

            "parse_mode":
                parse_mode,

            "libraries":
                [],

            "notebook_targets":
                [],
        }, ""

    return (
        extracted_candidates[0],
        ""
    )


# ============================================================
# Matching Workspace -> UC
# ============================================================

GENERIC_JOB_TOKENS = {
    "cron",
    "cov",
    "job",
    "explotacion",
    "streams",
    "stream",
}


def significant_tokens(value):
    tokens = set(
        normalize_name(value).split()
    )

    return {
        token
        for token in tokens
        if token not in GENERIC_JOB_TOKENS
    }


def notebook_basenames(paths):
    return {
        library_basename(
            path
        ).casefold()
        for path in paths
        if clean(path)
    }


def similarity_score(
    workspace_job,
    uc_job,
):
    a = normalize_name(
        workspace_job
    )

    b = normalize_name(
        uc_job
    )

    return SequenceMatcher(
        None,
        a,
        b
    ).ratio()


def token_score(
    workspace_job,
    uc_job,
):
    a = significant_tokens(
        workspace_job
    )

    b = significant_tokens(
        uc_job
    )

    if not a or not b:
        return 0.0

    intersection = len(
        a & b
    )

    union = len(
        a | b
    )

    if not union:
        return 0.0

    return (
        intersection
        / union
    )


def notebook_overlap_score(
    workspace_paths,
    uc_paths,
):
    a = notebook_basenames(
        workspace_paths
    )

    b = notebook_basenames(
        uc_paths
    )

    if not a or not b:
        return 0.0

    overlap = a & b

    if not overlap:
        return 0.0

    return (
        len(overlap)
        / max(
            1,
            min(
                len(a),
                len(b)
            )
        )
    )


def match_workspace_job(
    workspace_job,
    uc_jobs,
):
    workspace_name = clean(
        workspace_job[
            "job"
        ]
    )

    normalized_workspace = (
        normalize_name(
            workspace_name
        )
    )

    # --------------------------------------------------------
    # 1. Alias conocido
    # --------------------------------------------------------

    alias_target = (
        KNOWN_JOB_ALIASES.get(
            normalized_workspace
        )
    )

    if alias_target:
        for candidate in uc_jobs:
            if (
                normalize_name(
                    candidate[
                        "logical_job"
                    ]
                )
                ==
                normalize_name(
                    alias_target
                )
            ):
                return (
                    candidate,
                    "KNOWN_ALIAS",
                    1.0,
                    ""
                )

    # --------------------------------------------------------
    # 2. Exact match contra nombre lógico o name interno YAML
    # --------------------------------------------------------

    exact = []

    for candidate in uc_jobs:
        names = {
            normalize_name(
                candidate[
                    "logical_job"
                ]
            ),
            normalize_name(
                candidate[
                    "yaml_job_name"
                ]
            ),
        }

        if (
            normalized_workspace
            in names
        ):
            exact.append(
                candidate
            )

    if len(exact) == 1:
        return (
            exact[0],
            "EXACT_NAME",
            1.0,
            ""
        )

    if len(exact) > 1:
        return (
            None,
            "AMBIGUOUS",
            0.0,
            (
                "Múltiples YAML UC coinciden "
                "exactamente por nombre."
            )
        )

    # --------------------------------------------------------
    # 3. Puntaje combinado:
    #      - similitud de nombre
    #      - tokens funcionales
    #      - notebook target
    #
    # Notebook tiene mayor peso porque suele sobrevivir al
    # cambio de nombre del job.
    # --------------------------------------------------------

    scored = []

    for candidate in uc_jobs:
        logical_job = (
            candidate[
                "logical_job"
            ]
        )

        name_score = max(
            similarity_score(
                workspace_name,
                logical_job
            ),
            similarity_score(
                workspace_name,
                candidate[
                    "yaml_job_name"
                ]
            ),
        )

        tokens = max(
            token_score(
                workspace_name,
                logical_job
            ),
            token_score(
                workspace_name,
                candidate[
                    "yaml_job_name"
                ]
            ),
        )

        nb_score = (
            notebook_overlap_score(
                workspace_job[
                    "notebook_targets"
                ],
                candidate[
                    "notebook_targets"
                ]
            )
        )

        final_score = (
            0.35 * name_score
            + 0.20 * tokens
            + 0.45 * nb_score
        )

        scored.append({
            "candidate":
                candidate,

            "score":
                final_score,

            "name_score":
                name_score,

            "token_score":
                tokens,

            "notebook_score":
                nb_score,
        })

    scored.sort(
        key=lambda item:
            item[
                "score"
            ],
        reverse=True
    )

    if not scored:
        return (
            None,
            "NOT_FOUND",
            0.0,
            "No existen YAML UC candidatos."
        )

    best = scored[0]

    second_score = (
        scored[1]["score"]
        if len(scored) > 1
        else 0.0
    )

    # Aceptar si:
    #   - score >= 0.72
    #   - ventaja mínima 0.12
    #
    # o si el notebook coincide exactamente y el candidato es
    # único con esa evidencia.
    strong_notebook_match = (
        best[
            "notebook_score"
        ]
        >= 1.0
        and (
            best["score"]
            - second_score
        )
        >= 0.08
    )

    normal_match = (
        best[
            "score"
        ]
        >= 0.72
        and (
            best["score"]
            - second_score
        )
        >= 0.12
    )

    if (
        strong_notebook_match
        or normal_match
    ):
        method = (
            "NOTEBOOK_TARGET_MATCH"
            if best[
                "notebook_score"
            ] >= 1.0
            else "SIMILARITY_MATCH"
        )

        return (
            best[
                "candidate"
            ],
            method,
            round(
                best[
                    "score"
                ],
                4
            ),
            ""
        )

    # Si hay dos candidatos prácticamente empatados:
    if (
        best[
            "score"
        ] >= 0.55
        and abs(
            best["score"]
            - second_score
        ) < 0.12
    ):
        return (
            None,
            "AMBIGUOUS",
            round(
                best[
                    "score"
                ],
                4
            ),
            (
                "Los mejores candidatos UC "
                "tienen puntajes demasiado cercanos."
            )
        )

    return (
        None,
        "NOT_FOUND",
        round(
            best[
                "score"
            ],
            4
        ),
        (
            "No se encontró un homólogo UC "
            "con evidencia suficiente."
        )
    )


# ============================================================
# Inventario por ambiente
# ============================================================

def build_library_inventory(
    libraries
):
    result = defaultdict(
        list
    )

    for library in libraries:
        key = (
            library[
                "library_type"
            ],
            normalize(
                library[
                    "library_name"
                ]
            )
        )

        result[
            key
        ].append(
            library
        )

    return result


# ============================================================
# Clasificación - MISMA semántica Tool 1
# ============================================================

def classify_maven(
    library_name,
    pro_values,
    uc_values
):
    expected_version = (
        EXPECTED_MAVEN.get(
            library_name
        )
    )

    pro_versions = sorted({
        value[
            "version"
        ]
        for value in pro_values
        if value[
            "version"
        ]
    })

    uc_versions = sorted({
        value[
            "version"
        ]
        for value in uc_values
        if value[
            "version"
        ]
    })

    if expected_version:
        if not uc_values:
            return (
                "MISSING_IN_UC",
                expected_version,
                "YES",
                (
                    f"Agregar {library_name}:"
                    f"{expected_version} en UC."
                )
            )

        if (
            uc_versions
            == [
                expected_version
            ]
        ):
            if (
                pro_versions
                == uc_versions
            ):
                return (
                    "UNCHANGED",
                    expected_version,
                    "NO",
                    "Dependencia alineada."
                )

            return (
                "VERSION_ALIGNED_UC",
                expected_version,
                "NO",
                (
                    "Versión UC alineada "
                    "con baseline acordado."
                )
            )

        return (
            "UC_VERSION_MISMATCH",
            expected_version,
            "YES",
            (
                f"Homologar UC a "
                f"{library_name}:"
                f"{expected_version}."
            )
        )

    if (
        pro_values
        and not uc_values
    ):
        return (
            "REMOVED_IN_UC",
            "",
            "REVIEW",
            (
                "Dependencia existente en PRO "
                "y ausente en UC. Validar si "
                "la eliminación es intencional."
            )
        )

    if (
        not pro_values
        and uc_values
    ):
        return (
            "ADDED_IN_UC",
            "",
            "REVIEW",
            (
                "Dependencia agregada en UC. "
                "Validar necesidad."
            )
        )

    if (
        pro_versions
        == uc_versions
    ):
        return (
            "UNCHANGED",
            "",
            "NO",
            "Sin cambio."
        )

    return (
        "VERSION_CHANGED",
        "",
        "REVIEW",
        "Validar cambio de versión."
    )


def classify_jar(
    library_name,
    pro_values,
    uc_values,
    uc_job_libraries
):
    basename = (
        library_name
    )

    # customcfdi-gson
    if basename in OBSOLETE_JARS:
        if uc_values:
            return (
                "OBSOLETE_STILL_IN_UC",
                "",
                "YES",
                (
                    "Eliminar customcfdi-gson "
                    "del job UC."
                )
            )

        return (
            "OBSOLETE_REMOVED",
            "",
            "NO",
            (
                "Dependencia obsoleta "
                "eliminada correctamente."
            )
        )

    # parser 0.1
    if (
        basename
        == "parser_2.12-0.1.jar"
        or
        "parser-covol_2.12-0.1.jar"
        in basename
    ):
        return (
            "PARSER_PRO_VERSION",
            EXPECTED_PARSER,
            "NO",
            (
                "Versión histórica PRO. "
                "Debe sustituirse por "
                "parser_2.12-0.2.jar en UC."
            )
        )

    # parser 0.2
    if (
        basename
        == EXPECTED_PARSER
    ):
        if uc_values:
            return (
                "PARSER_UC_ALIGNED",
                EXPECTED_PARSER,
                "NO",
                "Parser UC correcto."
            )

        return (
            "PARSER_MISSING_IN_UC",
            EXPECTED_PARSER,
            "YES",
            (
                "Agregar parser_2.12-0.2.jar "
                "al job UC."
            )
        )

    # parserCV2V
    if (
        basename.lower()
        == "parsercv2v.jar"
    ):
        uc_parser_02_present = (
            (
                "JAR",
                EXPECTED_PARSER.lower()
            )
            in uc_job_libraries
        )

        if uc_parser_02_present:
            return (
                "PARSER_CV2V_CONSOLIDATED",
                EXPECTED_PARSER,
                "NO",
                (
                    "parserCV2V.jar utilizado en PRO queda "
                    "consolidado funcionalmente en "
                    "parser_2.12-0.2.jar para UC. "
                    "No se requiere conservar ambos JARs."
                )
            )

        return (
            "PARSER_CV2V_REPLACEMENT_MISSING",
            EXPECTED_PARSER,
            "YES",
            (
                "parserCV2V.jar existe en PRO, pero no se "
                "detectó parser_2.12-0.2.jar en el mismo job UC. "
                "Agregar o validar el parser requerido."
            )
        )

    if (
        pro_values
        and not uc_values
    ):
        return (
            "JAR_REMOVED_IN_UC",
            "",
            "REVIEW",
            (
                "JAR presente en PRO y "
                "ausente en UC."
            )
        )

    if (
        not pro_values
        and uc_values
    ):
        return (
            "JAR_ADDED_IN_UC",
            "",
            "REVIEW",
            "JAR nuevo en UC."
        )

    return (
        "JAR_PRESENT_BOTH",
        "",
        "NO",
        "JAR presente en ambos ambientes."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 72)
    print(
        "ASSESSMENT WORKSPACE - PASO 22 V3"
    )
    print(
        "LIBRERIAS/JARS WORKSPACE UAT-PRO -> UNITY CATALOG"
    )
    print("=" * 72)
    print()

    if not JOBS_DIR.exists():
        raise SystemExit(
            "ERROR: no existe snapshot/jobs."
        )

    if not CONFIG_JOBS_DIR.exists():
        raise SystemExit(
            "ERROR: no existe input/config/jobs."
        )

    # --------------------------------------------------------
    # Workspace jobs
    # --------------------------------------------------------

    job_id_by_file = (
        load_job_id_map()
    )

    workspace_jobs = []
    workspace_errors = []

    for path in sorted(
        JOBS_DIR.glob(
            "*.json"
        ),
        key=lambda p:
            p.name.casefold()
    ):
        try:
            workspace_jobs.append(
                extract_workspace_job(
                    path,
                    job_id_by_file
                )
            )

        except Exception as exc:
            workspace_errors.append(
                (
                    str(path),
                    (
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )
                )
            )

    # --------------------------------------------------------
    # UC YAMLs
    # --------------------------------------------------------

    uc_jobs = []
    uc_errors = []

    uc_yaml_files = sorted(
        [
            path
            for pattern in (
                "UC_*.yml",
                "UC_*.yaml",
            )
            for path in CONFIG_JOBS_DIR.glob(
                pattern
            )
        ],
        key=lambda p:
            p.name.casefold()
    )

    for path in uc_yaml_files:
        job_data, error = (
            extract_uc_job_from_yaml(
                path
            )
        )

        if error:
            uc_errors.append(
                (
                    str(path),
                    error
                )
            )

        elif job_data:
            uc_jobs.append(
                job_data
            )

    # --------------------------------------------------------
    # Matching
    # --------------------------------------------------------

    matching_rows = []
    matches = {}

    for workspace_job in sorted(
        workspace_jobs,
        key=lambda item:
            item[
                "job"
            ].casefold()
    ):
        (
            matched,
            method,
            confidence,
            notes
        ) = match_workspace_job(
            workspace_job,
            uc_jobs
        )

        workspace_name = (
            workspace_job[
                "job"
            ]
        )

        if matched:
            matches[
                workspace_name
            ] = matched

        matching_rows.append({
            "workspace_job":
                workspace_name,

            "workspace_job_id":
                workspace_job[
                    "job_id"
                ],

            "matched_uc_job":
                (
                    matched[
                        "logical_job"
                    ]
                    if matched
                    else ""
                ),

            "uc_yaml_job_name":
                (
                    matched[
                        "yaml_job_name"
                    ]
                    if matched
                    else ""
                ),

            "match_method":
                method,

            "match_confidence":
                confidence,

            "workspace_notebooks":
                unique_join(
                    workspace_job[
                        "notebook_targets"
                    ]
                ),

            "uc_notebooks":
                (
                    unique_join(
                        matched[
                            "notebook_targets"
                        ]
                    )
                    if matched
                    else ""
                ),

            "source_workspace_json":
                workspace_job[
                    "source_file"
                ],

            "uc_yaml":
                (
                    matched[
                        "source_file"
                    ]
                    if matched
                    else ""
                ),

            "notes":
                notes,
        })

    # --------------------------------------------------------
    # Guardar matching primero
    # --------------------------------------------------------

    MATCH_OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    matching_fields = [
        "workspace_job",
        "workspace_job_id",
        "matched_uc_job",
        "uc_yaml_job_name",
        "match_method",
        "match_confidence",
        "workspace_notebooks",
        "uc_notebooks",
        "source_workspace_json",
        "uc_yaml",
        "notes",
    ]

    with MATCH_OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=matching_fields
        )

        writer.writeheader()
        writer.writerows(
            matching_rows
        )

    # --------------------------------------------------------
    # Comparativa de libraries
    #
    # El job de salida será el nombre lógico UC, igual que en
    # Tool 1. Si no existe match, usamos Workspace y generamos
    # una fila JOB_MATCH_NOT_FOUND para no ocultarlo.
    # --------------------------------------------------------

    output_rows = []

    for workspace_job in workspace_jobs:
        workspace_name = (
            workspace_job[
                "job"
            ]
        )

        matched = matches.get(
            workspace_name
        )

        pro_inventory = (
            build_library_inventory(
                workspace_job[
                    "libraries"
                ]
            )
        )

        if not matched:
            output_rows.append({
                "job":
                    workspace_name,

                "library_type":
                    "JOB_MATCH",

                "library_name":
                    "",

                "pro_value":
                    "",

                "uc_value":
                    "",

                "expected_uc_value":
                    "",

                "migration_status":
                    "JOB_MATCH_NOT_FOUND",

                "requires_action":
                    "REVIEW",

                "recommended_action":
                    (
                        "No se encontró homólogo UC "
                        "para el job del Workspace."
                    ),

                # Se conserva el nombre del contrato Tool 1.
                # En Tool 2 la fuente productiva es JSON Workspace.
                "pro_yaml":
                    workspace_job[
                        "source_file"
                    ],

                "uc_yaml":
                    "",
            })

            continue

        uc_inventory = (
            build_library_inventory(
                matched[
                    "libraries"
                ]
            )
        )

        logical_job = (
            matched[
                "logical_job"
            ]
        )

        keys = (
            set(
                pro_inventory.keys()
            )
            |
            set(
                uc_inventory.keys()
            )
        )

        # Validación explícita parser 0.2 si PRO usa 0.1.
        pro_jar_names = {
            key[1]
            for key
            in pro_inventory.keys()
            if key[0] == "JAR"
        }

        if (
            "parser_2.12-0.1.jar"
            in pro_jar_names
            or
            "parser-covol_2.12-0.1.jar"
            in pro_jar_names
        ):
            keys.add(
                (
                    "JAR",
                    EXPECTED_PARSER.lower()
                )
            )

        for (
            library_type,
            library_name_normalized
        ) in sorted(
            keys
        ):
            pro_values = (
                pro_inventory.get(
                    (
                        library_type,
                        library_name_normalized
                    ),
                    []
                )
            )

            uc_values = (
                uc_inventory.get(
                    (
                        library_type,
                        library_name_normalized
                    ),
                    []
                )
            )

            if pro_values:
                display_name = (
                    pro_values[0][
                        "library_name"
                    ]
                )

            elif uc_values:
                display_name = (
                    uc_values[0][
                        "library_name"
                    ]
                )

            else:
                display_name = (
                    library_name_normalized
                )

            if (
                library_type
                == "MAVEN"
            ):
                (
                    migration_status,
                    expected_value,
                    requires_action,
                    recommended_action
                ) = classify_maven(
                    display_name,
                    pro_values,
                    uc_values
                )

            elif (
                library_type
                == "JAR"
            ):
                (
                    migration_status,
                    expected_value,
                    requires_action,
                    recommended_action
                ) = classify_jar(
                    display_name,
                    pro_values,
                    uc_values,
                    uc_inventory
                )

            else:
                if (
                    pro_values
                    and uc_values
                ):
                    migration_status = (
                        "PRESENT_BOTH"
                    )
                    requires_action = "NO"
                    recommended_action = (
                        "Sin cambio."
                    )

                elif pro_values:
                    migration_status = (
                        "REMOVED_IN_UC"
                    )
                    requires_action = (
                        "REVIEW"
                    )
                    recommended_action = (
                        "Validar eliminación."
                    )

                else:
                    migration_status = (
                        "ADDED_IN_UC"
                    )
                    requires_action = (
                        "REVIEW"
                    )
                    recommended_action = (
                        "Validar incorporación."
                    )

                expected_value = ""

            output_rows.append({
                "job":
                    logical_job,

                "library_type":
                    library_type,

                "library_name":
                    display_name,

                "pro_value":
                    " | ".join(
                        sorted({
                            item[
                                "value"
                            ]
                            for item
                            in pro_values
                        })
                    ),

                "uc_value":
                    " | ".join(
                        sorted({
                            item[
                                "value"
                            ]
                            for item
                            in uc_values
                        })
                    ),

                "expected_uc_value":
                    expected_value,

                "migration_status":
                    migration_status,

                "requires_action":
                    requires_action,

                "recommended_action":
                    recommended_action,

                "pro_yaml":
                    workspace_job[
                        "source_file"
                    ],

                "uc_yaml":
                    matched[
                        "source_file"
                    ],
            })

    # --------------------------------------------------------
    # Orden - Tool 1 + estado de matching
    # --------------------------------------------------------

    STATUS_ORDER = {
        "JOB_MATCH_NOT_FOUND":
            0,

        "OBSOLETE_STILL_IN_UC":
            1,

        "PARSER_MISSING_IN_UC":
            2,

        "MISSING_IN_UC":
            3,

        "UC_VERSION_MISMATCH":
            4,

        "ADDED_IN_UC":
            5,

        "REMOVED_IN_UC":
            6,

        "JAR_ADDED_IN_UC":
            7,

        "PARSER_CV2V_REPLACEMENT_MISSING":
            8,

        "JAR_REMOVED_IN_UC":
            9,

        "VERSION_CHANGED":
            10,

        "PARSER_CV2V_CONSOLIDATED":
            11,

        "VERSION_ALIGNED_UC":
            12,

        "PARSER_UC_ALIGNED":
            13,

        "OBSOLETE_REMOVED":
            14,

        "UNCHANGED":
            15,

        "PARSER_PRO_VERSION":
            16,

        "JAR_PRESENT_BOTH":
            17,

        "PRESENT_BOTH":
            18,
    }

    output_rows.sort(
        key=lambda row: (
            STATUS_ORDER.get(
                row[
                    "migration_status"
                ],
                99
            ),
            normalize(
                row[
                    "job"
                ]
            ),
            normalize(
                row[
                    "library_name"
                ]
            )
        )
    )

    # --------------------------------------------------------
    # CSV principal: MISMO contrato Tool 1
    # --------------------------------------------------------

    fieldnames = [
        "job",
        "library_type",
        "library_name",
        "pro_value",
        "uc_value",
        "expected_uc_value",
        "migration_status",
        "requires_action",
        "recommended_action",
        "pro_yaml",
        "uc_yaml",
    ]

    with OUTPUT_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(
            output_rows
        )

    # --------------------------------------------------------
    # Resumen
    # --------------------------------------------------------

    match_counter = Counter(
        row[
            "match_method"
        ]
        for row
        in matching_rows
    )

    status_counter = Counter(
        row[
            "migration_status"
        ]
        for row
        in output_rows
    )

    action_counter = Counter(
        row[
            "requires_action"
        ]
        for row
        in output_rows
    )

    jobs_with_actions = {
        row[
            "job"
        ]
        for row
        in output_rows
        if row[
            "requires_action"
        ]
        in {
            "YES",
            "REVIEW",
        }
    }

    print(
        f"Jobs Workspace analizados        : "
        f"{len(workspace_jobs)}"
    )

    print(
        f"YAML UC disponibles              : "
        f"{len(uc_jobs)}"
    )

    print()

    alias_hits = sum(
        1
        for row in matching_rows
        if row["match_method"] == "KNOWN_ALIAS"
    )

    print(
        f"Aliases UAT reconocidos          : "
        f"{alias_hits}"
    )

    print()

    print(
        "Matching Workspace -> UC:"
    )

    for method in sorted(
        match_counter
    ):
        print(
            f" - {method:<30}: "
            f"{match_counter[method]}"
        )

    print()

    print(
        f"Relaciones job -> librería       : "
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
                99
            ),
            value
        )
    ):
        print(
            f" - {status:<36}: "
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
            f" - {action:<36}: "
            f"{action_counter[action]}"
        )

    print()

    print(
        f"Jobs con alguna revisión/acción  : "
        f"{len(jobs_with_actions)}"
    )

    if jobs_with_actions:
        print()
        print(
            "Jobs con pendientes:"
        )

        for job in sorted(
            jobs_with_actions,
            key=str.casefold
        ):
            print(
                f" - {job}"
            )

    if workspace_errors:
        print()
        print(
            "Errores leyendo Workspace jobs:"
        )

        for path, error in workspace_errors:
            print(
                f" - {path}: {error}"
            )

    if uc_errors:
        print()
        print(
            "Errores leyendo YAML UC:"
        )

        for path, error in uc_errors:
            print(
                f" - {path}: {error}"
            )

    print()

    print(
        f"Matching generado: "
        f"{MATCH_OUTPUT_FILE}"
    )

    print(
        f"Comparativa generada: "
        f"{OUTPUT_FILE}"
    )

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
