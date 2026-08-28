from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SNAPSHOT_DIR = PROJECT_ROOT / "snapshot"
OUTPUT_DIR = PROJECT_ROOT / "output"

NOTEBOOK_INDEX_FILE = (
    SNAPSHOT_DIR / "notebooks_index.json"
)

OUTPUT_FILE = (
    OUTPUT_DIR / "notebooks.csv"
)


# ============================================================
# UTILIDADES
# ============================================================

def load_json(path: Path):
    """
    Lee un archivo JSON usando UTF-8.
    """

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def normalize_language(value: str | None) -> str:
    """
    Convierte los valores provenientes del SDK:

        Language.SCALA
        Language.PYTHON
        Language.SQL
        Language.R

    a:

        scala
        python
        sql
        r
    """

    if not value:
        return "unknown"

    value = str(value).strip()

    if "." in value:
        value = value.split(".")[-1]

    return value.lower()


def extension_from_language(
    language: str,
) -> str:
    """
    Fallback por si no podemos determinar la extensión
    desde el archivo físico.
    """

    mapping = {
        "scala": ".scala",
        "python": ".py",
        "sql": ".sql",
        "r": ".r",
    }

    return mapping.get(
        language,
        ".txt",
    )


def workspace_to_local_path(
    workspace_path: str,
    language: str,
) -> Path:
    """
    Convierte una ruta lógica de Workspace a la ruta local
    generada por el extractor.

    Ejemplo:

        /Workspace/Oro/Cedulas/Ejecutor

    se convierte en:

        snapshot/notebooks/Workspace/Oro/Cedulas/Ejecutor.scala
    """

    relative_workspace_path = (
        workspace_path
        .replace("\\", "/")
        .lstrip("/")
    )

    extension = extension_from_language(
        language
    )

    return (
        SNAPSHOT_DIR
        / "notebooks"
        / f"{relative_workspace_path}{extension}"
    )


def dbfs_file_to_local_path(
    dbfs_file: str | None,
    workspace_path: str,
    language: str,
) -> Path:
    """
    El notebooks_index.json contiene la ubicación original
    usada durante la extracción:

        /dbfs/FileStore/.../notebooks/Workspace/...

    Esa ruta no existe en la laptop.

    Para el Assessment utilizamos como verdad la parte de ruta
    relativa debajo de notebooks/.
    """

    if dbfs_file:

        normalized = (
            str(dbfs_file)
            .replace("\\", "/")
        )

        marker = "/notebooks/"

        position = normalized.find(
            marker
        )

        if position >= 0:

            relative = normalized[
                position + len(marker):
            ]

            candidate = (
                SNAPSHOT_DIR
                / "notebooks"
                / Path(relative)
            )

            if candidate.exists():
                return candidate

            # ------------------------------------------------
            # Fallback Windows:
            # Databricks puede permitir caracteres en nombres
            # que Windows no admite, por ejemplo ":".
            # Al extraer el ZIP esos caracteres pueden quedar
            # sustituidos por "_".
            # ------------------------------------------------

            sanitized_name = candidate.name

            for char in '<>:"|?*':
                sanitized_name = sanitized_name.replace(
                    char,
                    "_",
                )

            sanitized_candidate = (
                candidate.parent
                / sanitized_name
            )

            if sanitized_candidate.exists():
                return sanitized_candidate

            return candidate

    # Fallback usando workspace_path
    return workspace_to_local_path(
        workspace_path,
        language,
    )


def count_source_cells(
    local_file: Path,
) -> tuple[int, int, int]:
    """
    Cuenta celdas de un notebook exportado en formato
    Databricks Source.

    Mantiene el mismo criterio del Workflow 1:

        COMMAND ----------

    representa un separador de celdas.

    Además contamos las celdas MAGIC como Markdown cuando
    podemos identificarlas.
    """

    content = local_file.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    # Cada COMMAND separa una celda.
    total_cells = (
        content.count(
            "COMMAND ----------"
        )
        + 1
    )

    # --------------------------------------------------------
    # Identificación aproximada de Markdown Databricks Source
    #
    # Scala/Python:
    #   // MAGIC %md
    #   # MAGIC %md
    #
    # SQL:
    #   -- MAGIC %md
    # --------------------------------------------------------

    markdown_markers = [
        "// MAGIC %md",
        "# MAGIC %md",
        "-- MAGIC %md",
    ]

    markdown_cells = sum(
        content.count(marker)
        for marker in markdown_markers
    )

    # Evitar inconsistencias por casos extraños
    markdown_cells = min(
        markdown_cells,
        total_cells,
    )

    code_cells = (
        total_cells
        - markdown_cells
    )

    other_cells = 0

    return (
        code_cells,
        markdown_cells,
        other_cells,
    )


def notebook_name_from_workspace_path(
    workspace_path: str,
) -> str:
    """
    Obtiene el nombre lógico del notebook desde Workspace.
    """

    normalized = (
        workspace_path
        .replace("\\", "/")
        .rstrip("/")
    )

    return normalized.split("/")[-1]


def relative_local_file(
    local_file: Path,
) -> str:
    """
    Guarda en CSV una ruta portable relativa al proyecto.

    Ejemplo:

        snapshot/notebooks/Workspace/Oro/Notebook.scala
    """

    try:
        return (
            local_file
            .relative_to(PROJECT_ROOT)
            .as_posix()
        )

    except ValueError:
        return local_file.as_posix()


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print("=" * 70)
    print(
        "ASSESSMENT WORKSPACE - PASO 01"
    )
    print(
        "INVENTARIO DE NOTEBOOKS"
    )
    print("=" * 70)

    try:

        if not NOTEBOOK_INDEX_FILE.exists():

            raise FileNotFoundError(
                "No existe notebooks_index.json.\n"
                "Ejecuta primero:\n"
                "  python tools/00_prepare_snapshot.py"
            )

        OUTPUT_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        notebook_index = load_json(
            NOTEBOOK_INDEX_FILE
        )

        if not isinstance(
            notebook_index,
            list,
        ):

            raise TypeError(
                "notebooks_index.json "
                "no contiene una lista."
            )

        rows = []

        languages = Counter()
        formats = Counter()

        total_code_cells = 0
        total_markdown_cells = 0
        total_other_cells = 0

        missing_files = []
        extractor_errors = []

        # ====================================================
        # Procesar inventario entregado por el extractor
        # ====================================================

        for item in notebook_index:

            if not isinstance(
                item,
                dict,
            ):
                continue

            # ------------------------------------------------
            # Si el extractor reportó error, conservarlo
            # para el resumen y no inventar metadata.
            # ------------------------------------------------

            if item.get("error"):

                extractor_errors.append(
                    {
                        "path": item.get(
                            "path",
                            "",
                        ),
                        "error": item.get(
                            "error",
                            "",
                        ),
                    }
                )

                continue

            workspace_path = (
                item.get("path")
                or ""
            ).strip()

            if not workspace_path:

                continue

            language = normalize_language(
                item.get("language")
            )

            local_file = dbfs_file_to_local_path(
                dbfs_file=item.get("file"),
                workspace_path=workspace_path,
                language=language,
            )

            # ------------------------------------------------
            # Validar archivo físico
            # ------------------------------------------------

            if not local_file.exists():

                missing_files.append(
                    {
                        "workspace_path":
                            workspace_path,
                        "expected_file":
                            local_file,
                    }
                )

                continue

            file_extension = (
                local_file
                .suffix
                .lower()
            )

            # ------------------------------------------------
            # Conteo de celdas
            # ------------------------------------------------

            (
                code_cells,
                markdown_cells,
                other_cells,
            ) = count_source_cells(
                local_file
            )

            total_code_cells += (
                code_cells
            )

            total_markdown_cells += (
                markdown_cells
            )

            total_other_cells += (
                other_cells
            )

            languages[language] += 1

            formats[
                "DATABRICKS_SOURCE"
            ] += 1

            notebook_name = (
                notebook_name_from_workspace_path(
                    workspace_path
                )
            )

            # ------------------------------------------------
            # Contrato compatible con Assessment 1
            #
            # path mantiene una ruta local relativa para que
            # los scripts existentes puedan abrir archivos.
            #
            # workspace_path mantiene la verdad Databricks.
            # ------------------------------------------------

            local_file_relative = (
                relative_local_file(
                    local_file
                )
            )

            rows.append(
                {
                    "path":
                        local_file_relative,

                    "notebook_name":
                        notebook_name,

                    "source_format":
                        "DATABRICKS_SOURCE",

                    "file_extension":
                        file_extension,

                    "language":
                        language,

                    "code_cells":
                        code_cells,

                    "markdown_cells":
                        markdown_cells,

                    "other_cells":
                        other_cells,

                    "workspace_path":
                        workspace_path,

                    "local_file":
                        local_file_relative,
                }
            )

        # ====================================================
        # Orden
        # ====================================================

        rows.sort(
            key=lambda row:
            row["workspace_path"].lower()
        )

        # ====================================================
        # Generar CSV
        # ====================================================

        fieldnames = [
            # Assessment 1
            "path",
            "notebook_name",
            "source_format",
            "file_extension",
            "language",
            "code_cells",
            "markdown_cells",
            "other_cells",

            # Assessment 2
            "workspace_path",
            "local_file",
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
            writer.writerows(rows)

        # ====================================================
        # Resumen
        # ====================================================

        print()
        print(
            f"Registros en índice       : "
            f"{len(notebook_index)}"
        )

        print(
            f"Notebooks inventariados   : "
            f"{len(rows)}"
        )

        print(
            f"Errores extractor         : "
            f"{len(extractor_errors)}"
        )

        print(
            f"Archivos físicos faltantes: "
            f"{len(missing_files)}"
        )

        print()
        print(
            "Resumen por formato:"
        )

        for (
            source_format,
            count,
        ) in sorted(
            formats.items()
        ):

            print(
                f" - "
                f"{source_format:<20}: "
                f"{count}"
            )

        print()
        print(
            "Resumen por lenguaje:"
        )

        for (
            language,
            count,
        ) in sorted(
            languages.items()
        ):

            print(
                f" - "
                f"{language:<10}: "
                f"{count}"
            )

        print()
        print(
            "Resumen de celdas:"
        )

        print(
            f" - Celdas código    : "
            f"{total_code_cells}"
        )

        print(
            f" - Celdas Markdown  : "
            f"{total_markdown_cells}"
        )

        print(
            f" - Otras celdas     : "
            f"{total_other_cells}"
        )

        if missing_files:

            print()
            print(
                "Archivos físicos faltantes:"
            )

            for item in missing_files[:20]:

                print(
                    f" - "
                    f"{item['workspace_path']}"
                )

                print(
                    f"   esperado: "
                    f"{item['expected_file']}"
                )

            if len(
                missing_files
            ) > 20:

                print(
                    f" ... y "
                    f"{len(missing_files) - 20} "
                    f"más"
                )

        if extractor_errors:

            print()
            print(
                "Errores reportados "
                "por el extractor:"
            )

            for item in (
                extractor_errors[:20]
            ):

                print(
                    f" - "
                    f"{item['path']}: "
                    f"{item['error']}"
                )

        print()
        print(
            f"Archivo generado: "
            f"{OUTPUT_FILE}"
        )

        print(
            f"Registros generados: "
            f"{len(rows)}"
        )

        print()
        print("=" * 70)

        if (
            missing_files
            or extractor_errors
        ):

            print(
                "RESULTADO: COMPLETADO "
                "CON ADVERTENCIAS"
            )

        else:

            print(
                "RESULTADO: "
                "INVENTARIO GENERADO "
                "CORRECTAMENTE"
            )

        print("=" * 70)
        print()

        return 0

    except Exception as exc:

        print()
        print("=" * 70)
        print(
            "ERROR - PASO 01"
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