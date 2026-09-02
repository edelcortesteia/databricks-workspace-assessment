#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import ast
import csv
import json
import re

JOB_INVENTORY_FILE = Path("output/job_notebook_inventory.csv")
NOTEBOOK_INVENTORY_FILE = Path("output/notebooks.csv")
PRO_CONFIG_FILE = Path("input/config/0.0_Configuration_PROD.json")
UC_CONFIG_FILE = Path("input/config/0.0_Configuration_UC.json")
OUTPUT_FILE = Path("output/secret_usage_analysis.csv")


def clean(value):
    return "" if value is None else str(value).strip()


def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


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


def get_json_value(data, path):
    if not path:
        return None
    current = data
    for part in clean(path).split("."):
        if not isinstance(current, dict):
            return None
        real_key = next((k for k in current if k.casefold() == part.casefold()), None)
        if real_key is None:
            return None
        current = current[real_key]
    return current


def scalar_to_string(value):
    return str(value) if isinstance(value, (str, int, float, bool)) else ""


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
        match = re.match(r'^(\s*)(?://|#|--)\s*MAGIC\s?(.*)$', line, flags=re.IGNORECASE)
        result.append(match.group(1) + match.group(2) if match else line)
    return "\n".join(result)


def remove_comments(code):
    code = normalize_magic_lines(code)
    result = []
    i = 0
    in_single = in_double = in_block = False

    while i < len(code):
        if in_block:
            if code[i:i+2] == "*/":
                in_block = False
                i += 2
            else:
                i += 1
            continue

        ch = code[i]

        if ch == '"' and not in_single:
            if i == 0 or code[i-1] != "\\":
                in_double = not in_double
            result.append(ch)
            i += 1
            continue

        if ch == "'" and not in_double:
            if i == 0 or code[i-1] != "\\":
                in_single = not in_single
            result.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            if code[i:i+2] == "/*":
                in_block = True
                i += 2
                continue
            if code[i:i+2] in {"//", "--"}:
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


CONFIG_EXPR_RE = re.compile(r'parsedConfiguration((?:\.[A-Za-z_][A-Za-z0-9_]*)+)')


def config_path_from_expr(expr):
    match = CONFIG_EXPR_RE.search(clean(expr))
    return match.group(1).lstrip(".") if match else ""


def strip_outer(expr):
    expr = clean(expr)
    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in {"'", '"'}:
        try:
            return ast.literal_eval(expr)
        except Exception:
            return expr[1:-1]
    return expr


def parse_call_arguments(text):
    args = []
    current = []
    round_d = square_d = curly_d = 0
    in_single = in_double = escaped = False

    for ch in text:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\" and (in_single or in_double):
            current.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            continue

        if not in_single and not in_double:
            if ch == "(":
                round_d += 1
            elif ch == ")":
                round_d = max(0, round_d - 1)
            elif ch == "[":
                square_d += 1
            elif ch == "]":
                square_d = max(0, square_d - 1)
            elif ch == "{":
                curly_d += 1
            elif ch == "}":
                curly_d = max(0, curly_d - 1)
            elif ch == "," and round_d == 0 and square_d == 0 and curly_d == 0:
                args.append("".join(current).strip())
                current = []
                continue

        current.append(ch)

    if current:
        args.append("".join(current).strip())

    return args


def find_balanced_calls(code, call_names):
    calls = []

    for call_name in call_names:
        start_re = re.compile(re.escape(call_name) + r"\s*\(", flags=re.IGNORECASE)

        for match in start_re.finditer(code):
            open_pos = code.find("(", match.start())
            depth = 0
            in_single = in_double = escaped = False
            i = open_pos

            while i < len(code):
                ch = code[i]

                if escaped:
                    escaped = False
                    i += 1
                    continue
                if ch == "\\" and (in_single or in_double):
                    escaped = True
                    i += 1
                    continue
                if ch == "'" and not in_double:
                    in_single = not in_single
                    i += 1
                    continue
                if ch == '"' and not in_single:
                    in_double = not in_double
                    i += 1
                    continue

                if not in_single and not in_double:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            calls.append({
                                "call_name": call_name,
                                "start": match.start(),
                                "end": i + 1,
                                "arguments": code[open_pos+1:i],
                                "source": code[match.start():i+1],
                            })
                            break
                i += 1

    return sorted(calls, key=lambda x: x["start"])


def find_assignments(code):
    assignments = {}
    pattern = re.compile(r'(?m)^\s*(?:(?:val|var)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^\n;]+)')
    for match in pattern.finditer(code):
        assignments[match.group(1)] = clean(match.group(2))
    return assignments


