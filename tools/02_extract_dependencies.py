from __future__ import annotations

import csv
import posixpath
import re
import shlex
import sys
from collections import Counter
from pathlib import Path


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

OUTPUT_DIR = PROJECT_ROOT / "output"

NOTEBOOK_INVENTORY_FILE = (
    OUTPUT_DIR / "notebooks.csv"
)

OUTPUT_FILE = (
    OUTPUT_DIR / "notebook_dependencies.csv"
)


# ============================================================
# CONSTANTES
# ============================================================

NOTEBOOK_EXTENSIONS = {
    ".scala",
    ".py",
    ".python",
    ".sql",
    ".r",
    ".ipynb",
}

# Separador utilizado por notebooks exportados como Databricks Source.
COMMAND_SEPARATOR_RE = re.compile(
    r"^\s*(?://|#|--)?\s*COMMAND\s+-{5,}\s*$",
    re.IGNORECASE,
)

# %run activo:
#
#   %run ./Utils
#   // MAGIC %run ./Utils
#   # MAGIC %run ./Utils
#   -- MAGIC %run ./Utils
#
PERCENT_RUN_RE = re.compile(
    r"^\s*(?:(?://|#|--)\s*MAGIC\s+)?%run\s+(.+?)\s*$",
    re.IGNORECASE,
)

