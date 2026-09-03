#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import re
import yaml


# ============================================================
# ASSESSMENT WORKSPACE - PASO 24
# PARAMETROS Y VARIABLES DE EJECUCION
#
# PRO:
#   - snapshot real del Workspace
#   - snapshot/jobs/*.json
#   - notebooks reales del snapshot
#
# UC:
#   - input/config/jobs/UC_*.yml
#
# Base metodológica:
#   Tool 1 / antiguo Paso 23 - Parámetros y Variables.
#
# Se inventarían:
#   - variables de ambiente consumidas por notebooks;
#   - dbutils.widgets definidos;
#   - dbutils.widgets consumidos.
#
# Criterios:
#   - comentarios inactivos NO cuentan;
#   - líneas MAGIC activas sí cuentan;
#   - widgets son parámetros internos del flujo y no implican
#     por sí mismos un cambio de infraestructura;
#   - variables de ambiente se comparan contra la configuración
#     real PRO y la definición objetivo UC;
#   - no se generan acciones nuevas si el cambio ya corresponde
#     a la homologación UC conocida.
# ============================================================


JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
NOTEBOOK_INVENTORY_FILE = Path("output/notebooks.csv")
MATCHING_FILE = Path("output/job_name_matching.csv")
SNAPSHOT_JOBS_DIR = Path("snapshot/jobs")
UC_JOBS_DIR = Path("input/config/jobs")
OUTPUT_FILE = Path("output/job_runtime_parameters_analysis.csv")


def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def unique_join(values):
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
    return " | ".join(result)


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def normalize_yaml_text(text):
    text = text.replace("\t", "  ")
    stripped = text.strip()
    if stripped.startswith('"resources:') and stripped.endswith('"'):
        text = stripped[1:-1]
    text = re.sub(
        r':\s*""([^"\r\n]*)""\s*$',
        lambda m: ': "' + m.group(1) + '"',
        text,
        flags=re.MULTILINE,
    )
    return text


def load_yaml(path):
    try:
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
            parse_mode = "DIRECT"
        except Exception:
            data = yaml.safe_load(normalize_yaml_text(text))
            parse_mode = "NORMALIZED"
        return data, parse_mode, ""
    except Exception as e:
        return None, "ERROR", f"{type(e).__name__}: {e}"


def get_code_blocks(notebook_path):
    if notebook_path.suffix.lower() == ".ipynb":
        with notebook_path.open("r", encoding="utf-8") as f:
            notebook = json.load(f)
        blocks = []
        for cell in notebook.get("cells", []):
            if cell.get("cell_type") != "code":
                continue
            source = cell.get("source", [])
            blocks.append("".join(source) if isinstance(source, list) else str(source or ""))
        return blocks

    content = notebook_path.read_text(encoding="utf-8", errors="ignore")
    return re.split(r'(?:\/\/|#|--)\s*COMMAND\s*-+', content)


def normalize_magic_lines(code):
    result = []
    for line in code.splitlines():
        match = re.match(
            r'^(\s*)(?://|#|--)\s*MAGIC\s?(.*)$',
            line,
            flags=re.IGNORECASE,
        )
        result.append(match.group(1) + match.group(2) if match else line)
    return "\n".join(result)


def remove_comments(code):
    code = normalize_magic_lines(code)
    result = []
    i = 0
    in_single = False
    in_double = False
    in_block = False

    while i < len(code):
        if in_block:
            if code[i:i + 2] == "*/":
                in_block = False
                i += 2
            else:
                i += 1
            continue

        ch = code[i]

        if ch == '"' and not in_single:
            if i == 0 or code[i - 1] != "\\":
                in_double = not in_double
            result.append(ch)
            i += 1
            continue

        if ch == "'" and not in_double:
            if i == 0 or code[i - 1] != "\\":
                in_single = not in_single
            result.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            if code[i:i + 2] == "/*":
                in_block = True
                i += 2
                continue
            if code[i:i + 2] in {"//", "--"}:
                while i < len(code) and code[i] != "\n":
                    i += 1
                continue
            if ch == "#":
                while i < len(code) and code[i] != "\n":
                    i += 1
                continue

        result.append(ch)
        i += 1

    return "".join(result)


