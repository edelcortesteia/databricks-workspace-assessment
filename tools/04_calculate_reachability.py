from pathlib import Path
import csv
from collections import defaultdict, deque, Counter


# ============================================================
# CONFIGURACIÓN
# ============================================================

OUTPUT_DIR = Path("output")

JOBS_FILE = (
    OUTPUT_DIR
    / "job_roots_resolved.csv"
)

DEPENDENCIES_FILE = (
    OUTPUT_DIR
    / "notebook_dependencies.csv"
)

INVENTORY_FILE = (
    OUTPUT_DIR
    / "notebooks.csv"
)

OUTPUT_FILE = (
    OUTPUT_DIR
    / "notebook_reachability.csv"
)


# ============================================================
# UTILIDADES
# ============================================================

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


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("ASSESSMENT WORKSPACE - PASO 04")
    print("ANÁLISIS DE ALCANZABILIDAD DE NOTEBOOKS")
    print("=" * 70)
    print()

    OUTPUT_DIR.mkdir(
        exist_ok=True
    )

    inventory = load_csv(
        INVENTORY_FILE
    )

    job_roots = load_csv(
        JOBS_FILE
    )

    dependencies = load_csv(
        DEPENDENCIES_FILE
    )

    # --------------------------------------------------------
    # 1. Universo físico/lógico del Workspace
    #
    # Assessment 1 utilizaba row["path"] porque los notebooks
    # estaban físicamente dentro del repositorio.
    #
    # Assessment 2 utiliza workspace_path como identidad
    # autoritativa del notebook.
    # --------------------------------------------------------

    all_notebooks = set()

    missing_workspace_path = 0

    for row in inventory:

        workspace_path = (
            row.get("workspace_path")
            or ""
        ).strip()

        if not workspace_path:
            missing_workspace_path += 1
            continue

        all_notebooks.add(
            workspace_path
        )

    if missing_workspace_path:
        raise RuntimeError(
            "Se encontraron registros en notebooks.csv "
            "sin workspace_path: "
            f"{missing_workspace_path}"
        )

    # --------------------------------------------------------
    # 2. Roots reales de los Jobs
    # --------------------------------------------------------

    root_notebooks = set()
    root_jobs = defaultdict(set)

    unresolved_roots = 0
    roots_outside_inventory = []

    for row in job_roots:

        if row.get("status") != "RESOLVED":
            unresolved_roots += 1
            continue

        notebook = (
            row.get("resolved_notebook")
            or ""
        ).strip()

        job = (
            row.get("job")
            or ""
        ).strip()

        if not notebook:
            continue

        if notebook not in all_notebooks:
            roots_outside_inventory.append(
                notebook
            )
            continue

        root_notebooks.add(
            notebook
        )

        if job:
            root_jobs[
                notebook
            ].add(job)

    if roots_outside_inventory:

        examples = "\n".join(
            f" - {path}"
            for path
            in roots_outside_inventory[:10]
        )

        raise RuntimeError(
            "Hay roots marcados RESOLVED que no existen "
            "en el inventario lógico del Workspace:\n"
            f"{examples}"
        )

    # --------------------------------------------------------
    # 3. Grafo de dependencias
    #
    # Solo se incorporan relaciones RESOLVED.
    #
    # source_notebook -> resolved_target
    #
    # Los 104 NOT_FOUND del Paso 02 permanecen como hallazgo
    # y NO generan aristas artificiales.
    # --------------------------------------------------------

    graph = defaultdict(set)

    resolved_edges = 0
    unresolved_edges = 0
    invalid_resolved_edges = []

    for row in dependencies:

        status = (
            row.get("status")
            or ""
        ).strip()

        if status != "RESOLVED":
            unresolved_edges += 1
            continue

        source = (
            row.get("source_notebook")
            or ""
        ).strip()

        target = (
            row.get("resolved_target")
            or ""
        ).strip()

        if not source or not target:
            continue

        # Una relación RESOLVED debe estar completamente
        # contenida en el snapshot actual.
        if (
            source not in all_notebooks
            or target not in all_notebooks
        ):
            invalid_resolved_edges.append(
                (
                    source,
                    target,
                )
            )
            continue

        graph[source].add(
            target
        )

        resolved_edges += 1

    if invalid_resolved_edges:

        examples = "\n".join(
            f" - {source} -> {target}"
            for source, target
            in invalid_resolved_edges[:10]
        )

        raise RuntimeError(
            "Se encontraron dependencias RESOLVED "
            "fuera del inventario del Workspace:\n"
            f"{examples}"
        )

    # --------------------------------------------------------
    # 4. Recorrer el grafo desde cada root
    #
    # Se conserva la semántica del Assessment 1:
    # cada notebook recibe todos los jobs desde los cuales
    # puede alcanzarse.
    # --------------------------------------------------------

    reachable = set()
    reached_by_jobs = defaultdict(set)

    for root_notebook in sorted(
        root_notebooks
    ):

        jobs = root_jobs[
            root_notebook
        ]

        queue = deque([
            root_notebook
        ])

        visited_for_root = set()

        while queue:

            current = queue.popleft()

            if current in visited_for_root:
                continue

            visited_for_root.add(
                current
            )

            reachable.add(
                current
            )

            for job in jobs:
                reached_by_jobs[
                    current
                ].add(job)

            for child in graph.get(
                current,
                set(),
            ):
                if child not in visited_for_root:
                    queue.append(
                        child
                    )

    # --------------------------------------------------------
    # 5. Clasificación
    #
    # ROOT        = notebook disparado directamente por Job
    # REACHABLE   = descendiente alcanzable desde algún root
    # UNREACHABLE = existe en Workspace pero no es alcanzable
    #               desde los Jobs extraídos
    # --------------------------------------------------------

    rows = []

    for notebook in sorted(
        all_notebooks
    ):

        if notebook in root_notebooks:
            status = "ROOT"

        elif notebook in reachable:
            status = "REACHABLE"

        else:
            status = "UNREACHABLE"

        jobs = sorted(
            reached_by_jobs.get(
                notebook,
                set(),
            )
        )

        rows.append({
            "notebook": notebook,
            "status": status,
            "jobs": " | ".join(jobs),
        })

    # --------------------------------------------------------
    # 6. Generar CSV
    #
    # Se mantiene EXACTAMENTE el contrato del Assessment 1:
    #
    # notebook,status,jobs
    # --------------------------------------------------------

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "notebook",
                "status",
                "jobs",
            ],
        )

        writer.writeheader()
        writer.writerows(rows)

    # --------------------------------------------------------
    # 7. Resumen
    # --------------------------------------------------------

    status_counter = Counter(
        row["status"]
        for row in rows
    )

    root_count = status_counter.get(
        "ROOT",
        0,
    )

    reachable_count = status_counter.get(
        "REACHABLE",
        0,
    )

    unreachable_count = status_counter.get(
        "UNREACHABLE",
        0,
    )

    in_job_scope = (
        root_count
        + reachable_count
    )

    jobs_represented = set()

    for jobs in reached_by_jobs.values():
        jobs_represented.update(
            jobs
        )

    print("--- Entradas ---")
    print(
        f"Notebooks Workspace          : "
        f"{len(all_notebooks)}"
    )
    print(
        f"Roots RESOLVED               : "
        f"{len(root_notebooks)}"
    )
    print(
        f"Roots no resueltos           : "
        f"{unresolved_roots}"
    )
    print(
        f"Dependencias RESOLVED        : "
        f"{resolved_edges}"
    )
    print(
        f"Dependencias no resueltas    : "
        f"{unresolved_edges}"
    )
    print()

    print("--- Clasificación ---")
    print(
        f"ROOT                         : "
        f"{root_count}"
    )
    print(
        f"REACHABLE                    : "
        f"{reachable_count}"
    )
    print(
        f"UNREACHABLE                  : "
        f"{unreachable_count}"
    )
    print()

    print(
        f"Notebooks en alcance de Jobs : "
        f"{in_job_scope}"
    )
    print(
        f"Jobs representados           : "
        f"{len(jobs_represented)}"
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
    print(
        "RESULTADO: ALCANZABILIDAD "
        "CALCULADA CORRECTAMENTE"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