def resolve_expr(expr, assignments, pro_config, uc_config, depth=0):
    expr = clean(expr)

    if depth > 6:
        return {"mode":"UNRESOLVED","expr":expr,"config_path":"","pro_value":"","uc_value":"","resolved_value":""}

    literal = strip_outer(expr)
    if literal != expr:
        return {"mode":"LITERAL","expr":expr,"config_path":"","pro_value":"","uc_value":"","resolved_value":clean(literal)}

    config_path = config_path_from_expr(expr)
    if config_path:
        return {
            "mode":"CONFIG_DRIVEN",
            "expr":expr,
            "config_path":config_path,
            "pro_value":scalar_to_string(get_json_value(pro_config, config_path)),
            "uc_value":scalar_to_string(get_json_value(uc_config, config_path)),
            "resolved_value":scalar_to_string(get_json_value(pro_config, config_path)),
        }

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
        if expr in assignments:
            result = resolve_expr(assignments[expr], assignments, pro_config, uc_config, depth+1)
            result = dict(result)
            result["mode"] = "VARIABLE_" + result["mode"]
            result["expr"] = expr
            return result
        return {"mode":"VARIABLE_UNRESOLVED","expr":expr,"config_path":"","pro_value":"","uc_value":"","resolved_value":""}

    return {"mode":"EXPRESSION_REVIEW","expr":expr,"config_path":"","pro_value":"","uc_value":"","resolved_value":""}



def find_iterator_config_paths(code):
    """
    Detecta iteradores Scala/Python sobre listas de parsedConfiguration.
    Ejemplos soportados:
      parsedConfiguration.StorageMountList.foreach(v => ...)
      parsedConfiguration.StorageMountList.map(v => ...)
      for (v <- parsedConfiguration.StorageMountList)
    Devuelve {variable: config_path_base}.
    """
    result = {}

    scala_lambda = re.compile(
        r'parsedConfiguration((?:\.[A-Za-z_][A-Za-z0-9_]*)+)'
        r'\s*\.\s*(?:foreach|map|flatMap|filter)\s*\(\s*'
        r'([A-Za-z_][A-Za-z0-9_]*)\s*=>',
        flags=re.IGNORECASE,
    )

    scala_for = re.compile(
        r'for\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*<-\s*'
        r'parsedConfiguration((?:\.[A-Za-z_][A-Za-z0-9_]*)+)',
        flags=re.IGNORECASE,
    )

    for match in scala_lambda.finditer(code):
        config_path = match.group(1).lstrip(".")
        variable = match.group(2)
        result[variable] = config_path

    for match in scala_for.finditer(code):
        variable = match.group(1)
        config_path = match.group(2).lstrip(".")
        result[variable] = config_path

    return result


def values_from_list_object_path(data, base_path, suffix_path):
    """
    Resuelve valores tipo:
      StorageMountList[*].UrlWithSas.scope
      StorageMountList[*].UrlWithSas.key
    """
    base_value = get_json_value(data, base_path)

    if not isinstance(base_value, list):
        return []

    values = []

    for item in base_value:
        current = item

        for part in suffix_path.split("."):
            if not isinstance(current, dict):
                current = None
                break

            real_key = next(
                (
                    key
                    for key in current
                    if key.casefold() == part.casefold()
                ),
                None,
            )

            if real_key is None:
                current = None
                break

            current = current[real_key]

        if isinstance(current, (str, int, float, bool)):
            value = str(current)

            if value not in values:
                values.append(value)

    return values


def resolve_iterator_member_expr(
    expr,
    iterator_paths,
    pro_config,
    uc_config,
):
    """
    Resuelve expresiones como:
      v.UrlWithSas.scope
    cuando v itera una lista de parsedConfiguration.
    """
    expr = clean(expr)

    match = re.fullmatch(
        r'([A-Za-z_][A-Za-z0-9_]*)'
        r'\.((?:[A-Za-z_][A-Za-z0-9_]*\.?)+)',
        expr,
    )

    if not match:
        return None

    variable = match.group(1)
    suffix = match.group(2).rstrip(".")

    base_path = iterator_paths.get(variable)

    if not base_path:
        return None

    pro_values = values_from_list_object_path(
        pro_config,
        base_path,
        suffix,
    )

    uc_values = values_from_list_object_path(
        uc_config,
        base_path,
        suffix,
    )

    return {
        "mode": "ITERATOR_CONFIG_LIST",
        "expr": expr,
        "config_path": f"{base_path}[*].{suffix}",
        "pro_value": unique_join(pro_values),
        "uc_value": unique_join(uc_values),
        "resolved_value": unique_join(pro_values),
    }


