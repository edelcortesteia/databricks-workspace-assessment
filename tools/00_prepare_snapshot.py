from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path


# ============================================================
# CONFIGURACIÓN
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = PROJECT_ROOT / "input"
SNAPSHOT_DIR = PROJECT_ROOT / "snapshot"

REQUIRED_FILES = [
    "_manifest.json",
    "notebooks_index.json",
    "jobs_index.json",
    "jobs_to_notebooks.json",
    "ddl/inventario_tablas_csv/inventario_tablas.csv",
]


# ============================================================
# UTILIDADES
# ============================================================

def find_input_zip() -> Path:
    """
    Busca archivos ZIP dentro de input/.

    Para esta primera versión esperamos exactamente un ZIP.
    """
    zip_files = sorted(INPUT_DIR.glob("*.zip"))

    if not zip_files:
        raise FileNotFoundError(
            f"No se encontró ningún archivo .zip en:\n{INPUT_DIR}"
        )

    if len(zip_files) > 1:
        files = "\n".join(f" - {p.name}" for p in zip_files)
        raise RuntimeError(
            "Se encontró más de un archivo ZIP en input/.\n"
            "Deja solamente el snapshot que deseas analizar:\n"
            f"{files}"
        )

    return zip_files[0]


def clean_snapshot_dir() -> None:
    """
    Limpia el contenido anterior de snapshot/.

    No elimina la carpeta raíz; solo reconstruye su contenido.
    """
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def validate_zip(zip_path: Path) -> None:
    """
    Verifica que el archivo sea un ZIP válido.
    """
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError(
            f"El archivo no es un ZIP válido:\n{zip_path}"
        )


def extract_zip(zip_path: Path) -> None:
    """
    Extrae el ZIP completo dentro de snapshot/.
    """
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(SNAPSHOT_DIR)


def normalize_snapshot_root() -> Path:
    """
    Algunos ZIP pueden traer todos los archivos directamente en la raíz:

        snapshot/_manifest.json
        snapshot/notebooks_index.json
        ...

    Otros podrían traer una carpeta contenedora:

        snapshot/migracion_hive_pro/_manifest.json
        ...

    Esta función detecta ambos casos y devuelve la raíz real
    del snapshot.
    """

    direct_manifest = SNAPSHOT_DIR / "_manifest.json"

    if direct_manifest.exists():
        return SNAPSHOT_DIR

    candidates = list(SNAPSHOT_DIR.rglob("_manifest.json"))

    if not candidates:
        raise FileNotFoundError(
            "No se encontró _manifest.json dentro del ZIP."
        )

    if len(candidates) > 1:
        locations = "\n".join(
            f" - {p.parent}"
            for p in candidates
        )

        raise RuntimeError(
            "Se encontraron múltiples manifests dentro del ZIP.\n"
            "No es posible determinar automáticamente la raíz:\n"
            f"{locations}"
        )

    return candidates[0].parent


def validate_required_files(snapshot_root: Path) -> None:
    """
    Valida que existan los archivos mínimos necesarios para
    continuar con el Assessment 2.
    """
    missing = []

    for relative_path in REQUIRED_FILES:
        file_path = snapshot_root / relative_path

        if not file_path.exists():
            missing.append(relative_path)

    if missing:
        missing_text = "\n".join(
            f" - {item}"
            for item in missing
        )

        raise FileNotFoundError(
            "El snapshot está incompleto.\n"
            "Faltan los siguientes archivos requeridos:\n"
            f"{missing_text}"
        )


def load_json(path: Path):
    """
    Lee un archivo JSON usando UTF-8.
    """
    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def validate_json_files(snapshot_root: Path) -> dict:
    """
    Verifica que los JSON principales puedan leerse correctamente.

    Devuelve información básica para el resumen.
    """

    manifest = load_json(
        snapshot_root / "_manifest.json"
    )

    notebooks = load_json(
        snapshot_root / "notebooks_index.json"
    )

    jobs = load_json(
        snapshot_root / "jobs_index.json"
    )

    job_tasks = load_json(
        snapshot_root / "jobs_to_notebooks.json"
    )

    if not isinstance(notebooks, list):
        raise TypeError(
            "notebooks_index.json no contiene una lista."
        )

    if not isinstance(jobs, list):
        raise TypeError(
            "jobs_index.json no contiene una lista."
        )

    if not isinstance(job_tasks, list):
        raise TypeError(
            "jobs_to_notebooks.json no contiene una lista."
        )

    return {
        "manifest": manifest,
        "notebooks": notebooks,
        "jobs": jobs,
        "job_tasks": job_tasks,
    }