def build_notebook_index(rows):
    index = {}
    for row in rows:
        workspace_path = clean(row.get("workspace_path") or row.get("path"))
        local_file = clean(row.get("local_file"))
        if not workspace_path or not local_file:
            continue
        path = Path(local_file)
        if not path.is_absolute():
            path = Path(".") / path
        index[normalize(workspace_path)] = path
    return index


def load_exact_matches():
    if not MATCHING_FILE.exists():
        raise SystemExit(
            "Falta output/job_name_matching.csv. Ejecuta primero el Paso 22."
        )

    rows = read_csv(MATCHING_FILE)
    matches = {}

    for row in rows:
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
            matches[pro_name] = uc_name

    return matches


def collect_job_runtime_config(job_data):
    env_vars = defaultdict(list)
    base_parameters = defaultdict(list)

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                key_norm = clean(key).casefold()

                if key_norm == "spark_env_vars" and isinstance(child, dict):
                    for env_name, env_value in child.items():
                        env_vars[clean(env_name)].append(clean(env_value))

                if key_norm == "base_parameters" and isinstance(child, dict):
                    for parameter, parameter_value in child.items():
                        base_parameters[clean(parameter)].append(clean(parameter_value))

                walk(child)

        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(job_data)

    return {
        "env_vars": {
            key: unique_join(values)
            for key, values in env_vars.items()
        },
        "base_parameters": {
            key: unique_join(values)
            for key, values in base_parameters.items()
        },
    }


def extract_uc_job_from_yaml(data):
    if not isinstance(data, dict):
        return "", {}

    resources = data.get("resources", {})
    jobs = resources.get("jobs", {})

    if not isinstance(jobs, dict):
        return "", {}

    for _, job_data in jobs.items():
        if not isinstance(job_data, dict):
            continue
        return clean(job_data.get("name")), job_data

    return "", {}


def load_pro_jobs():
    result = {}

    for path in sorted(SNAPSHOT_JOBS_DIR.glob("*.json")):
        try:
            data = load_json(path)
        except Exception as e:
            print(f"ADVERTENCIA leyendo {path}: {e}")
            continue

        if not isinstance(data, dict):
            continue

        job_name = clean(data.get("name"))

        if not job_name:
            continue

        result[job_name] = {
            "source": str(path),
            "config": collect_job_runtime_config(data),
        }

    return result


def load_uc_jobs():
    result = {}
    yaml_files = []

    for pattern in ["UC_*.yml", "UC_*.yaml"]:
        yaml_files.extend(UC_JOBS_DIR.glob(pattern))

    for path in sorted(yaml_files):
        data, parse_mode, error = load_yaml(path)

        if data is None:
            print(f"ADVERTENCIA leyendo {path}: {error}")
            continue

        job_name, job_data = extract_uc_job_from_yaml(data)

        if not job_name:
            continue

        result[job_name] = {
            "source": str(path),
            "parse_mode": parse_mode,
            "config": collect_job_runtime_config(job_data),
        }

    return result


WIDGET_DEFINE_RE = re.compile(
    r'''
    dbutils
    \s*\.\s*
    widgets
    \s*\.\s*
    (?P<method>text|dropdown|combobox|multiselect)
    \s*\(
    \s*
    (?P<quote>["'])
    (?P<name>.*?)
    (?P=quote)
    ''',
    flags=re.IGNORECASE | re.VERBOSE,
)

WIDGET_GET_RE = re.compile(
    r'''
    dbutils
    \s*\.\s*
    widgets
    \s*\.\s*
    (?P<method>get|getArgument)
    \s*\(
    \s*
    (?P<quote>["'])
    (?P<name>.*?)
    (?P=quote)
    ''',
    flags=re.IGNORECASE | re.VERBOSE,
)