def resolve_secret_expr(
    expr,
    assignments,
    iterator_paths,
    pro_config,
    uc_config,
):
    iterator_result = resolve_iterator_member_expr(
        expr,
        iterator_paths,
        pro_config,
        uc_config,
    )

    if iterator_result is not None:
        return iterator_result

    return resolve_expr(
        expr,
        assignments,
        pro_config,
        uc_config,
    )

def infer_usage_context(code, start, end):
    context = normalize(code[max(0, start-300):min(len(code), end+300)])

    if any(x in context for x in ["jdbc:", ".jdbc(", "postgres", "sqlserver", "database.azure.com"]):
        return "JDBC_DATABASE"
    if any(x in context for x in ["account.key", "abfss://", "wasbs://", "dfs.core.windows.net", "blob.core.windows.net"]):
        return "AZURE_STORAGE"
    if any(x in context for x in ["eventhub", "kafka", "bootstrap.servers", "sasl"]):
        return "MESSAGING"
    if any(x in context for x in ["authorization", "bearer", "http", "token", "api"]):
        return "API_AUTH"

    return "UNKNOWN_REVIEW"


def build_row(notebook, jobs, cell_index, system, api_call, scope_info, key_info, vault_url, usage, source):
    backend = "UNKNOWN_FROM_CODE" if system == "DATABRICKS_SECRET_SCOPE" else "AZURE_KEY_VAULT"

    requires_review = "NO"
    if system == "DATABRICKS_SECRET_SCOPE":
        if not scope_info.get("resolved_value") or not key_info.get("resolved_value"):
            requires_review = "YES"
    elif not key_info.get("resolved_value"):
        requires_review = "YES"

    return {
        "job": unique_join(sorted(jobs, key=str.casefold)),
        "notebook": notebook,
        "cell": cell_index,
        "secret_system": system,
        "backend": backend,
        "api_call": api_call,
        "scope_expression": scope_info.get("expr",""),
        "scope_value_pro": scope_info.get("pro_value") or scope_info.get("resolved_value") or "",
        "scope_value_uc": scope_info.get("uc_value",""),
        "scope_config_path": scope_info.get("config_path",""),
        "key_expression": key_info.get("expr",""),
        "secret_key_pro": key_info.get("pro_value") or key_info.get("resolved_value") or "",
        "secret_key_uc": key_info.get("uc_value",""),
        "key_config_path": key_info.get("config_path",""),
        "vault_url": vault_url,
        "usage_context": usage,
        "resolution_mode": unique_join([scope_info.get("mode",""), key_info.get("mode","")]),
        "requires_review": requires_review,
        "source": source.replace("\n"," ").strip(),
    }


