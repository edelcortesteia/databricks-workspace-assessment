from pathlib import Path
from collections import defaultdict, Counter
import csv
import re

NOTEBOOKS_FILE = Path("output/notebooks.csv")
REACHABILITY_FILE = Path("output/notebook_reachability.csv")
JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
TABLE_REFERENCES_FILE = Path("output/table_references.csv")
OUTPUT_FILE = Path("output/dynamic_variable_sources.csv")

ASSIGNMENT_PATTERN = re.compile(r"(?ix)\b(?:lazy\s+val|val|var)\s+([A-Za-z_]\w*)(?:\s*:\s*[\w\[\].]+)?\s*=\s*([^\n;]+)")
FUNCTION_PARAMETER_PATTERN = re.compile(r"(?ix)\bdef\s+([A-Za-z_]\w*)\s*\(([^)]*)\)")
ITERATOR_PATTERN = re.compile(r"(?ix)\b([A-Za-z_]\w*)\.(map|foreach|flatMap|filter)\s*\(\s*([A-Za-z_]\w*)\s*=>")

def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo requerido: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def project_path(value):
    return Path(".") / Path(str(value or "").strip().replace("\\\\", "/"))

def get_code_blocks(path):
    content = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"(?m)^\s*(?://|#|--)\s*COMMAND\s*-+\s*$", content)
    normalized = []
    for block in blocks:
        lines = []
        for line in block.splitlines():
            m = re.match(r"^\s*(?://|#|--)\s*MAGIC\s?(.*)$", line, flags=re.IGNORECASE)
            lines.append(m.group(1) if m else line)
        normalized.append("\n".join(lines))
    return normalized