# dbutils.notebook.run("ruta", ...)
# Puede estar distribuido en varias líneas.
NOTEBOOK_RUN_RE = re.compile(
    r"""
    dbutils
    \s*\.\s*
    notebook
    \s*\.\s*
    run
    \s*\(
    \s*
    (?P<quote>["'])
    (?P<target>.*?)
    (?P=quote)
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


# ============================================================
# UTILIDADES GENERALES
# ============================================================

def load_notebook_inventory() -> list[dict]:
    """
    Lee output/notebooks.csv generado por el Paso 01.
    """

    if not NOTEBOOK_INVENTORY_FILE.exists():
        raise FileNotFoundError(
            "No existe output/notebooks.csv.\n"
            "Ejecuta primero:\n"
            "  python tools/01_inventory.py"
        )

    with NOTEBOOK_INVENTORY_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as csvfile:
        return list(
            csv.DictReader(csvfile)
        )


def project_path(relative_path: str) -> Path:
    """
    Convierte una ruta portable del CSV a Path local.
    """

    normalized = (
        str(relative_path)
        .replace("\\", "/")
        .strip()
    )

    return (
        PROJECT_ROOT
        / Path(normalized)
    )


def normalize_workspace_path(value: str) -> str:
    """
    Normaliza una ruta lógica de Databricks Workspace.

    La salida siempre:
      - usa "/"
      - comienza por "/Workspace"
      - no termina en "/"
      - elimina extensiones de archivo conocidas

    Ejemplos:

        /Workspace/Oro/Utils
            -> /Workspace/Oro/Utils

        /Oro/Utils
            -> /Workspace/Oro/Utils

        Workspace/Oro/Utils.scala
            -> /Workspace/Oro/Utils
    """

    value = (
        str(value or "")
        .strip()
        .replace("\\", "/")
    )

    if not value:
        return ""

    # Quitar comillas envolventes si existen.
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1].strip()

    # Convertir cualquier forma absoluta a /Workspace/...
    if value == "Workspace":
        value = "/Workspace"

    elif value.startswith("Workspace/"):
        value = "/" + value

    elif value == "/":
        value = "/Workspace"

    elif value.startswith("/") and not value.startswith("/Workspace"):
        value = "/Workspace" + value

    elif not value.startswith("/"):
        value = "/" + value

    # Normalización POSIX; Databricks utiliza "/".
    value = posixpath.normpath(value)

    if value == ".":
        return ""

    # Eliminar extensión conocida del último componente.
    suffix = Path(value).suffix.lower()

    if suffix in NOTEBOOK_EXTENSIONS:
        value = value[: -len(suffix)]

    # Evitar slash final salvo /Workspace.
    if value != "/Workspace":
        value = value.rstrip("/")

    return value


def source_parent(workspace_path: str) -> str:
    """
    Directorio Workspace del notebook origen.
    """

    normalized = normalize_workspace_path(
        workspace_path
    )

    return posixpath.dirname(normalized)


def parse_percent_run_target(raw_value: str) -> str:
    """
    Obtiene únicamente la referencia al notebook desde una
    instrucción %run.

    Soporta, por ejemplo:

        %run ./Utils
        %run "../Mi Carpeta/Utils"
        %run ./Utils $param="valor"
    """

    raw_value = (
        str(raw_value or "")
        .strip()
    )

    if not raw_value:
        return ""

    try:
        tokens = shlex.split(
            raw_value,
            posix=True,
        )

        if tokens:
            return tokens[0].strip()

    except ValueError:
        # Si hay comillas incompletas, conservar un fallback
        # sencillo sin interrumpir todo el análisis.
        pass

    return raw_value.split()[0].strip()


def resolve_reference_to_candidate(
    source_workspace_path: str,
    target_reference: str,
) -> str:
    """
    Convierte una referencia encontrada en código a una ruta
    candidata absoluta de Workspace.

    Reglas Databricks:

      ./Hijo
      ../Compartido/Utils
      Hijo

    son relativas al directorio del notebook origen.

      /Workspace/Oro/Hijo
      /Oro/Hijo

    se consideran absolutas.
    """

    target_reference = (
        str(target_reference or "")
        .strip()
        .replace("\\", "/")
    )

    if not target_reference:
        return ""

    # Quitar extensión si vino escrita explícitamente.
    suffix = Path(target_reference).suffix.lower()

    if suffix in NOTEBOOK_EXTENSIONS:
        target_reference = (
            target_reference[: -len(suffix)]
        )

    if (
        target_reference.startswith("/Workspace")
        or target_reference.startswith("/")
        or target_reference.startswith("Workspace/")
    ):
        return normalize_workspace_path(
            target_reference
        )

    parent = source_parent(
        source_workspace_path
    )

    combined = posixpath.normpath(
        posixpath.join(
            parent,
            target_reference,
        )
    )

    return normalize_workspace_path(
        combined
    )


# ============================================================
# PREPROCESAMIENTO DE CÓDIGO
# ============================================================

def split_databricks_cells(
    content: str,
) -> list[str]:
    """
    Divide un notebook Databricks Source por separadores
    COMMAND ----------.

    El número de celda que se exporta al CSV es 1-based.
    """

    cells: list[list[str]] = [[]]

    for line in content.splitlines():

        if COMMAND_SEPARATOR_RE.match(line):
            cells.append([])
            continue

        cells[-1].append(line)

    return [
        "\n".join(lines)
        for lines in cells
    ]


def mask_block_comments(text: str) -> str:
    """
    Sustituye comentarios /* ... */ por espacios preservando
    saltos de línea.

    Esto evita detectar dependencias deshabilitadas sin alterar
    demasiado la estructura del texto.
    """

    def replacer(match: re.Match) -> str:
        value = match.group(0)

        return "".join(
            "\n" if char == "\n" else " "
            for char in value
        )

    return re.sub(
        r"/\*.*?\*/",
        replacer,
        text,
        flags=re.DOTALL,
    )


def remove_comment_only_lines(
    text: str,
    *,
    preserve_magic_percent_run: bool = False,
) -> str:
    """
    Elimina líneas completamente comentadas para los lenguajes
    válidos del inventario:

      Scala / Java : //
      Python       : #
      SQL          : --

    También reconoce el formato Databricks Source para MAGIC:

      // MAGIC %run ./Utils
      # MAGIC %run ./Utils
      -- MAGIC %run ./Utils

    Cuando preserve_magic_percent_run=True esas líneas se
    conservan porque representan una instrucción %run ACTIVA
    exportada por Databricks.

    En cambio se eliminan variantes deshabilitadas como:

      //%run ./Utils
      // %run ./Utils
      # %run ./Utils
      -- %run ./Utils
      // dbutils.notebook.run(...)
      # dbutils.notebook.run(...)
      -- dbutils.notebook.run(...)
    """

    result = []

    for line in text.splitlines():

        stripped = line.lstrip()

        magic_match = re.match(
            r"^(?://|#|--)\s+MAGIC\s+%run\b",
            stripped,
            flags=re.IGNORECASE,
        )

        if (
            preserve_magic_percent_run
            and magic_match
        ):
            result.append(line)
            continue

        if (
            stripped.startswith("//")
            or stripped.startswith("#")
            or stripped.startswith("--")
        ):
            result.append("")
            continue

        result.append(line)

    return "\n".join(result)


# ============================================================
# EXTRACCIÓN DE DEPENDENCIAS
# ============================================================

def extract_percent_runs(
    cell_content: str,
) -> list[str]:
    """
    Extrae referencias %run activas de una celda.
    """

    references = []

    cleaned = mask_block_comments(
        cell_content
    )

    cleaned = remove_comment_only_lines(
        cleaned,
        preserve_magic_percent_run=True,
    )

    for line in cleaned.splitlines():

        match = PERCENT_RUN_RE.match(
            line
        )

        if not match:
            continue

        target = parse_percent_run_target(
            match.group(1)
        )

        if target:
            references.append(
                target
            )

    return references


def extract_notebook_runs(
    cell_content: str,
) -> list[str]:
    """
    Extrae referencias literales de dbutils.notebook.run.

    Solo se inventarían relaciones falsas si intentáramos
    resolver expresiones dinámicas. Por eso aquí únicamente
    capturamos el primer argumento cuando es String literal.
    """

    cleaned = mask_block_comments(
        cell_content
    )

    cleaned = remove_comment_only_lines(
        cleaned,
        preserve_magic_percent_run=False,
    )

    return [
        match.group("target").strip()
        for match in NOTEBOOK_RUN_RE.finditer(
            cleaned
        )
        if match.group("target").strip()
    ]


# ============================================================
# RESOLUCIÓN CONTRA INVENTARIO REAL
# ============================================================

def build_workspace_index(
    notebooks: list[dict],
) -> dict[str, str]:
    """
    Construye el índice estricto de notebooks del Workspace.

    Regla del Assessment 2:
        ruta lógica completa exacta -> notebook real

    No se busca por basename.
    No se busca en otra carpeta.
    No se aplica fallback case-insensitive.
    """

    exact_index: dict[str, str] = {}

    for row in notebooks:

        workspace_path = (
            row.get("workspace_path")
            or ""
        ).strip()

        if not workspace_path:
            continue

        canonical = normalize_workspace_path(
            workspace_path
        )

        exact_index[
            canonical
        ] = workspace_path

    return exact_index


def resolve_target(
    source_workspace_path: str,
    target_reference: str,
    exact_index: dict[str, str],
) -> tuple[str, str]:
    """
    Devuelve:

        resolved_target, status

    Estados:
        RESOLVED
        NOT_FOUND
        AMBIGUOUS
    """

    candidate = resolve_reference_to_candidate(
        source_workspace_path,
        target_reference,
    )

    if not candidate:
        return "", "NOT_FOUND"

    # Coincidencia estricta por ruta lógica completa.
    if candidate in exact_index:
        return (
            exact_index[candidate],
            "RESOLVED",
        )

    return (
        "",
        "NOT_FOUND",
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print("=" * 70)
    print(
        "ASSESSMENT WORKSPACE - PASO 02"
    )
    print(
        "EXTRACCIÓN DE DEPENDENCIAS ENTRE NOTEBOOKS"
    )
    print("=" * 70)

    try:

        notebooks = load_notebook_inventory()

        exact_index = build_workspace_index(
            notebooks
        )

        dependencies = []

        relationship_counter = Counter()
        status_counter = Counter()

        processed = 0
        missing_local_files = []

        # Para evitar relaciones duplicadas exactamente iguales.
        seen = set()

        for row in notebooks:

            workspace_path = (
                row.get("workspace_path")
                or ""
            ).strip()

            local_file_value = (
                row.get("local_file")
                or row.get("path")
                or ""
            ).strip()

            if (
                not workspace_path
                or not local_file_value
            ):
                continue

            local_file = project_path(
                local_file_value
            )

            if not local_file.exists():

                missing_local_files.append(
                    {
                        "workspace_path":
                            workspace_path,
                        "local_file":
                            local_file,
                    }
                )

                continue

            content = local_file.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            cells = split_databricks_cells(
                content
            )

            processed += 1

            for cell_number, cell in enumerate(
                cells,
                start=1,
            ):

                # ============================================
                # %run
                # ============================================

                for target_reference in extract_percent_runs(
                    cell
                ):

                    (
                        resolved_target,
                        status,
                    ) = resolve_target(
                        source_workspace_path=workspace_path,
                        target_reference=target_reference,
                        exact_index=exact_index,
                    )

                    key = (
                        workspace_path,
                        cell_number,
                        "PERCENT_RUN",
                        target_reference,
                        resolved_target,
                        status,
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    dependencies.append(
                        {
                            "source_notebook":
                                workspace_path,
                            "cell":
                                cell_number,
                            "relationship":
                                "PERCENT_RUN",
                            "target_reference":
                                target_reference,
                            "resolved_target":
                                resolved_target,
                            "status":
                                status,
                        }
                    )

                    relationship_counter[
                        "PERCENT_RUN"
                    ] += 1

                    status_counter[
                        status
                    ] += 1

                # ============================================
                # dbutils.notebook.run
                # ============================================

                for target_reference in extract_notebook_runs(
                    cell
                ):

                    (
                        resolved_target,
                        status,
                    ) = resolve_target(
                        source_workspace_path=workspace_path,
                        target_reference=target_reference,
                        exact_index=exact_index,
                    )

                    key = (
                        workspace_path,
                        cell_number,
                        "NOTEBOOK_RUN",
                        target_reference,
                        resolved_target,
                        status,
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    dependencies.append(
                        {
                            "source_notebook":
                                workspace_path,
                            "cell":
                                cell_number,
                            "relationship":
                                "NOTEBOOK_RUN",
                            "target_reference":
                                target_reference,
                            "resolved_target":
                                resolved_target,
                            "status":
                                status,
                        }
                    )

                    relationship_counter[
                        "NOTEBOOK_RUN"
                    ] += 1

                    status_counter[
                        status
                    ] += 1

        # ====================================================
        # ORDEN
        # ====================================================

        dependencies.sort(
            key=lambda row: (
                row["source_notebook"].casefold(),
                int(row["cell"]),
                row["relationship"],
                row["target_reference"].casefold(),
            )
        )

        # ====================================================
        # GENERAR CSV
        # ====================================================

        OUTPUT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        fieldnames = [
            "source_notebook",
            "cell",
            "relationship",
            "target_reference",
            "resolved_target",
            "status",
        ]

        with OUTPUT_FILE.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as csvfile:

            writer = csv.DictWriter(
                csvfile,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(
                dependencies
            )

        # ====================================================
        # RESUMEN
        # ====================================================

        print()
        print(
            f"Notebooks inventariados    : "
            f"{len(notebooks)}"
        )

        print(
            f"Notebooks procesados       : "
            f"{processed}"
        )

        print(
            f"Archivos locales faltantes : "
            f"{len(missing_local_files)}"
        )

        print()
        print(
            f"Dependencias detectadas    : "
            f"{len(dependencies)}"
        )

        print()
        print(
            "Resumen por relación:"
        )

        if relationship_counter:

            for (
                relationship,
                count,
            ) in sorted(
                relationship_counter.items()
            ):

                print(
                    f" - "
                    f"{relationship:<15}: "
                    f"{count}"
                )

        else:

            print(
                " - Sin dependencias detectadas"
            )

        print()
        print(
            "Resumen por estado:"
        )

        for status in [
            "RESOLVED",
            "NOT_FOUND",
            "AMBIGUOUS",
        ]:

            print(
                f" - "
                f"{status:<12}: "
                f"{status_counter.get(status, 0)}"
            )

        # Mostrar pendientes sin abortar la corrida.
        unresolved = [
            row
            for row in dependencies
            if row["status"] != "RESOLVED"
        ]

        if unresolved:

            print()
            print(
                "Referencias no resueltas "
                "(primeras 25):"
            )

            for item in unresolved[:25]:

                print(
                    f" - "
                    f"{item['source_notebook']}"
                )

                print(
                    f"   celda        : "
                    f"{item['cell']}"
                )

                print(
                    f"   relación     : "
                    f"{item['relationship']}"
                )

                print(
                    f"   referencia   : "
                    f"{item['target_reference']}"
                )

                print(
                    f"   estado       : "
                    f"{item['status']}"
                )

            if len(unresolved) > 25:

                print(
                    f" ... y "
                    f"{len(unresolved) - 25} "
                    f"más"
                )

        if missing_local_files:

            print()
            print(
                "Archivos locales faltantes "
                "(primeros 20):"
            )

            for item in missing_local_files[:20]:

                print(
                    f" - "
                    f"{item['workspace_path']}"
                )

                print(
                    f"   {item['local_file']}"
                )

        print()
        print(
            f"Archivo generado: "
            f"{OUTPUT_FILE}"
        )

        print(
            f"Registros generados: "
            f"{len(dependencies)}"
        )

        print()
        print("=" * 70)

        if (
            missing_local_files
            or unresolved
        ):

            print(
                "RESULTADO: COMPLETADO "
                "CON REFERENCIAS PARA REVISIÓN"
            )

        else:

            print(
                "RESULTADO: "
                "DEPENDENCIAS GENERADAS "
                "CORRECTAMENTE"
            )

        print("=" * 70)
        print()

        return 0

    except Exception as exc:

        print()
        print("=" * 70)
        print(
            "ERROR - PASO 02"
        )
        print("=" * 70)

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        print("=" * 70)
        print()

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )