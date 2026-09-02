#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import re

JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
NOTEBOOK_INVENTORY_FILE = Path("output/notebooks.csv")
STORAGE_ANALYSIS_FILE = Path("output/storage_migration_analysis.csv")
TABLE_FINAL_FILE = Path("output/table_hive_reconciliation_final.csv")
OUTPUT_FILE = Path("output/environment_hardcodes.csv")


def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


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
        if match:
            result.append(match.group(1) + match.group(2))
        else:
            result.append(line)
    return "\n".join(result)


def remove_comments(code):
    code = normalize_magic_lines(code)
    result = []
    i = 0
    length = len(code)
    in_single = False
    in_double = False
    in_triple_double = False
    in_block_comment = False

    while i < length:
        if in_block_comment:
            if code[i:i + 2] == "*/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if not in_single and not in_double and code[i:i + 3] == '\"\"\"':
            in_triple_double = not in_triple_double
            result.append('\"\"\"')
            i += 3
            continue

        if in_triple_double:
            result.append(code[i])
            i += 1
            continue

        if code[i] == '"' and not in_single:
            escaped = i > 0 and code[i - 1] == "\\"
            if not escaped:
                in_double = not in_double
            result.append(code[i])
            i += 1
            continue

        if code[i] == "'" and not in_double:
            escaped = i > 0 and code[i - 1] == "\\"
            if not escaped:
                in_single = not in_single
            result.append(code[i])
            i += 1
            continue

        if not in_single and not in_double:
            if code[i:i + 2] == "/*":
                in_block_comment = True
                i += 2
                continue
            if code[i:i + 2] in {"//", "--"}:
                while i < length and code[i] != "\n":
                    i += 1
                continue
            if code[i] == "#":
                while i < length and code[i] != "\n":
                    i += 1
                continue

        result.append(code[i])
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


def build_storage_covered_index(rows):
    covered = set()
    for row in rows:
        notebook = normalize(row.get("notebook"))
        reference = normalize(row.get("storage_reference"))
        if notebook and reference:
            covered.add((notebook, reference))
    return covered


def build_table_covered_index(rows):
    values = set()
    for row in rows:
        for field in ("tabla_pro", "tabla_uc"):
            value = normalize(row.get(field))
            if value:
                values.add(value)
    return values


PATTERNS = [
    (
        "CONFIG_FILE_HARDCODE",
        re.compile(r'''(?i)["']((?:dbfs:/|/mnt/|/volumes/)[^"']*0\.0_configuration\.json)["']'''),
    ),
    (
        "LEGACY_STORAGE_PATH",
        re.compile(r'''(?i)["'](dbfs:/[^"']+|/mnt/[^"']+)["']'''),
    ),
    (
        "WORKSPACE_PATH",
        re.compile(r'''(?i)["'](/workspace/[^"']+)["']'''),
    ),
    (
        "ABFS_PATH",
        re.compile(r'''(?i)["'](abfss?://[^"']+)["']'''),
    ),
    (
        "WASB_PATH",
        re.compile(r'''(?i)["'](wasbs?://[^"']+)["']'''),
    ),
    (
        "STORAGE_ACCOUNT",
        re.compile(r'''(?i)\b([a-z0-9]{3,24}\.dfs\.core\.windows\.net)\b'''),
    ),
    (
        "AZURE_POSTGRES_HOST",
        re.compile(r'''(?i)\b([a-z0-9.-]+\.postgres\.database\.azure\.com)\b'''),
    ),
    (
        "HTTP_URL",
        re.compile(r'''(?i)["'](https?://[^"']+)["']'''),
    ),
    (
        "ENVIRONMENT_LITERAL",
        re.compile(r'''(?i)["']([^"']*(?:\bpro\b|\buat\b|\bprod\b|\bproduction\b|\bdev\b|\bdesa\b|\bdesarrollo\b|\btest\b)[^"']*)["']'''),
    ),
]


def likely_safe_literal(hardcode_type, value):
    normalized = normalize(value)
    if not normalized:
        return True
    if hardcode_type == "HTTP_URL" and ("localhost" in normalized or "127.0.0.1" in normalized):
        return True
    if "${" in value and hardcode_type not in {"LEGACY_STORAGE_PATH", "CONFIG_FILE_HARDCODE"}:
        return True
    return False


def classify_coverage(notebook, hardcode_type, value, storage_covered, table_covered):
    notebook_norm = normalize(notebook)
    value_norm = normalize(value)

    for covered_notebook, covered_reference in storage_covered:
        if covered_notebook == notebook_norm and (
            value_norm == covered_reference
            or value_norm in covered_reference
            or covered_reference in value_norm
        ):
            return "STEP_15", "NO", "Hallazgo ya documentado en análisis de storage."

    if value_norm in table_covered:
        return "STEP_14", "NO", "Referencia de tabla ya cubierta por inventario de tablas."

    if hardcode_type in {"LEGACY_STORAGE_PATH", "CONFIG_FILE_HARDCODE"}:
        return "", "YES", "Migrar a configuración o ruta UC."
    if hardcode_type == "WORKSPACE_PATH":
        return "", "YES", "Validar si la ruta Workspace es portable entre ambientes."
    if hardcode_type in {"STORAGE_ACCOUNT", "ABFS_PATH", "WASB_PATH"}:
        return "", "YES", "Validar si el recurso debe permanecer hardcodeado o moverse a configuración."
    if hardcode_type == "AZURE_POSTGRES_HOST":
        return "", "YES", "Mover host específico de ambiente a configuración si no está externalizado."
    if hardcode_type == "HTTP_URL":
        return "", "YES", "Validar si la URL depende del ambiente."
    if hardcode_type == "ENVIRONMENT_LITERAL":
        return "", "REVIEW", "Validar si el literal representa dependencia real de ambiente."
    return "", "REVIEW", "Revisar manualmente."