def count_notebook_files(snapshot_root: Path) -> int:
    """
    Cuenta archivos físicos exportados dentro de notebooks/.
    """
    notebooks_dir = snapshot_root / "notebooks"

    if not notebooks_dir.exists():
        return 0

    extensions = {
        ".scala",
        ".py",
        ".sql",
        ".r",
        ".txt",
    }

    return sum(
        1
        for path in notebooks_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in extensions
    )


def print_summary(
    zip_path: Path,
    snapshot_root: Path,
    data: dict,
) -> None:
    """
    Imprime resumen de validación.
    """

    manifest = data["manifest"]

    notebook_index = data["notebooks"]
    jobs = data["jobs"]
    job_tasks = data["job_tasks"]

    notebook_files = count_notebook_files(
        snapshot_root
    )

    valid_notebooks = sum(
        1
        for item in notebook_index
        if isinstance(item, dict)
        and item.get("file")
    )

    notebook_errors = sum(
        1
        for item in notebook_index
        if isinstance(item, dict)
        and item.get("error")
    )

    valid_jobs = sum(
        1
        for item in jobs
        if isinstance(item, dict)
        and item.get("file")
    )

    job_errors = sum(
        1
        for item in jobs
        if isinstance(item, dict)
        and item.get("error")
    )

    print()
    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 00")
    print("PREPARACIÓN Y VALIDACIÓN DEL SNAPSHOT")
    print("=" * 70)

    print()
    print(f"ZIP entrada       : {zip_path}")
    print(f"Snapshot          : {snapshot_root}")

    print()
    print("--- Manifest ---")
    print(
        f"Generado          : "
        f"{manifest.get('generado', 'N/D')}"
    )
    print(
        f"Source root       : "
        f"{manifest.get('source_root', 'N/D')}"
    )
    print(
        f"Hive catalog      : "
        f"{manifest.get('hive_catalog', 'N/D')}"
    )
    print(
        f"Notebooks manifest: "
        f"{manifest.get('notebooks', 'N/D')}"
    )
    print(
        f"Jobs manifest     : "
        f"{manifest.get('jobs', 'N/D')}"
    )
    print(
        f"Tablas Hive       : "
        f"{manifest.get('tablas_hive', 'N/D')}"
    )

    print()
    print("--- Validación física ---")
    print(
        f"Notebooks índice  : "
        f"{len(notebook_index)}"
    )
    print(
        f"Notebooks válidos : "
        f"{valid_notebooks}"
    )
    print(
        f"Errores notebook  : "
        f"{notebook_errors}"
    )
    print(
        f"Archivos notebook : "
        f"{notebook_files}"
    )

    print()
    print(
        f"Jobs índice       : "
        f"{len(jobs)}"
    )
    print(
        f"Jobs válidos      : "
        f"{valid_jobs}"
    )
    print(
        f"Errores job       : "
        f"{job_errors}"
    )
    print(
        f"Tareas de jobs    : "
        f"{len(job_tasks)}"
    )

    print()
    print("--- Archivos requeridos ---")

    for relative_path in REQUIRED_FILES:
        print(
            f"OK  {relative_path}"
        )

    print()
    print(
        "RESULTADO: SNAPSHOT PREPARADO CORRECTAMENTE"
    )
    print("=" * 70)
    print()


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    try:
        print(
            "Buscando snapshot en input/..."
        )

        zip_path = find_input_zip()

        print(
            f"ZIP encontrado: {zip_path.name}"
        )

        validate_zip(
            zip_path
        )

        print(
            "ZIP válido."
        )

        print(
            "Preparando snapshot/..."
        )

        clean_snapshot_dir()

        print(
            "Extrayendo archivos..."
        )

        extract_zip(
            zip_path
        )

        snapshot_root = normalize_snapshot_root()

        print(
            f"Raíz detectada: {snapshot_root}"
        )

        print(
            "Validando archivos requeridos..."
        )

        validate_required_files(
            snapshot_root
        )

        print(
            "Validando JSON..."
        )

        data = validate_json_files(
            snapshot_root
        )

        print_summary(
            zip_path=zip_path,
            snapshot_root=snapshot_root,
            data=data,
        )

        return 0

    except Exception as exc:

        print()
        print("=" * 70)
        print("ERROR - PASO 00")
        print("=" * 70)
        print(
            f"{type(exc).__name__}: {exc}"
        )
        print("=" * 70)
        print()

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )