from pathlib import Path
import csv
import json
import posixpath
from collections import Counter


# ============================================================
# CONFIGURACIÓN
# ============================================================

SNAPSHOT_DIR = Path("snapshot")
OUTPUT_DIR = Path("output")

JOBS_TO_NOTEBOOKS_FILE = (
    SNAPSHOT_DIR
    / "jobs_to_notebooks.json"
)

NOTEBOOK_INVENTORY_FILE = (
    OUTPUT_DIR
    / "notebooks.csv"
)

OUTPUT_FILE = (
    OUTPUT_DIR
    / "job_roots_resolved.csv"
)

SUPPORTED_NOTEBOOK_EXTENSIONS = {
    ".py",
    ".scala",
    ".sql",
    ".ipynb",
}


# ============================================================
# UTILIDADES
# ============================================================

def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el archivo requerido: {path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8-sig",
    ) as file:
        return json.load(file)


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el archivo requerido: {path}"
        )

    with open(
        path,
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        return list(csv.DictReader(file))


def strip_known_extension(path: str) -> str:
    """
    Quita únicamente extensiones conocidas de notebooks.
    No altera puntos que formen parte real del nombre.
    """

    lower_path = path.lower()

    for extension in SUPPORTED_NOTEBOOK_EXTENSIONS:
        if lower_path.endswith(extension):
            return path[:-len(extension)]

    return path


def normalize_workspace_reference(
    reference: str,
) -> str:
    """
    Normaliza una referencia lógica proveniente de la
    configuración de un Job hacia el namespace lógico
    del Workspace.

    Ejemplos:

        /Oro/Cedulas/Coordinador
            -> /Workspace/Oro/Cedulas/Coordinador

        /Workspace/Oro/Cedulas/Coordinador
            -> /Workspace/Oro/Cedulas/Coordinador

        Workspace/Oro/Cedulas/Coordinador
            -> /Workspace/Oro/Cedulas/Coordinador

    IMPORTANTE:
    - No busca por basename.
    - No usa suffix matching.
    - No usa comparación case-insensitive.
    - No intenta encontrar "equivalentes" en otras carpetas.
    """

    reference = (
        reference
        or ""
    ).strip().replace("\\", "/")

    if not reference:
        return ""

    reference = strip_known_extension(
        reference
    )

    if reference == "/Workspace":
        candidate = "/Workspace"

    elif reference.startswith("/Workspace/"):
        candidate = reference

    elif reference.startswith("Workspace/"):
        candidate = "/" + reference

    elif reference.startswith("/"):
        candidate = "/Workspace" + reference

    else:
        candidate = "/Workspace/" + reference

    # Normalización exclusivamente sintáctica:
    # dobles slash, "." y "..".
    candidate = posixpath.normpath(
        candidate
    )

    if not candidate.startswith("/"):
        candidate = "/" + candidate

    return candidate


def build_workspace_index(
    notebooks: list[dict],
) -> dict[str, str]:
    """
    Índice estricto:
        workspace_path exacto -> workspace_path real

    La ruta completa es la identidad del notebook.
    """

    index: dict[str, str] = {}

    duplicate_paths = []

    for row in notebooks:

        workspace_path = (
            row.get("workspace_path")
            or ""
        ).strip()

        if not workspace_path:
            continue

        canonical = normalize_workspace_reference(
            workspace_path
        )

        if (
            canonical in index
            and index[canonical] != workspace_path
        ):
            duplicate_paths.append(
                canonical
            )

        index[canonical] = workspace_path

    if duplicate_paths:
        examples = "\n".join(
            f" - {path}"
            for path in duplicate_paths[:10]
        )

        raise RuntimeError(
            "Se detectaron rutas lógicas duplicadas "
            "en output/notebooks.csv:\n"
            f"{examples}"
        )

    return index


# ============================================================
# RESOLUCIÓN
# ============================================================

def resolve_job_root(
    reference: str,
    workspace_index: dict[str, str],
) -> tuple[str, str, str]:
    """
    Devuelve:
        normalized_reference,
        resolved_notebook,
        status

    Regla estricta del Assessment Workspace:

        ruta lógica exacta existente -> RESOLVED
        ruta lógica exacta ausente   -> NOT_FOUND
    """

    normalized_reference = (
        normalize_workspace_reference(
            reference
        )
    )

    if not normalized_reference:
        return (
            "",
            "",
            "NOT_FOUND",
        )

    if normalized_reference in workspace_index:
        return (
            normalized_reference,
            workspace_index[
                normalized_reference
            ],
            "RESOLVED",
        )

    return (
        normalized_reference,
        "",
        "NOT_FOUND",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 03")
    print("RESOLUCIÓN DE NOTEBOOKS RAÍZ DE JOBS")
    print("=" * 70)
    print()

    OUTPUT_DIR.mkdir(
        exist_ok=True
    )

    jobs_to_notebooks = load_json(
        JOBS_TO_NOTEBOOKS_FILE
    )

    notebooks = load_csv(
        NOTEBOOK_INVENTORY_FILE
    )

    if not isinstance(
        jobs_to_notebooks,
        list,
    ):
        raise TypeError(
            "jobs_to_notebooks.json debe contener una lista."
        )

    workspace_index = build_workspace_index(
        notebooks
    )

    print(
        f"Relaciones Job/Task extraídas : "
        f"{len(jobs_to_notebooks)}"
    )
    print(
        f"Notebooks inventariados       : "
        f"{len(workspace_index)}"
    )
    print()

    rows = []
    seen = set()

    ignored_non_notebook = 0
    ignored_without_target = 0
    duplicate_relations = 0

    jobs_seen = set()
    task_keys_seen = set()

    for relation in jobs_to_notebooks:

        job = str(
            relation.get("job")
            or ""
        ).strip()

        task_key = str(
            relation.get("task_key")
            or ""
        ).strip()

        relation_type = str(
            relation.get("tipo")
            or ""
        ).strip().lower()

        reference = str(
            relation.get("dispara")
            or ""
        ).strip()

        if job:
            jobs_seen.add(job)

        if task_key:
            task_keys_seen.add(
                (
                    job,
                    task_key,
                )
            )

        if relation_type != "notebook":
            ignored_non_notebook += 1
            continue

        if not reference:
            ignored_without_target += 1
            continue

        (
            normalized_reference,
            resolved_notebook,
            status,
        ) = resolve_job_root(
            reference=reference,
            workspace_index=workspace_index,
        )

        # El contrato heredado del Assessment 1 es Job -> root.
        # Si un mismo Job ejecuta exactamente el mismo notebook
        # desde más de una tarea, basta una relación para los
        # pasos de reachability.
        key = (
            job,
            reference,
            normalized_reference,
            resolved_notebook,
            status,
        )

        if key in seen:
            duplicate_relations += 1
            continue

        seen.add(key)

        rows.append({
            "job": job,
            "root_notebook": reference,
            "normalized_reference": normalized_reference,
            "resolved_notebook": resolved_notebook,
            "status": status,
        })

    rows.sort(
        key=lambda row: (
            row["job"].casefold(),
            row["normalized_reference"],
            row["root_notebook"],
        )
    )

    fieldnames = [
        "job",
        "root_notebook",
        "normalized_reference",
        "resolved_notebook",
        "status",
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(rows)

    status_counter = Counter(
        row["status"]
        for row in rows
    )

    print("--- Fuente real del extractor ---")
    print(
        f"Jobs detectados               : "
        f"{len(jobs_seen)}"
    )
    print(
        f"Tareas detectadas             : "
        f"{len(task_keys_seen)}"
    )
    print(
        f"Relaciones notebook usadas    : "
        f"{len(rows)}"
    )
    print(
        f"No-notebook ignoradas         : "
        f"{ignored_non_notebook}"
    )
    print(
        f"Sin notebook destino          : "
        f"{ignored_without_target}"
    )
    print(
        f"Duplicados Job->root omitidos : "
        f"{duplicate_relations}"
    )
    print()

    print("--- Resolución estricta ---")
    print(
        f"RESOLVED    : "
        f"{status_counter.get('RESOLVED', 0)}"
    )
    print(
        f"NOT_FOUND   : "
        f"{status_counter.get('NOT_FOUND', 0)}"
    )
    print(
        f"AMBIGUOUS   : 0"
    )
    print()

    not_found_rows = [
        row
        for row in rows
        if row["status"] == "NOT_FOUND"
    ]

    if not_found_rows:

        print(
            "Roots no encontrados "
            "(primeros 25):"
        )

        for row in not_found_rows[:25]:
            print(
                f" - Job        : {row['job']}"
            )
            print(
                f"   referencia : "
                f"{row['root_notebook']}"
            )
            print(
                f"   esperada   : "
                f"{row['normalized_reference']}"
            )

        if len(not_found_rows) > 25:
            print(
                f" ... y "
                f"{len(not_found_rows) - 25} más"
            )

        print()

    print(
        f"Archivo generado: "
        f"{OUTPUT_FILE.resolve()}"
    )
    print(
        f"Registros generados: "
        f"{len(rows)}"
    )
    print()

    print("=" * 70)

    if not_found_rows:
        print(
            "RESULTADO: COMPLETADO CON ROOTS "
            "PARA REVISIÓN"
        )
    else:
        print(
            "RESULTADO: ROOTS DE JOBS "
            "RESUELTOS CORRECTAMENTE"
        )

    print("=" * 70)


if __name__ == "__main__":
    main()