def remove_comments(code):
    # Excluir //, /*...*/, # y -- sin importar lenguaje.
    # No tratar estos marcadores como comentario dentro de strings.
    result = []
    i, n = 0, len(code)
    quote = None
    triple = None
    block_comment = False

    while i < n:
        if block_comment:
            if code[i:i+2] == "*/":
                block_comment = False
                i += 2
            else:
                if code[i] == "\n":
                    result.append("\n")
                i += 1
            continue

        if triple:
            if code[i:i+3] == triple:
                result.append(triple)
                i += 3
                triple = None
            else:
                result.append(code[i])
                i += 1
            continue

        if quote:
            ch = code[i]
            result.append(ch)
            if ch == "\\\\" and i + 1 < n:
                result.append(code[i+1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        token3 = code[i:i+3]
        if token3 == chr(34)*3 or token3 == chr(39)*3:
            triple = token3
            result.append(token3)
            i += 3
            continue

        if code[i] in {chr(34), chr(39)}:
            quote = code[i]
            result.append(code[i])
            i += 1
            continue

        if code[i:i+2] == "/*":
            block_comment = True
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

def main():
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 09")
    print("ORIGEN DE VARIABLES DINAMICAS DE TABLAS")
    print("=" * 70)
    print()

    notebooks = load_csv(NOTEBOOKS_FILE)
    reachability = load_csv(REACHABILITY_FILE)
    job_inventory = load_csv(JOB_INVENTORY_FILE)
    table_refs = load_csv(TABLE_REFERENCES_FILE)

    notebook_files = {}
    for row in notebooks:
        wp = (row.get("workspace_path") or "").strip()
        lf = (row.get("local_file") or row.get("path") or "").strip()
        if wp and lf:
            notebook_files[wp] = project_path(lf)

    used_notebooks = {
        (r.get("notebook") or "").strip()
        for r in reachability
        if (r.get("status") or "").strip() in {"ROOT", "REACHABLE"}
        and (r.get("notebook") or "").strip()
    }

    notebook_jobs = defaultdict(set)
    for r in job_inventory:
        nb = (r.get("notebook") or "").strip()
        job = (r.get("job") or "").strip()
        if nb and job:
            notebook_jobs[nb].add(job)

    dynamic_variables = defaultdict(set)
    for r in table_refs:
        if (r.get("name_format") or "").strip() not in {"DYNAMIC_VARIABLE", "DYNAMIC_TABLE_EXPRESSION"}:
            continue
        nb = (r.get("notebook") or "").strip()
        ref = (r.get("table_reference") or "").strip()
        for variable in re.findall(r"\$\{([A-Za-z_]\w*)\}", ref):
            dynamic_variables[nb].add(variable)

    rows = []
    missing_files = []
    analyzed = 0

    for notebook in sorted(dynamic_variables, key=str.casefold):
        if notebook not in used_notebooks:
            continue

        variables_needed = dynamic_variables[notebook]
        local_file = notebook_files.get(notebook)

        if local_file is None or not local_file.exists():
            missing_files.append((notebook, str(local_file or "SIN_MAPEO_LOCAL")))
            continue

        analyzed += 1

        for cell_number, original_code in enumerate(get_code_blocks(local_file), start=1):
            code = remove_comments(original_code)

            for match in ASSIGNMENT_PATTERN.finditer(code):
                variable = match.group(1).strip()
                expression = match.group(2).strip()
                if variable not in variables_needed:
                    continue
                if expression.endswith(","):
                    expression = expression[:-1].strip()
                rows.append({
                    "notebook": notebook,
                    "cell": cell_number,
                    "variable": variable,
                    "source_type": "DIRECT_ASSIGNMENT",
                    "source_expression": expression,
                    "jobs": " | ".join(sorted(notebook_jobs.get(notebook, set()), key=str.casefold)),
                })

            for match in FUNCTION_PARAMETER_PATTERN.finditer(code):
                function_name = match.group(1)
                parameters = match.group(2)
                for variable in variables_needed:
                    if re.search(rf"\b{re.escape(variable)}\s*:\s*[\w\[\].]+", parameters):
                        rows.append({
                            "notebook": notebook,
                            "cell": cell_number,
                            "variable": variable,
                            "source_type": "FUNCTION_PARAMETER",
                            "source_expression": f"parameter of {function_name}",
                            "jobs": " | ".join(sorted(notebook_jobs.get(notebook, set()), key=str.casefold)),
                        })

            for match in ITERATOR_PATTERN.finditer(code):
                collection_name, operation, iterator_variable = match.groups()
                if iterator_variable not in variables_needed:
                    continue
                rows.append({
                    "notebook": notebook,
                    "cell": cell_number,
                    "variable": iterator_variable,
                    "source_type": "ITERATOR_VARIABLE",
                    "source_expression": f"{collection_name}.{operation}({iterator_variable} => ...)",
                    "jobs": " | ".join(sorted(notebook_jobs.get(notebook, set()), key=str.casefold)),
                })

    if missing_files:
        examples = "\n".join(f" - {nb}: {p}" for nb, p in missing_files[:20])
        raise RuntimeError(f"No fue posible localizar notebooks requeridos.\\nFaltantes: {len(missing_files)}\\n{examples}")

    unique_rows, seen = [], set()
    duplicates = 0
    for r in rows:
        key = (r["notebook"], r["variable"], r["source_type"], r["source_expression"])
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique_rows.append(r)
    rows = unique_rows

    rows.sort(key=lambda r: (r["notebook"].casefold(), r["variable"].casefold(), int(r["cell"])))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["notebook", "cell", "variable", "source_type", "source_expression", "jobs"]
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    used_pairs = {(nb, var) for nb, variables in dynamic_variables.items() for var in variables}
    resolved_pairs = {(r["notebook"], r["variable"]) for r in rows}
    unresolved_pairs = sorted(used_pairs - resolved_pairs, key=lambda x: (x[0].casefold(), x[1].casefold()))
    unique_names = {var for _, var in used_pairs}
    counts = Counter(r["source_type"] for r in rows)

    print("--- Alcance ---")
    print(f"Notebooks con variables dinamicas : {len(dynamic_variables)}")
    print(f"Notebooks analizados              : {analyzed}")
    print(f"Archivos faltantes                : {len(missing_files)}")
    print()
    print("--- Variables ---")
    print(f"Nombres de variable unicos        : {len(unique_names)}")
    print(f"Usos Notebook+Variable            : {len(used_pairs)}")
    print(f"Origenes detectados               : {len(rows)}")
    print(f"Usos con origen identificado      : {len(resolved_pairs)}")
    print(f"Usos sin origen identificado      : {len(unresolved_pairs)}")
    print(f"Duplicados omitidos               : {duplicates}")
    print()
    print("Resumen por tipo de origen:")
    for source_type in sorted(counts):
        print(f" - {source_type:<22}: {counts[source_type]}")
    print()
    print("Usos sin origen identificado:")
    if unresolved_pairs:
        for nb, var in unresolved_pairs:
            print(f" - {var}")
            print(f"     {nb}")
    else:
        print(" - Ninguno")
    print()
    print(f"Archivo generado: {OUTPUT_FILE.resolve()}")
    print(f"Registros generados: {len(rows)}")
    print()
    print("=" * 70)
    print("RESULTADO: ORIGENES DE VARIABLES DINAMICAS GENERADOS")
    print("=" * 70)

if __name__ == "__main__":
    main()