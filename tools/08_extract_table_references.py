from pathlib import Path
from collections import defaultdict
import csv
import json
import re


# --------------------------------------------------
# Archivos
# --------------------------------------------------

reachability_file = Path("output/notebook_reachability.csv")
job_inventory_file = Path("output/job_notebook_inventory.csv")
inventory_file = Path("output/notebooks.csv")

output_dir = Path("output")
output_dir.mkdir(exist_ok=True)

output_file = output_dir / "table_references.csv"


# --------------------------------------------------
# Patrones explícitos de tablas
# --------------------------------------------------

PATTERNS = {
    "SPARK_TABLE": re.compile(
        r'(?i)spark\.table\s*\(\s*["\']([^"\']+)["\']\s*\)'
    ),

    "READ_TABLE": re.compile(
        r'(?i)spark\.read\.table\s*\(\s*["\']([^"\']+)["\']\s*\)'
    ),

    "SAVE_AS_TABLE": re.compile(
        r'(?i)saveAsTable\s*\(\s*["\']([^"\']+)["\']\s*\)'
    ),

    "INSERT_INTO": re.compile(
        r'(?i)insertInto\s*\(\s*["\']([^"\']+)["\']\s*\)'
    ),
}

TEMP_VIEW_PATTERN = re.compile(
    r'''(?ix)
    \.
    (
        createOrReplaceTempView
        |createTempView
        |createOrReplaceGlobalTempView
        |createGlobalTempView
    )
    \s*
    \(
    \s*
    ["']([^"']+)["']
    \s*
    \)
    '''
)

# --------------------------------------------------
# SQL keywords que pueden referenciar tablas
# --------------------------------------------------

SQL_TABLE_PATTERN = re.compile(
    r'''(?ix)
    \b(
        from
        |join
        |into
        |update
        |merge\s+into
        |delete\s+from
        |truncate\s+table
        |create\s+table(?:\s+if\s+not\s+exists)?
        |alter\s+table
        |drop\s+table(?:\s+if\s+exists)?
        |optimize
    )
    \s+
    (
        [A-Za-z0-9_`.-]*\$\{[A-Za-z_]\w*\}[A-Za-z0-9_`.-]*
        |
        `?[\w.-]+`?(?:\.`?[\w.-]+`?){0,2}
    )
    '''
)


# --------------------------------------------------
# 1. Notebooks utilizados
# --------------------------------------------------

used_notebooks = set()

with open(
    reachability_file,
    "r",
    encoding="utf-8-sig"
) as csvfile:

    reader = csv.DictReader(csvfile)

    for row in reader:

        if row["status"] in {
            "ROOT",
            "REACHABLE"
        }:
            used_notebooks.add(
                row["notebook"].strip()
            )


# --------------------------------------------------
# 2. Jobs asociados por notebook
# --------------------------------------------------

notebook_jobs = defaultdict(set)

# --------------------------------------------------
# Lenguaje de cada notebook
# --------------------------------------------------

notebook_languages = {}
notebook_local_paths = {}

with open(
    inventory_file,
    "r",
    encoding="utf-8-sig"
) as csvfile:

    reader = csv.DictReader(csvfile)

    for row in reader:

        workspace_path = (row.get("workspace_path") or row.get("path") or "").strip()
        local_file = (row.get("local_file") or row.get("path") or "").strip()

        if workspace_path:
            notebook_languages[workspace_path] = row["language"].strip().lower()

        if workspace_path and local_file:
            notebook_local_paths[workspace_path] = Path(local_file)

with open(
    job_inventory_file,
    "r",
    encoding="utf-8-sig"
) as csvfile:

    reader = csv.DictReader(csvfile)

    for row in reader:

        notebook = row["notebook"].strip()
        job = row["job"].strip()

        notebook_jobs[notebook].add(job)


# --------------------------------------------------
# 3. Lectura de bloques/celdas
# --------------------------------------------------

def get_code_blocks(notebook_path):

    if notebook_path.suffix.lower() == ".ipynb":

        with open(
            notebook_path,
            "r",
            encoding="utf-8"
        ) as f:
            notebook = json.load(f)

        blocks = []

        for cell in notebook.get("cells", []):

            if cell.get("cell_type") != "code":
                continue

            source = cell.get("source", [])

            if isinstance(source, list):
                code = "".join(source)
            else:
                code = source

            blocks.append(code)

        return blocks

    # --------------------------------------------------
    # Databricks Source
    # --------------------------------------------------

    content = notebook_path.read_text(
        encoding="utf-8",
        errors="ignore"
    )

    blocks = re.split(
        r'(?:\/\/|#|--)\s*COMMAND\s*-+',
        content
    )

    normalized_blocks = []

    for block in blocks:

        normalized_lines = []

        for line in block.splitlines():

            # ------------------------------------------
            # Databricks MAGIC
            #
            # Ejemplo:
            # // MAGIC %sql
            # // MAGIC OPTIMIZE tabla;
            #
            # Se convierte en:
            # %sql
            # OPTIMIZE tabla;
            # ------------------------------------------

            magic_match = re.match(
                r'^\s*(?://|#|--)\s*MAGIC\s?(.*)$',
                line,
                flags=re.IGNORECASE
            )

            if magic_match:
                normalized_lines.append(
                    magic_match.group(1)
                )
            else:
                normalized_lines.append(line)

        normalized_blocks.append(
            "\n".join(normalized_lines)
        )

    return normalized_blocks


# --------------------------------------------------
# 4. Eliminar comentarios SIN IMPORTAR LENGUAJE
#
# Siempre se excluyen: //, /* ... */, # y --
# Los marcadores dentro de strings se conservan.
# Databricks MAGIC se normaliza previamente como código activo.
# --------------------------------------------------

def remove_comments(code):

    result = []

    i = 0
    length = len(code)

    in_single_quote = False
    in_double_quote = False
    in_triple_double = False
    in_block_comment = False

    while i < length:

        # ------------------------------------------
        # Dentro de comentario /* ... */
        # ------------------------------------------

        if in_block_comment:

            if code[i:i + 2] == "*/":
                in_block_comment = False
                i += 2
            else:
                i += 1

            continue

        # ------------------------------------------
        # Triple comilla """
        # Importante para spark.sql(""" ... """)
        # ------------------------------------------

        if (
            not in_single_quote
            and not in_double_quote
            and code[i:i + 3] == '"""'
        ):

            in_triple_double = not in_triple_double

            result.append('"""')
            i += 3
            continue

        if in_triple_double:
            result.append(code[i])
            i += 1
            continue

        # ------------------------------------------
        # String con "
        # ------------------------------------------

        if (
            code[i] == '"'
            and not in_single_quote
        ):

            escaped = (
                i > 0
                and code[i - 1] == "\\"
            )

            if not escaped:
                in_double_quote = not in_double_quote

            result.append(code[i])
            i += 1
            continue

        # ------------------------------------------
        # String con '
        # ------------------------------------------

        if (
            code[i] == "'"
            and not in_double_quote
        ):

            escaped = (
                i > 0
                and code[i - 1] == "\\"
            )

            if not escaped:
                in_single_quote = not in_single_quote

            result.append(code[i])
            i += 1
            continue

        # ------------------------------------------
        # Detectar comentarios solo fuera de strings
        # ------------------------------------------

        if not in_single_quote and not in_double_quote:

            # /* ... */
            if code[i:i + 2] == "/*":
                in_block_comment = True
                i += 2
                continue

            # // Scala
            if code[i:i + 2] == "//":

                while (
                    i < length
                    and code[i] != "\n"
                ):
                    i += 1

                continue

            # -- SQL
            if code[i:i + 2] == "--":

                while (
                    i < length
                    and code[i] != "\n"
                ):
                    i += 1

                continue

            # # Python
            if code[i] == "#":

                while (
                    i < length
                    and code[i] != "\n"
                ):
                    i += 1

                continue

        result.append(code[i])
        i += 1

    return "".join(result)


#Limpiar comentarios SQL dentro de bloques """ ... """
def remove_sql_comments_from_triple_strings(code):
    """
    Elimina comentarios SQL dentro de bloques de triple comilla,
    normalmente utilizados por spark.sql(\"\"\" ... \"\"\").

    Elimina:
      -- comentario
      /* comentario */
    """

    triple_pattern = re.compile(
        r'"""(.*?)"""',
        flags=re.DOTALL
    )

    def clean_sql_block(match):

        sql = match.group(1)

        # Comentarios /* ... */
        sql = re.sub(
            r'/\*.*?\*/',
            '',
            sql,
            flags=re.DOTALL
        )

        # Comentarios -- hasta fin de línea
        sql = re.sub(
            r'--[^\n\r]*',
            '',
            sql
        )

        return f'"""{sql}"""'

    return triple_pattern.sub(
        clean_sql_block,
        code
    )