def main():
    for required in [JOB_INVENTORY_FILE, NOTEBOOK_INVENTORY_FILE]:
        if not required.exists():
            raise SystemExit(f"Falta archivo requerido: {required}")

    job_rows = read_csv(JOB_INVENTORY_FILE)
    notebook_rows = read_csv(NOTEBOOK_INVENTORY_FILE)
    pro_config = load_json(PRO_CONFIG_FILE)
    uc_config = load_json(UC_CONFIG_FILE)

    notebook_index = build_notebook_index(notebook_rows)
    job_notebooks = defaultdict(set)

    for row in job_rows:
        notebook = clean(row.get("notebook"))
        job = clean(row.get("job"))
        if notebook:
            if job:
                job_notebooks[notebook].add(job)
            else:
                job_notebooks[notebook]

    output_rows = []
    dedupe = set()
    missing_notebooks = []

    dbutils_calls = ["dbutils.secrets.get", "dbutils.secrets.getBytes"]
    vault_url_re = re.compile(r'(?i)https://[a-z0-9-]+\.vault\.azure\.net/?')
    secret_client_re = re.compile(r'(?i)\bSecretClient\s*\(')

    for notebook, jobs in sorted(job_notebooks.items(), key=lambda x: normalize(x[0])):
        path = notebook_index.get(normalize(notebook))
        if path is None or not path.exists():
            missing_notebooks.append(notebook)
            continue

        for cell_index, block in enumerate(get_code_blocks(path), start=1):
            code = remove_comments(block)
            assignments = find_assignments(code)
            iterator_paths = find_iterator_config_paths(code)

            for call in find_balanced_calls(code, dbutils_calls):
                args = parse_call_arguments(call["arguments"])
                scope_expr = key_expr = ""
                positional = []

                for arg in args:
                    if "=" in arg:
                        name, value = arg.split("=", 1)
                        name = clean(name).casefold()
                        if name == "scope":
                            scope_expr = clean(value)
                        elif name == "key":
                            key_expr = clean(value)
                    else:
                        positional.append(arg)

                if not scope_expr and len(positional) >= 1:
                    scope_expr = positional[0]
                if not key_expr and len(positional) >= 2:
                    key_expr = positional[1]

                scope_info = resolve_secret_expr(
                    scope_expr,
                    assignments,
                    iterator_paths,
                    pro_config,
                    uc_config,
                )
                key_info = resolve_secret_expr(
                    key_expr,
                    assignments,
                    iterator_paths,
                    pro_config,
                    uc_config,
                )
                usage = infer_usage_context(code, call["start"], call["end"])

                row = build_row(
                    notebook, jobs, cell_index,
                    "DATABRICKS_SECRET_SCOPE",
                    call["call_name"],
                    scope_info, key_info, "", usage, call["source"]
                )

                key = (
                    normalize(notebook), cell_index,
                    normalize(row["api_call"]),
                    normalize(row["scope_expression"]),
                    normalize(row["key_expression"]),
                )
                if key not in dedupe:
                    dedupe.add(key)
                    output_rows.append(row)

            vault_urls = unique_join(m.group(0) for m in vault_url_re.finditer(code))
            if secret_client_re.search(code) or vault_urls:
                for call in find_balanced_calls(code, [".get_secret", ".getSecret"]):
                    args = parse_call_arguments(call["arguments"])
                    key_expr = args[0] if args else ""
                    key_info = resolve_secret_expr(
                        key_expr,
                        assignments,
                        iterator_paths,
                        pro_config,
                        uc_config,
                    )
                    usage = infer_usage_context(code, call["start"], call["end"])

                    empty_scope = {"mode":"","expr":"","config_path":"","pro_value":"","uc_value":"","resolved_value":""}
                    row = build_row(
                        notebook, jobs, cell_index,
                        "AZURE_KEY_VAULT_DIRECT",
                        call["call_name"],
                        empty_scope, key_info, vault_urls, usage, call["source"]
                    )

                    key = (
                        normalize(notebook), cell_index,
                        normalize(row["api_call"]),
                        normalize(row["vault_url"]),
                        normalize(row["key_expression"]),
                    )
                    if key not in dedupe:
                        dedupe.add(key)
                        output_rows.append(row)

    output_rows.sort(key=lambda r: (
        1 if r["secret_system"] == "DATABRICKS_SECRET_SCOPE" else 2,
        normalize(r["notebook"]),
        int(r["cell"]),
        normalize(r["scope_value_pro"]),
        normalize(r["secret_key_pro"]),
    ))

    fieldnames = [
        "job","notebook","cell","secret_system","backend","api_call",
        "scope_expression","scope_value_pro","scope_value_uc","scope_config_path",
        "key_expression","secret_key_pro","secret_key_uc","key_config_path",
        "vault_url","usage_context","resolution_mode","requires_review","source"
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    system_counter = Counter(r["secret_system"] for r in output_rows)
    backend_counter = Counter(r["backend"] for r in output_rows)
    usage_counter = Counter(r["usage_context"] for r in output_rows)
    review_counter = Counter(r["requires_review"] for r in output_rows)

    unique_scopes = {normalize(r["scope_value_pro"]) for r in output_rows if r["scope_value_pro"]}
    unique_pairs = {
        (normalize(r["scope_value_pro"]), normalize(r["secret_key_pro"]))
        for r in output_rows if r["secret_key_pro"]
    }

    print("="*72)
    print("ASSESSMENT WORKSPACE - PASO 17")
    print("ANALISIS DE USO DE SECRETS")
    print("="*72)
    print()
    print(f"Notebooks de alcance             : {len(job_notebooks)}")
    print(f"Notebooks faltantes en snapshot  : {len(missing_notebooks)}")
    print(f"Referencias a secrets detectadas : {len(output_rows)}")
    print(f"Scopes Databricks únicos         : {len(unique_scopes)}")
    print(f"Combinaciones scope/key únicas   : {len(unique_pairs)}")
    print()

    print("Resumen por sistema:")
    for k in sorted(system_counter):
        print(f" - {k:<34}: {system_counter[k]}")

    print()
    print("Backend:")
    for k in sorted(backend_counter):
        print(f" - {k:<34}: {backend_counter[k]}")

    print()
    print("Uso probable:")
    for k in sorted(usage_counter):
        print(f" - {k:<34}: {usage_counter[k]}")

    print()
    print("Revisión requerida:")
    for k in sorted(review_counter):
        print(f" - {k:<34}: {review_counter[k]}")

    print()
    print("NOTA: dbutils.secrets.get no permite determinar por código si el scope")
    print("es Databricks-backed o Azure Key Vault-backed; requiere metadata del Workspace.")
    print()
    print(f"Archivo generado: {OUTPUT_FILE}")
    print("="*72)


if __name__ == "__main__":
    main()
