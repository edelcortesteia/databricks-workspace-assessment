from pathlib import Path
from collections import defaultdict, Counter
import csv
import re

NOTEBOOKS_FILE = Path("output/notebooks.csv")
REACHABILITY_FILE = Path("output/notebook_reachability.csv")
JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
OUTPUT_FILE = Path("output/storage_references.csv")

# No se considera '}' terminador para soportar rutas interpoladas
# como dbfs:/${variable}/ruta.
PATH_END = r"[^\s\"'`,;)\]]+"

PATTERNS = {
    "MOUNT": re.compile(rf"(?i)(?:dbfs:)?/mnt/{PATH_END}"),
    "DBFS": re.compile(rf"(?i)(?:dbfs:/|/dbfs/){PATH_END}"),
    "WASB": re.compile(rf"(?i)wasbs?://{PATH_END}"),
    "ABFS": re.compile(rf"(?i)abfss?://{PATH_END}"),
    "HDFS": re.compile(rf"(?i)hdfs://{PATH_END}"),
    "FILE": re.compile(rf"(?i)file:/+{PATH_END}"),
}

def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo requerido: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def project_path(value):
    return Path(".") / Path(str(value or "").strip().replace("\\", "/"))

def split_databricks_source(content):
    return re.split(
        r"(?m)^\s*(?://|#|--)?\s*COMMAND\s*-+\s*$",
        content,
    )

def remove_comments(code):
    result = []
    i = 0
    n = len(code)
    quote = None
    triple_quote = None
    in_block_comment = False

    while i < n:
        if in_block_comment:
            if code[i:i+2] == "*/":
                in_block_comment = False
                i += 2
                continue
            if code[i] == "\n":
                result.append("\n")
            i += 1
            continue

        if triple_quote:
            if code[i:i+3] == triple_quote:
                result.append(triple_quote)
                i += 3
                triple_quote = None
                continue
            result.append(code[i])
            i += 1
            continue

        if quote:
            ch = code[i]
            result.append(ch)
            if ch == "\\" and i + 1 < n:
                result.append(code[i+1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        token3 = code[i:i+3]
        if token3 == '"""' or token3 == "'''":
            triple_quote = token3
            result.append(token3)
            i += 3
            continue

        if code[i] in {'"', "'"}:
            quote = code[i]
            result.append(code[i])
            i += 1
            continue

        if code[i:i+2] == "/*":
            in_block_comment = True
            i += 2
            continue

        if code[i:i+2] in {"//", "--"}:
            while i < n and code[i] != "\n":
                i += 1
            continue

        if code[i] == "#":
            while i < n and code[i] != "\n":
                i += 1
            continue

        result.append(code[i])
        i += 1

    return "".join(result)

def classify_reference(value):
    return "DYNAMIC" if ("${" in value or "$" in value) else "STATIC"

def main():
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 07")
    print("REFERENCIAS DE STORAGE EN NOTEBOOKS UTILIZADOS")
    print("=" * 70)
    print()

    notebooks = load_csv(NOTEBOOKS_FILE)
    reachability = load_csv(REACHABILITY_FILE)
    job_inventory = load_csv(JOB_INVENTORY_FILE)

    notebook_files = {}
    for row in notebooks:
        wp = (row.get("workspace_path") or "").strip()
        lf = (row.get("local_file") or row.get("path") or "").strip()
        if wp and lf:
            notebook_files[wp] = project_path(lf)

    used_notebooks = {
        (row.get("notebook") or "").strip()
        for row in reachability
        if (row.get("status") or "").strip() in {"ROOT", "REACHABLE"}
        and (row.get("notebook") or "").strip()
    }

    notebook_jobs = defaultdict(set)
    for row in job_inventory:
        notebook = (row.get("notebook") or "").strip()
        job = (row.get("job") or "").strip()
        if notebook and job:
            notebook_jobs[notebook].add(job)

    rows = []
    missing_files = []
    processed = 0

    for notebook in sorted(used_notebooks, key=str.casefold):
        local_file = notebook_files.get(notebook)

        if local_file is None:
            missing_files.append((notebook, "SIN_MAPEO_LOCAL"))
            continue

        if not local_file.exists():
            missing_files.append((notebook, str(local_file)))
            continue

        processed += 1
        content = local_file.read_text(encoding="utf-8", errors="ignore")
        blocks = split_databricks_source(content)

        for cell_number, original_code in enumerate(blocks, start=1):
            code = remove_comments(original_code)

            for finding_type, pattern in PATTERNS.items():
                for match in pattern.finditer(code):
                    value = match.group(0).strip()

                    rows.append({
                        "notebook": notebook,
                        "cell": cell_number,
                        "finding_type": finding_type,
                        "value": value,
                        "jobs": " | ".join(sorted(
                            notebook_jobs.get(notebook, set()),
                            key=str.casefold,
                        )),
                    })

    if missing_files:
        examples = "\n".join(
            f" - {notebook}: {path}"
            for notebook, path in missing_files[:20]
        )
        raise RuntimeError(
            "No fue posible localizar físicamente todos los notebooks utilizados.\n"
            f"Faltantes: {len(missing_files)}\n{examples}"
        )

    unique_rows = []
    seen = set()
    duplicates = 0

    for row in rows:
        key = (
            row["notebook"],
            row["cell"],
            row["finding_type"],
            row["value"],
        )
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique_rows.append(row)

    rows = unique_rows
    rows.sort(
        key=lambda row: (
            row["notebook"].casefold(),
            int(row["cell"]),
            row["finding_type"],
            row["value"].casefold(),
        )
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["notebook", "cell", "finding_type", "value", "jobs"],
        )
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["finding_type"] for row in rows)
    mode_counts = Counter(classify_reference(row["value"]) for row in rows)

    print("--- Alcance ---")
    print(f"Notebooks en alcance          : {len(used_notebooks)}")
    print(f"Notebooks analizados          : {processed}")
    print(f"Archivos faltantes            : {len(missing_files)}")
    print()

    print("--- Hallazgos ---")
    print(f"Referencias detectadas        : {len(rows)}")
    print(f"Duplicados omitidos           : {duplicates}")
    print()

    print("Resumen por tipo:")
    for finding_type in PATTERNS:
        print(f" - {finding_type:<10}: {counts[finding_type]}")

    print()
    print("Resumen por modo:")
    print(f" - STATIC    : {mode_counts['STATIC']}")
    print(f" - DYNAMIC   : {mode_counts['DYNAMIC']}")

    print()
    print("Detalle detectado:")
    for row in rows:
        mode = classify_reference(row["value"])
        print(
            f" - [{row['finding_type']}/{mode}] "
            f"{row['notebook']} (celda {row['cell']})"
        )
        print(f"   {row['value']}")

    print()
    print(f"Archivo generado: {OUTPUT_FILE.resolve()}")
    print(f"Registros generados: {len(rows)}")
    print()
    print("=" * 70)
    print("RESULTADO: REFERENCIAS DE STORAGE GENERADAS CORRECTAMENTE")
    print("=" * 70)

if __name__ == "__main__":
    main()