def looks_like_sql(text):
    """
    Determina si un bloque de texto parece contener
    una sentencia SQL real.
    """

    sql = text.strip()

    if not sql:
        return False

    sql_indicators = [
        r'\bselect\b.+\bfrom\b',
        r'\bupdate\b.+\bset\b',
        r'\bdelete\s+from\b',
        r'\binsert\s+into\b',
        r'\bmerge\s+into\b',
        r'\bcreate\s+table\b',
        r'\btruncate\s+table\b',
        r'\balter\s+table\b',
        r'\bdrop\s+table\b'
    ]

    for pattern in sql_indicators:

        if re.search(
            pattern,
            sql,
            flags=re.IGNORECASE | re.DOTALL
        ):
            return True

    return False


def extract_sql_blocks(code):
    """
    Extrae bloques SQL reales.

    Soporta:
      spark.sql("SELECT ...")
      spark.sql(" ... ")
      spark.sql(s" ... ")
      val query = " ... "
      val query = s" ... "

      Con una o triple comilla
    Solo conserva textos que parezcan SQL real.
    """

    sql_blocks = []

    # --------------------------------------------------
    # 1. spark.sql("...")
    # --------------------------------------------------

    single_line_pattern = re.compile(
        r'''(?is)
        spark\.sql
        \s*
        \(
        \s*
        s?
        ["']
        (.*?)
        ["']
        \s*
        \)
        ''',
        re.VERBOSE
    )

    for match in single_line_pattern.finditer(code):

        candidate = match.group(1)

        if looks_like_sql(candidate):
            sql_blocks.append(candidate)

    # --------------------------------------------------
    # 2. Strings multilínea """
    # --------------------------------------------------

    triple_string_pattern = re.compile(
        r'''(?is)
        s?
        """
        (.*?)
        """
        ''',
        re.VERBOSE
    )

    for match in triple_string_pattern.finditer(code):

        candidate = match.group(1)

        if looks_like_sql(candidate):
            sql_blocks.append(candidate)

    return sql_blocks

# --------------------------------------------------
# 5. Clasificar formato de referencia
# --------------------------------------------------

def classify_table_reference(
    table_reference,
    temp_views
):

    clean_reference = table_reference.strip()

    # Toda la referencia viene de una variable dinámica.
    # Ejemplo:
    # ${TablaFaltantes}
    if re.fullmatch(
        r'\$\{[A-Za-z_]\w*\}',
        clean_reference
    ):
        return "DYNAMIC_VARIABLE"

    # Referencia mixta:
    # parte fija + variable dinámica.
    # Ejemplos:
    # default.${nombreTablaSinBaseDeDatos}
    # schema.${tabla}
    # catalog.schema.${tabla}
    if "${" in clean_reference:
        return "DYNAMIC_TABLE_EXPRESSION"

    clean = (
        table_reference
        .replace("`", "")
        .strip()
    )

    # Vista temporal conocida dentro del notebook
    if clean.lower() in temp_views:
        return "TEMP_VIEW"

    parts = clean.split(".")

    if clean.lower().startswith(
        "hive_metastore."
    ):
        return "HIVE_METASTORE"

    if len(parts) == 3:
        return "THREE_PART_NAME"

    if len(parts) == 2:
        return "TWO_PART_NAME"

    if len(parts) == 1:
        return "ONE_PART_NAME"

    return "UNKNOWN"


# --------------------------------------------------
# 6. Analizar notebooks
# --------------------------------------------------

rows = []

for notebook in sorted(used_notebooks):

    notebook_path = notebook_local_paths.get(notebook)

    if notebook_path is None or not notebook_path.exists():
        print(
            f"WARNING: notebook no encontrado: {notebook}"
        )
        continue

    try:

        blocks = get_code_blocks(
            notebook_path
        )

        temp_views = set()

        for block_index, original_code in enumerate(blocks):

            code = remove_comments(
                original_code
            )

            code = remove_sql_comments_from_triple_strings(
                code
            )

            # --------------------------------------
            # Detectar vistas temporales creadas
            # dentro del notebook
            # --------------------------------------

            for match in TEMP_VIEW_PATTERN.finditer(code):

                temp_view_name = match.group(2)

                temp_views.add(
                    temp_view_name.lower()
                )

            jobs = sorted(
                notebook_jobs.get(
                    notebook,
                    []
                )
            )

            jobs_value = " | ".join(jobs)

            # --------------------------------------
            # APIs Spark explícitas
            # --------------------------------------

            for reference_type, pattern in PATTERNS.items():

                for match in pattern.finditer(code):

                    table_reference = match.group(1)

                    rows.append({
                        "notebook": notebook,
                        "cell": block_index + 1,
                        "reference_type": reference_type,
                        "table_reference": table_reference,
                        "name_format": classify_table_reference(
                            table_reference,
                            temp_views
                        ),
                        "jobs": jobs_value
                    })

            # --------------------------------------
            # SQL embebido / SQL notebook
            # --------------------------------------

            # --------------------------------------
            # SQL
            # --------------------------------------

            language = notebook_languages.get(
                notebook,
                "unknown"
            )

            sql_blocks = []

            # --------------------------------------------------
            # Notebook SQL nativo
            # --------------------------------------------------

            if language == "sql":

                sql_blocks.append(code)

            # --------------------------------------------------
            # Databricks MAGIC %sql dentro de notebook
            # Scala / Python
            #
            # Ejemplo:
            # %sql
            # OPTIMIZE tabla;
            # --------------------------------------------------

            elif re.match(
                r'^\s*%sql\b',
                code,
                flags=re.IGNORECASE
            ):

                # Quitar la primera línea %sql
                sql_code = re.sub(
                    r'^\s*%sql\b[^\n\r]*',
                    '',
                    code,
                    count=1,
                    flags=re.IGNORECASE
                )

                sql_blocks.append(sql_code)

            # --------------------------------------------------
            # Scala / Python con SQL embebido
            # spark.sql(...)
            # strings SQL, etc.
            # --------------------------------------------------

            else:

                sql_blocks.extend(
                    extract_sql_blocks(code)
                )        


            for sql_code in sql_blocks:

                # Limpiar comentarios SQL dentro del bloque
                sql_code = re.sub(
                    r'/\*.*?\*/',
                    '',
                    sql_code,
                    flags=re.DOTALL
                )

                sql_code = re.sub(
                    r'--[^\n\r]*',
                    '',
                    sql_code
                )

                for match in SQL_TABLE_PATTERN.finditer(
                    sql_code
                ):

                    keyword = match.group(1)
                    table_reference = match.group(2)

                    rows.append({
                        "notebook": notebook,
                        "cell": block_index + 1,
                        "reference_type": (
                            "SQL_" +
                            keyword.upper()
                            .replace(" ", "_")
                        ),
                        "table_reference": table_reference,
                        "name_format": classify_table_reference(
                            table_reference,
                            temp_views
                        ),
                        "jobs": jobs_value
                    })

    except Exception as e:

        print(
            f"ERROR leyendo {notebook}: {e}"
        )


# --------------------------------------------------
# 7. Eliminar duplicados exactos
# --------------------------------------------------

unique_rows = []
seen = set()

for row in rows:

    key = (
        row["notebook"],
        row["cell"],
        row["reference_type"],
        row["table_reference"]
    )

    if key in seen:
        continue

    seen.add(key)
    unique_rows.append(row)

rows = unique_rows


# --------------------------------------------------
# 8. Ordenar
# --------------------------------------------------

rows.sort(
    key=lambda row: (
        row["notebook"].lower(),
        row["cell"],
        row["table_reference"].lower(),
        row["reference_type"]
    )
)


# --------------------------------------------------
# 9. Generar CSV
# --------------------------------------------------

with open(
    output_file,
    "w",
    newline="",
    encoding="utf-8-sig"
) as csvfile:

    fieldnames = [
        "notebook",
        "cell",
        "reference_type",
        "table_reference",
        "name_format",
        "jobs"
    ]

    writer = csv.DictWriter(
        csvfile,
        fieldnames=fieldnames
    )

    writer.writeheader()
    writer.writerows(rows)


# --------------------------------------------------
# 10. Resumen
# --------------------------------------------------

type_counts = defaultdict(int)
format_counts = defaultdict(int)

for row in rows:
    type_counts[row["reference_type"]] += 1
    format_counts[row["name_format"]] += 1


print("=" * 60)
print("ASSESSMENT WORKSPACE - PASO 08")
print("REFERENCIAS DE TABLAS - NOTEBOOKS UTILIZADOS")
print("=" * 60)

print()
print(
    f"Notebooks utilizados analizados : "
    f"{len(used_notebooks)}"
)

print(
    f"Referencias detectadas          : "
    f"{len(rows)}"
)

print()
print("Resumen por formato de nombre:")

for name_format in sorted(format_counts):
    print(
        f" - {name_format:<20}: "
        f"{format_counts[name_format]}"
    )

print()
print("Resumen por tipo de referencia:")

for reference_type in sorted(type_counts):
    print(
        f" - {reference_type:<25}: "
        f"{type_counts[reference_type]}"
    )

print()
print(
    f"Archivo generado: {output_file}"
)

print()
print("=" * 60)