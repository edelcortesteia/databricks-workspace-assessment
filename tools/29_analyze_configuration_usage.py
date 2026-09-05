#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict, Counter
import csv
import json
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
INPUT_DIR = PROJECT_ROOT / "input"

NOTEBOOKS_FILE = OUTPUT_DIR / "notebooks.csv"
JOB_NOTEBOOK_FILE = OUTPUT_DIR / "job_notebook_inventory.csv"

PRO_CONFIG_CANDIDATES = [
    INPUT_DIR / "config" / "0.0_Configuration_PROD.json",
    INPUT_DIR / "config" / "0.0_Configuration_PROD(1).json",
    INPUT_DIR / "0.0_Configuration_PROD.json",
    INPUT_DIR / "0.0_Configuration_PROD(1).json",
]
UC_CONFIG_CANDIDATES = [
    INPUT_DIR / "config" / "0.0_Configuration_UC.json",
    INPUT_DIR / "0.0_Configuration_UC.json",
]

OUT_INVENTORY = OUTPUT_DIR / "configuration_key_inventory.csv"
OUT_USAGE = OUTPUT_DIR / "configuration_usage_analysis.csv"
OUT_COMPARE = OUTPUT_DIR / "configuration_pro_uc_comparison.csv"
OUT_ACTIONS = OUTPUT_DIR / "configuration_migration_actions.csv"

NEW_UC_SECTIONS = {
    "TablasDbks_UC",
    "TablasPostgresSQL_UC",
    "SPsPostgresSQL_UC",
    "CedulasJobsCluster",
}

SENSITIVE_TOKENS = {
    "password", "pwd", "secret", "token", "sas", "clientsecret",
    "connectionstring",
}

def clean(v):
    return str(v or "").strip()

def norm(v):
    return clean(v).casefold()

def uniq(values):
    out, seen = [], set()
    for value in values:
        value = clean(value)
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out

def join(values):
    return " | ".join(uniq(values))

def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def load_json(path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)

def first_existing(candidates, label):
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"No se encontró {label}. Rutas revisadas:\n"
        + "\n".join(f" - {p}" for p in candidates)
    )