def main():
    required_files = [JOB_INVENTORY_FILE, NOTEBOOK_INVENTORY_FILE]
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        print("ERROR: faltan archivos requeridos:")
        for path in missing:
            print(f" - {path}")
        raise SystemExit(1)

    job_rows = read_csv(JOB_INVENTORY_FILE)
    notebook_rows = read_csv(NOTEBOOK_INVENTORY_FILE)
    storage_rows = read_csv(STORAGE_ANALYSIS_FILE) if STORAGE_ANALYSIS_FILE.exists() else []
    table_rows = read_csv(TABLE_FINAL_FILE) if TABLE_FINAL_FILE.exists() else []

    storage_covered = build_storage_covered_index(storage_rows)
    table_covered = build_table_covered_index(table_rows)
    notebook_index = build_notebook_index(notebook_rows)

    job_notebooks = defaultdict(set)
    for row in job_rows:
        notebook = clean(row.get("notebook"))
        job = clean(row.get("job"))
        if not notebook:
            continue
        if job:
            job_notebooks[notebook].add(job)
        else:
            job_notebooks[notebook]

    output_rows = []
    dedupe = set()
    missing_notebooks = []

    for notebook, jobs in sorted(job_notebooks.items(), key=lambda item: normalize(item[0])):
        notebook_path = notebook_index.get(normalize(notebook))
        if notebook_path is None or not notebook_path.exists():
            missing_notebooks.append(notebook)
            continue

        blocks = get_code_blocks(notebook_path)

        for cell_index, block in enumerate(blocks, start=1):
            code = remove_comments(block)

            for hardcode_type, pattern in PATTERNS:
                for match in pattern.finditer(code):
                    value = clean(match.group(1))

                    if hardcode_type == "LEGACY_STORAGE_PATH" and normalize(value).endswith("/0.0_configuration.json"):
                        continue

                    if likely_safe_literal(hardcode_type, value):
                        continue

                    key = (normalize(notebook), cell_index, hardcode_type, normalize(value))
                    if key in dedupe:
                        continue
                    dedupe.add(key)

                    context_start = max(0, match.start() - 100)
                    context_end = min(len(code), match.end() + 100)
                    context = code[context_start:context_end].replace("\n", " ").strip()

                    already_covered_by, requires_action, recommended_action = classify_coverage(
                        notebook,
                        hardcode_type,
                        value,
                        storage_covered,
                        table_covered,
                    )

                    output_rows.append({
                        "job": unique_join(sorted(jobs, key=str.casefold)),
                        "notebook": notebook,
                        "cell": cell_index,
                        "hardcode_type": hardcode_type,
                        "hardcoded_value": value,
                        "context": context,
                        "already_covered_by": already_covered_by,
                        "requires_action": requires_action,
                        "recommended_action": recommended_action,
                    })

    type_order = {
        "CONFIG_FILE_HARDCODE": 1,
        "LEGACY_STORAGE_PATH": 2,
        "WORKSPACE_PATH": 3,
        "ABFS_PATH": 4,
        "WASB_PATH": 5,
        "STORAGE_ACCOUNT": 6,
        "AZURE_POSTGRES_HOST": 7,
        "HTTP_URL": 8,
        "ENVIRONMENT_LITERAL": 9,
    }

    output_rows.sort(
        key=lambda row: (
            type_order.get(row["hardcode_type"], 99),
            normalize(row["notebook"]),
            int(row["cell"]),
            normalize(row["hardcoded_value"]),
        )
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "job",
        "notebook",
        "cell",
        "hardcode_type",
        "hardcoded_value",
        "context",
        "already_covered_by",
        "requires_action",
        "recommended_action",
    ]

    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    type_counter = Counter(row["hardcode_type"] for row in output_rows)
    coverage_counter = Counter(row["already_covered_by"] or "NEW" for row in output_rows)
    action_counter = Counter(row["requires_action"] for row in output_rows)

    print("=" * 72)
    print("ASSESSMENT WORKSPACE - PASO 16")
    print("ANALISIS DE HARDCODES DE AMBIENTE")
    print("=" * 72)
    print()
    print(f"Notebooks de alcance            : {len(job_notebooks)}")
    print(f"Notebooks faltantes en snapshot : {len(missing_notebooks)}")
    print(f"Hallazgos detectados            : {len(output_rows)}")
    print()
    print("Resumen por tipo:")
    for hardcode_type in sorted(type_counter, key=lambda value: (type_order.get(value, 99), value)):
        print(f" - {hardcode_type:<32}: {type_counter[hardcode_type]}")
    print()
    print("Cobertura:")
    for coverage in sorted(coverage_counter):
        print(f" - {coverage:<32}: {coverage_counter[coverage]}")
    print()
    print("Acción requerida:")
    for action in sorted(action_counter):
        print(f" - {action:<32}: {action_counter[action]}")

    if missing_notebooks:
        print()
        print("Notebooks faltantes (primeros 10):")
        for notebook in missing_notebooks[:10]:
            print(f" - {notebook}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE}")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