ENV_PATTERNS = [
    re.compile(
        r'''\bsys\s*\.\s*env\s*\(\s*["'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["']\s*\)''',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'''\bsys\s*\.\s*env\s*\.\s*get\s*\(\s*["'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["']''',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'''\bos\s*\.\s*getenv\s*\(\s*["'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["']''',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'''\bos\s*\.\s*environ\s*\[\s*["'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["']\s*\]''',
        flags=re.IGNORECASE,
    ),
    re.compile(
        r'''\bSystem\s*\.\s*getenv\s*\(\s*["'](?P<name>[A-Za-z_][A-Za-z0-9_]*)["']''',
        flags=re.IGNORECASE,
    ),
]


def find_environment_variables(code):
    values = []

    for pattern in ENV_PATTERNS:
        for match in pattern.finditer(code):
            values.append(
                (
                    clean(match.group("name")),
                    match.group(0),
                )
            )

    return values


def dict_value_ci(mapping, key):
    key_norm = clean(key).casefold()

    for real_key, value in mapping.items():
        if clean(real_key).casefold() == key_norm:
            return value

    return ""


def classify_env_var(variable, pro_value, uc_value):
    variable_norm = clean(variable).upper()

    if variable_norm == "CV_EXPLOTACION_CONFIG_FILE_PATH":
        if uc_value and "/volumes/" in normalize(uc_value):
            return (
                "EXPECTED_UC_CHANGE",
                "NO",
                (
                    "La variable externaliza la ubicación del archivo de configuración. "
                    "En UC se espera una ruta gobernada /Volumes/... en lugar de "
                    "mounts/DBFS legacy."
                ),
            )

        return (
            "REVIEW_REQUIRED",
            "REVIEW",
            (
                "CV_EXPLOTACION_CONFIG_FILE_PATH se consume en el notebook, "
                "pero no se confirmó una ruta /Volumes/... en la definición UC."
            ),
        )

    if pro_value and uc_value and clean(pro_value) == clean(uc_value):
        return (
            "UNCHANGED",
            "NO",
            "Variable de ambiente conservada sin cambio.",
        )

    if not pro_value and uc_value:
        return (
            "UC_ENVIRONMENT_CONFIGURATION",
            "NO",
            (
                "Variable definida explícitamente en UC. Se conserva como "
                "evidencia de configuración del entorno objetivo."
            ),
        )

    if pro_value and not uc_value:
        return (
            "REVIEW_REQUIRED",
            "REVIEW",
            (
                "Variable consumida/configurada en PRO y sin valor equivalente "
                "detectado en el YAML UC."
            ),
        )

    return (
        "ENVIRONMENT_REFERENCE",
        "NO",
        (
            "Referencia a variable de ambiente detectada en el notebook; "
            "no se identificó un cambio de infraestructura accionable en este paso."
        ),
    )


def classify_widget(parameter, pro_base_value, uc_base_value):
    if pro_base_value or uc_base_value:
        note = (
            "Parámetro interno del flujo de notebooks. Existe además como "
            "base_parameters del job; se conserva la comparativa de valores "
            "como evidencia."
        )
    else:
        note = (
            "Parámetro interno del flujo de notebooks. Su presencia no implica "
            "por sí misma un cambio de infraestructura UC."
        )

    return (
        "INTERNAL_NOTEBOOK_PARAMETER",
        "NO",
        note,
    )