def flatten_json(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out.update(flatten_json(child, path))
    elif isinstance(value, list):
        if all(not isinstance(x, (dict, list)) for x in value):
            out[prefix] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        else:
            for i, child in enumerate(value):
                out.update(flatten_json(child, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out

def is_sensitive_path(path):
    compact = re.sub(r"[^a-z0-9]", "", norm(path))
    return any(token in compact for token in SENSITIVE_TOKENS)

def safe_value(path, value):
    if value is None:
        return ""
    if is_sensitive_path(path):
        return "[REDACTED]"
    text = str(value)
    return text if len(text) <= 500 else text[:497] + "..."

def project_path(relative):
    return PROJECT_ROOT / Path(clean(relative).replace("\\", "/"))

def strip_comments(code):
    # Suficiente para este inventario: elimina comentarios de bloque y líneas.
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    lines = []
    for line in code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("--"):
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines)

def build_functional_code():
    notebooks = load_csv(NOTEBOOKS_FILE)
    job_rows = load_csv(JOB_NOTEBOOK_FILE)

    local_by_workspace = {}
    for row in notebooks:
        workspace = clean(row.get("workspace_path") or row.get("path"))
        local = clean(row.get("local_file") or row.get("path"))
        if workspace and local:
            local_by_workspace[workspace] = local

    jobs_by_notebook = defaultdict(list)
    for row in job_rows:
        nb = clean(row.get("notebook"))
        job = clean(row.get("job"))
        if nb and job:
            jobs_by_notebook[nb].append(job)

    code_by_notebook = {}
    missing = []

    for nb in jobs_by_notebook:
        local = local_by_workspace.get(nb)
        if not local:
            missing.append((nb, "NO_LOCAL_MAPPING"))
            continue

        path = project_path(local)
        if not path.exists():
            missing.append((nb, str(path)))
            continue

        code_by_notebook[nb] = strip_comments(
            path.read_text(encoding="utf-8", errors="ignore")
        )

    return code_by_notebook, {k: uniq(v) for k, v in jobs_by_notebook.items()}, missing

def path_regex(config_path):
    parts = config_path.split(".")
    expr = r"\s*\.\s*".join(re.escape(p) for p in parts)
    return re.compile(
        rf"\bparsedConfiguration\s*\.\s*{expr}\b",
        re.IGNORECASE,
    )

def section_leaf_regex(config_path):
    parts = config_path.split(".")
    section = parts[0]
    leaf = parts[-1]
    return re.compile(
        rf"\b{re.escape(section)}\s*\.\s*{re.escape(leaf)}\b",
        re.IGNORECASE,
    )

def leaf_regex(config_path):
    leaf = config_path.split(".")[-1]
    return re.compile(rf"\b{re.escape(leaf)}\b", re.IGNORECASE)

def literal_occurrences(code, value):
    text = clean(value)
    if not text or len(text) < 5:
        return 0
    return len(re.findall(re.escape(text), code, flags=re.IGNORECASE))


def find_section_aliases(code, section):
    """
    Detecta patrones tipo:
      val cfg = parsedConfiguration.CedulasJobsCluster
      var cfg = parsedConfiguration.TablasDbks_UC
    y devuelve los alias encontrados.
    """
    pattern = re.compile(
        rf"\b(?:val|var)\s+([A-Za-z_]\w*)"
        rf"(?:\s*:\s*[^=\n]+)?\s*=\s*"
        rf"parsedConfiguration\s*\.\s*{re.escape(section)}\b",
        re.IGNORECASE,
    )
    return uniq(pattern.findall(code))


def alias_leaf_occurrences(code, aliases, leaf):
    total = 0
    for alias in aliases:
        pattern = re.compile(
            rf"\b{re.escape(alias)}\s*\.\s*{re.escape(leaf)}\b",
            re.IGNORECASE,
        )
        total += len(pattern.findall(code))
    return total


def legacy_uc_table_candidate(config_path, uc_value):
    """
    Para TablasDbks_UC:
      u_impin_convol.cv_cfdi_x.tabla
      -> cfdi_x.tabla
    """
    if not config_path.startswith("TablasDbks_UC."):
        return ""

    value = clean(uc_value).replace("`", "")
    parts = [p for p in value.split(".") if p]
    if len(parts) != 3:
        return ""

    _, schema, table = parts
    if schema.casefold().startswith("cv_"):
        schema = schema[3:]

    return f"{schema}.{table}"


def job_id_hardcode_evidence(code):
    """
    Detecta IDs numéricos largos en contexto de ejecución/orquestación de jobs.
    No intenta asignar automáticamente cada ID a pequeño/mediano/grande;
    sólo confirma que existen IDs productivos hardcodeados en código.
    """
    evidence = []

    for line_no, line in enumerate(code.splitlines(), start=1):
        if not re.search(r"\b\d{10,18}\b", line):
            continue

        if re.search(
            r"(?i)\b(job|jobs|run|runnow|jobid|job_id|pequen|pequeñ|median|grande|cedula)\b",
            line,
        ):
            ids = re.findall(r"\b\d{10,18}\b", line)
            for job_id in ids:
                evidence.append((line_no, job_id, line.strip()))

    return evidence


def enhanced_classify(
    present_pro,
    present_uc,
    used_direct,
    used_alias,
    used_weak,
    hardcoded_uc_value,
    legacy_value_hardcoded,
    legacy_job_ids_hardcoded,
    values_equal,
):
    used = used_direct or used_alias or used_weak

    if present_uc and not present_pro:
        if used_direct or used_alias:
            return "NEW_UC_CONFIG_CONSUMED"
        if legacy_job_ids_hardcoded:
            return "NEW_UC_CONFIG_NOT_CONSUMED_LEGACY_JOB_IDS_HARDCODED"
        if legacy_value_hardcoded:
            return "NEW_UC_CONFIG_NOT_CONSUMED_LEGACY_VALUE_HARDCODED"
        if hardcoded_uc_value:
            return "NEW_UC_CONFIG_NOT_CONSUMED_HARDCODE_PRESENT"
        return "NEW_UC_CONFIG_NOT_CONSUMED"

    if present_pro and not present_uc:
        return "PRO_CONFIG_MISSING_IN_UC" if used else "LEGACY_UNUSED"

    if present_pro and present_uc:
        if used:
            return "VALID_UC" if values_equal else "VALUE_CHANGED_USED"
        return "UNUSED_BOTH"

    return "UNKNOWN"


def classify(present_pro, present_uc, used_direct, used_weak, hardcoded_uc_value, values_equal):
    used = used_direct or used_weak

    if present_uc and not present_pro:
        if used_direct:
            return "NEW_UC_CONFIG_CONSUMED"
        if hardcoded_uc_value:
            return "NEW_UC_CONFIG_NOT_CONSUMED_HARDCODE_PRESENT"
        return "NEW_UC_CONFIG_NOT_CONSUMED"

    if present_pro and not present_uc:
        return "PRO_CONFIG_MISSING_IN_UC" if used else "LEGACY_UNUSED"

    if present_pro and present_uc:
        if used:
            return "VALID_UC" if values_equal else "VALUE_CHANGED_USED"
        return "UNUSED_BOTH"

    return "UNKNOWN"

def recommended_action(classification):
    return {
        "NEW_UC_CONFIG_CONSUMED": "NO_ACTION",
        "NEW_UC_CONFIG_NOT_CONSUMED_HARDCODE_PRESENT": "ADAPT_CODE_TO_CONSUME_UC_CONFIG",
        "NEW_UC_CONFIG_NOT_CONSUMED_LEGACY_VALUE_HARDCODED": "ADAPT_CODE_TO_CONSUME_UC_CONFIG",
        "NEW_UC_CONFIG_NOT_CONSUMED_LEGACY_JOB_IDS_HARDCODED": "ADAPT_CODE_TO_CONSUME_UC_CONFIG",
        "NEW_UC_CONFIG_NOT_CONSUMED": "REVIEW_IF_KEY_IS_REQUIRED",
        "PRO_CONFIG_MISSING_IN_UC": "ADD_OR_REMAP_CONFIG_IN_UC",
        "LEGACY_UNUSED": "CLEANUP_REVIEW",
        "VALID_UC": "NO_ACTION",
        "VALUE_CHANGED_USED": "VALIDATE_ENVIRONMENT_CHANGE",
        "UNUSED_BOTH": "CLEANUP_CANDIDATE",
    }.get(classification, "REVIEW")

def main():
    pro_path = first_existing(PRO_CONFIG_CANDIDATES, "JSON PRO")
    uc_path = first_existing(UC_CONFIG_CANDIDATES, "JSON UC")

    pro = flatten_json(load_json(pro_path))
    uc = flatten_json(load_json(uc_path))

    code_by_nb, jobs_by_nb, missing_files = build_functional_code()
    all_paths = sorted(set(pro) | set(uc), key=str.casefold)

    inventory_rows = []
    usage_rows = []
    compare_rows = []
    action_rows = []
    class_counter = Counter()

    for config_path in all_paths:
        present_pro = config_path in pro
        present_uc = config_path in uc
        pro_raw = pro.get(config_path)
        uc_raw = uc.get(config_path)

        direct_re = path_regex(config_path)
        section_re = section_leaf_regex(config_path)
        leaf_re = leaf_regex(config_path)

        direct_nbs, alias_nbs, weak_nbs, hardcoded_nbs = [], [], [], []
        legacy_hardcoded_nbs, legacy_job_id_nbs = [], []
        direct_occ = alias_occ = weak_occ = hardcoded_occ = 0
        legacy_hardcoded_occ = legacy_job_id_occ = 0
        legacy_job_id_evidence = []

        top_section = config_path.split(".", 1)[0].split("[", 1)[0]
        leaf = config_path.split(".")[-1]

        for nb, code in code_by_nb.items():
            dm = direct_re.findall(code)
            if dm:
                direct_nbs.append(nb)
                direct_occ += len(dm)
            else:
                # V2: consumo mediante alias de la sección.
                aliases = find_section_aliases(code, top_section)
                ao = alias_leaf_occurrences(code, aliases, leaf)
                if ao:
                    alias_nbs.append(nb)
                    alias_occ += ao
                else:
                    sm = section_re.findall(code)
                    lm = leaf_re.findall(code)
                    if sm:
                        weak_nbs.append(nb)
                        weak_occ += len(sm)
                    elif lm:
                        weak_nbs.append(nb)
                        weak_occ += len(lm)

            if present_uc and not present_pro and not is_sensitive_path(config_path):
                n = literal_occurrences(code, uc_raw)
                if n:
                    hardcoded_nbs.append(nb)
                    hardcoded_occ += n

                # V2: detectar el valor legacy equivalente para TablasDbks_UC.
                legacy_candidate = legacy_uc_table_candidate(config_path, uc_raw)
                if legacy_candidate:
                    legacy_n = literal_occurrences(code, legacy_candidate)
                    if legacy_n:
                        legacy_hardcoded_nbs.append(nb)
                        legacy_hardcoded_occ += legacy_n

                # V2: CedulasJobsCluster contiene IDs UC nuevos; los notebooks
                # productivos pueden conservar IDs PRO hardcodeados diferentes.
                if top_section == "CedulasJobsCluster":
                    evidence = job_id_hardcode_evidence(code)
                    if evidence:
                        legacy_job_id_nbs.append(nb)
                        legacy_job_id_occ += len(evidence)
                        for line_no, job_id, line in evidence:
                            legacy_job_id_evidence.append(
                                f"{nb}:L{line_no}:{job_id}"
                            )

        direct_nbs = uniq(direct_nbs)
        alias_nbs = uniq(alias_nbs)
        weak_nbs = uniq(weak_nbs)
        hardcoded_nbs = uniq(hardcoded_nbs)
        legacy_hardcoded_nbs = uniq(legacy_hardcoded_nbs)
        legacy_job_id_nbs = uniq(legacy_job_id_nbs)

        all_usage_nbs = uniq(direct_nbs + alias_nbs + weak_nbs)
        jobs = uniq(
            job
            for nb in all_usage_nbs
            for job in jobs_by_nb.get(nb, [])
        )

        values_equal = (
            present_pro and present_uc and str(pro_raw) == str(uc_raw)
        )

        classification = enhanced_classify(
            present_pro=present_pro,
            present_uc=present_uc,
            used_direct=bool(direct_nbs),
            used_alias=bool(alias_nbs),
            used_weak=bool(weak_nbs),
            hardcoded_uc_value=bool(hardcoded_nbs),
            legacy_value_hardcoded=bool(legacy_hardcoded_nbs),
            legacy_job_ids_hardcoded=bool(legacy_job_id_nbs),
            values_equal=values_equal,
        )
        action = recommended_action(classification)
        class_counter[classification] += 1

        inventory_rows.append({
            "config_path": config_path,
            "top_section": top_section,
            "is_new_uc_section": "YES" if top_section in NEW_UC_SECTIONS else "NO",
            "present_in_pro": "YES" if present_pro else "NO",
            "present_in_uc": "YES" if present_uc else "NO",
            "pro_value": safe_value(config_path, pro_raw),
            "uc_value": safe_value(config_path, uc_raw),
            "values_equal": "YES" if values_equal else "NO",
        })

        usage_rows.append({
            "config_path": config_path,
            "direct_consumed": "YES" if direct_nbs else "NO",
            "direct_occurrences": direct_occ,
            "alias_consumed": "YES" if alias_nbs else "NO",
            "alias_occurrences": alias_occ,
            "weak_reference_detected": "YES" if weak_nbs else "NO",
            "weak_occurrences": weak_occ,
            "hardcoded_uc_value_detected": "YES" if hardcoded_nbs else "NO",
            "hardcoded_occurrences": hardcoded_occ,
            "legacy_value_hardcoded_detected": "YES" if legacy_hardcoded_nbs else "NO",
            "legacy_value_hardcoded_occurrences": legacy_hardcoded_occ,
            "legacy_job_ids_hardcoded_detected": "YES" if legacy_job_id_nbs else "NO",
            "legacy_job_ids_hardcoded_occurrences": legacy_job_id_occ,
            "legacy_job_id_evidence": join(legacy_job_id_evidence),
            "notebooks_direct": join(direct_nbs),
            "notebooks_alias": join(alias_nbs),
            "notebooks_weak": join(weak_nbs),
            "notebooks_hardcoded_value": join(hardcoded_nbs),
            "notebooks_legacy_value_hardcoded": join(legacy_hardcoded_nbs),
            "notebooks_legacy_job_ids_hardcoded": join(legacy_job_id_nbs),
            "jobs": join(jobs),
        })

        compare_rows.append({
            "config_path": config_path,
            "present_in_pro": "YES" if present_pro else "NO",
            "present_in_uc": "YES" if present_uc else "NO",
            "pro_value": safe_value(config_path, pro_raw),
            "uc_value": safe_value(config_path, uc_raw),
            "values_equal": "YES" if values_equal else "NO",
            "functional_usage_detected": "YES" if all_usage_nbs else "NO",
            "classification": classification,
        })

        if action != "NO_ACTION":
            action_rows.append({
                "config_path": config_path,
                "classification": classification,
                "recommended_action": action,
                "notebooks": join(
                    all_usage_nbs
                    or legacy_hardcoded_nbs
                    or legacy_job_id_nbs
                    or hardcoded_nbs
                ),
                "jobs": join(jobs),
                "notes": (
                    "Consumo directo parsedConfiguration.* detectado."
                    if direct_nbs
                    else (
                        "Consumo mediante alias de sección parsedConfiguration.* detectado."
                        if alias_nbs
                        else (
                            "Valor legacy equivalente hardcodeado en código funcional."
                            if legacy_hardcoded_nbs
                            else (
                                "IDs productivos hardcodeados detectados en contexto de jobs; "
                                "la nueva sección CedulasJobsCluster aún no es consumida."
                                if legacy_job_id_nbs
                                else (
                                    "Valor UC hardcodeado detectado en código funcional."
                                    if hardcoded_nbs
                                    else (
                                        "Sólo evidencia secundaria por nombre de sección/clave."
                                        if weak_nbs
                                        else "Sin consumo funcional detectado."
                                    )
                                )
                            )
                        )
                    )
                ),
            })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def write_csv(path, rows, fields):
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    write_csv(
        OUT_INVENTORY, inventory_rows,
        ["config_path","top_section","is_new_uc_section","present_in_pro",
         "present_in_uc","pro_value","uc_value","values_equal"]
    )
    write_csv(
        OUT_USAGE, usage_rows,
        ["config_path","direct_consumed","direct_occurrences",
         "alias_consumed","alias_occurrences",
         "weak_reference_detected","weak_occurrences",
         "hardcoded_uc_value_detected","hardcoded_occurrences",
         "legacy_value_hardcoded_detected","legacy_value_hardcoded_occurrences",
         "legacy_job_ids_hardcoded_detected","legacy_job_ids_hardcoded_occurrences",
         "legacy_job_id_evidence",
         "notebooks_direct","notebooks_alias","notebooks_weak",
         "notebooks_hardcoded_value","notebooks_legacy_value_hardcoded",
         "notebooks_legacy_job_ids_hardcoded","jobs"]
    )
    write_csv(
        OUT_COMPARE, compare_rows,
        ["config_path","present_in_pro","present_in_uc","pro_value","uc_value",
         "values_equal","functional_usage_detected","classification"]
    )
    write_csv(
        OUT_ACTIONS, action_rows,
        ["config_path","classification","recommended_action","notebooks","jobs","notes"]
    )

    print("=" * 78)
    print("ASSESSMENT WORKSPACE - PASO 29 V2")
    print("USO FUNCIONAL DE CLAVES DE CONFIGURACION PRO / UC - DETECCION LEGACY")
    print("=" * 78)
    print()
    print(f"JSON PRO                        : {pro_path}")
    print(f"JSON UC                         : {uc_path}")
    print(f"Claves hoja PRO                 : {len(pro)}")
    print(f"Claves hoja UC                  : {len(uc)}")
    print(f"Claves únicas consolidadas      : {len(all_paths)}")
    print(f"Notebooks funcionales leídos    : {len(code_by_nb)}")
    print(f"Notebooks sin archivo local     : {len(missing_files)}")
    print()
    print("Clasificación:")
    for key in sorted(class_counter):
        print(f" - {key:<46}: {class_counter[key]}")
    print()
    print("--- Nuevas secciones UC ---")
    for section in sorted(NEW_UC_SECTIONS):
        rows = [r for r in compare_rows if r["config_path"].startswith(section + ".")]
        counts = Counter(r["classification"] for r in rows)
        print(f"{section}:")
        for status in sorted(counts):
            print(f"   - {status:<42}: {counts[status]}")
    print()
    if missing_files:
        print("ADVERTENCIA - notebooks funcionales sin código local:")
        for nb, reason in missing_files[:20]:
            print(f" - {nb} | {reason}")
        if len(missing_files) > 20:
            print(f" - ... y {len(missing_files)-20} más")
        print()
    print(f"Generado: {OUT_INVENTORY}")
    print(f"Generado: {OUT_USAGE}")
    print(f"Generado: {OUT_COMPARE}")
    print(f"Generado: {OUT_ACTIONS}")
    print("=" * 78)

if __name__ == "__main__":
    main()