def main():
    required = [
        JOB_INVENTORY_FILE,
        NOTEBOOK_INVENTORY_FILE,
        MATCHING_FILE,
    ]

    missing = [
        str(path)
        for path in required
        if not path.exists()
    ]

    if missing:
        print("ERROR: faltan archivos requeridos:")
        for path in missing:
            print(f" - {path}")
        raise SystemExit(1)

    job_rows = read_csv(JOB_INVENTORY_FILE)
    notebook_rows = read_csv(NOTEBOOK_INVENTORY_FILE)

    exact_matches = load_exact_matches()
    pro_jobs = load_pro_jobs()
    uc_jobs = load_uc_jobs()

    notebook_index = build_notebook_index(notebook_rows)

    notebook_jobs = defaultdict(set)

    for row in job_rows:
        job = clean(row.get("job"))
        notebook = clean(row.get("notebook"))

        if not job or not notebook:
            continue

        if job not in exact_matches:
            continue

        notebook_jobs[notebook].add(job)

    output_rows = []
    dedupe = set()
    missing_notebooks = []

    for notebook, jobs in sorted(
        notebook_jobs.items(),
        key=lambda item: normalize(item[0]),
    ):
        notebook_path = notebook_index.get(normalize(notebook))

        if notebook_path is None or not notebook_path.exists():
            missing_notebooks.append(notebook)
            continue

        try:
            blocks = get_code_blocks(notebook_path)
        except Exception as e:
            print(f"ADVERTENCIA leyendo {notebook}: {e}")
            missing_notebooks.append(notebook)
            continue

        for cell_index, block in enumerate(blocks, start=1):
            code = remove_comments(block)

            for variable, source in find_environment_variables(code):
                for job in sorted(jobs, key=str.casefold):
                    uc_job = exact_matches[job]

                    pro_cfg = pro_jobs.get(job, {}).get("config", {})
                    uc_cfg = uc_jobs.get(uc_job, {}).get("config", {})

                    pro_env = pro_cfg.get("env_vars", {})
                    uc_env = uc_cfg.get("env_vars", {})

                    pro_value = dict_value_ci(pro_env, variable)
                    uc_value = dict_value_ci(uc_env, variable)

                    status, action, notes = classify_env_var(
                        variable,
                        pro_value,
                        uc_value,
                    )

                    key = (
                        normalize(job),
                        normalize(notebook),
                        "ENV_VAR",
                        variable.casefold(),
                    )

                    if key in dedupe:
                        continue

                    dedupe.add(key)

                    output_rows.append({
                        "job": job,
                        "uc_job": uc_job,
                        "notebook": notebook,
                        "local_file": str(notebook_path),
                        "cell": cell_index,
                        "type": "ENV_VAR",
                        "parameter": variable,
                        "source_pro": "spark_env_vars" if pro_value else "",
                        "value_pro": pro_value,
                        "source_uc": "spark_env_vars" if uc_value else "",
                        "value_uc": uc_value,
                        "migration_status": status,
                        "requires_action": action,
                        "notes": notes,
                        "code_reference": source.replace("\n", " ").strip(),
                    })

            for match in WIDGET_GET_RE.finditer(code):
                parameter = clean(match.group("name"))

                if not parameter:
                    continue

                for job in sorted(jobs, key=str.casefold):
                    uc_job = exact_matches[job]

                    pro_cfg = pro_jobs.get(job, {}).get("config", {})
                    uc_cfg = uc_jobs.get(uc_job, {}).get("config", {})

                    pro_base = dict_value_ci(
                        pro_cfg.get("base_parameters", {}),
                        parameter,
                    )

                    uc_base = dict_value_ci(
                        uc_cfg.get("base_parameters", {}),
                        parameter,
                    )

                    status, action, notes = classify_widget(
                        parameter,
                        pro_base,
                        uc_base,
                    )

                    key = (
                        normalize(job),
                        normalize(notebook),
                        "WIDGET_GET",
                        parameter.casefold(),
                    )

                    if key in dedupe:
                        continue

                    dedupe.add(key)

                    output_rows.append({
                        "job": job,
                        "uc_job": uc_job,
                        "notebook": notebook,
                        "local_file": str(notebook_path),
                        "cell": cell_index,
                        "type": "WIDGET_GET",
                        "parameter": parameter,
                        "source_pro": "base_parameters" if pro_base else "",
                        "value_pro": pro_base,
                        "source_uc": "base_parameters" if uc_base else "",
                        "value_uc": uc_base,
                        "migration_status": status,
                        "requires_action": action,
                        "notes": notes,
                        "code_reference": match.group(0).replace("\n", " ").strip(),
                    })

            for match in WIDGET_DEFINE_RE.finditer(code):
                parameter = clean(match.group("name"))

                if not parameter:
                    continue

                for job in sorted(jobs, key=str.casefold):
                    uc_job = exact_matches[job]

                    pro_cfg = pro_jobs.get(job, {}).get("config", {})
                    uc_cfg = uc_jobs.get(uc_job, {}).get("config", {})

                    pro_base = dict_value_ci(
                        pro_cfg.get("base_parameters", {}),
                        parameter,
                    )

                    uc_base = dict_value_ci(
                        uc_cfg.get("base_parameters", {}),
                        parameter,
                    )

                    status, action, notes = classify_widget(
                        parameter,
                        pro_base,
                        uc_base,
                    )

                    key = (
                        normalize(job),
                        normalize(notebook),
                        "WIDGET_DEFINE",
                        parameter.casefold(),
                    )

                    if key in dedupe:
                        continue

                    dedupe.add(key)

                    output_rows.append({
                        "job": job,
                        "uc_job": uc_job,
                        "notebook": notebook,
                        "local_file": str(notebook_path),
                        "cell": cell_index,
                        "type": "WIDGET_DEFINE",
                        "parameter": parameter,
                        "source_pro": "base_parameters" if pro_base else "",
                        "value_pro": pro_base,
                        "source_uc": "base_parameters" if uc_base else "",
                        "value_uc": uc_base,
                        "migration_status": status,
                        "requires_action": action,
                        "notes": notes,
                        "code_reference": match.group(0).replace("\n", " ").strip(),
                    })

    TYPE_ORDER = {
        "ENV_VAR": 1,
        "WIDGET_GET": 2,
        "WIDGET_DEFINE": 3,
    }

    output_rows.sort(
        key=lambda row: (
            normalize(row["job"]),
            normalize(row["notebook"]),
            TYPE_ORDER.get(row["type"], 99),
            normalize(row["parameter"]),
        )
    )

    fieldnames = [
        "job",
        "uc_job",
        "notebook",
        "local_file",
        "cell",
        "type",
        "parameter",
        "source_pro",
        "value_pro",
        "source_uc",
        "value_uc",
        "migration_status",
        "requires_action",
        "notes",
        "code_reference",
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

    type_counter = Counter(
        row["type"]
        for row in output_rows
    )

    status_counter = Counter(
        row["migration_status"]
        for row in output_rows
    )

    action_counter = Counter(
        row["requires_action"]
        for row in output_rows
    )

    jobs_with_relations = {
        row["job"]
        for row in output_rows
    }

    jobs_with_actions = {
        row["job"]
        for row in output_rows
        if row["requires_action"] in {"YES", "REVIEW"}
    }

    unique_notebooks = {
        normalize(notebook)
        for notebook in notebook_jobs
    }

    print("=" * 72)
    print("ASSESSMENT WORKSPACE - PASO 24")
    print("PARAMETROS Y VARIABLES DE EJECUCION")
    print("=" * 72)
    print()
    print(f"Jobs en alcance                 : {len(exact_matches)}")
    print(f"Notebooks en alcance            : {len(unique_notebooks)}")
    print(f"Notebooks faltantes             : {len(set(missing_notebooks))}")
    print(f"Jobs con relaciones detectadas : {len(jobs_with_relations)}")
    print(f"Relaciones detectadas           : {len(output_rows)}")
    print()

    print("Resumen por tipo:")
    for item in ["ENV_VAR", "WIDGET_DEFINE", "WIDGET_GET"]:
        print(f" - {item:<28}: {type_counter.get(item, 0)}")

    print()
    print("Resumen por estado:")
    for status in sorted(status_counter):
        print(f" - {status:<28}: {status_counter[status]}")

    print()
    print("Resumen de acciones:")
    for action in sorted(action_counter):
        print(f" - {action:<28}: {action_counter[action]}")

    print()
    print(f"Jobs con revisión pendiente    : {len(jobs_with_actions)}")

    if jobs_with_actions:
        print()
        print("Jobs con pendientes:")
        for job in sorted(jobs_with_actions, key=str.casefold):
            print(f" - {job}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE}")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
