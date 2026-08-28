from pathlib import Path
from collections import defaultdict, Counter
import csv


# ============================================================
# CONFIGURACIÓN
# ============================================================

JOBS_FILE = Path(
    "output/job_roots_resolved.csv"
)

DEPENDENCIES_FILE = Path(
    "output/notebook_dependencies.csv"
)

INVENTORY_FILE = Path(
    "output/job_notebook_inventory.csv"
)

OUTPUT_FILE = Path(
    "output/job_notebook_hierarchy.csv"
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
    print("ASSESSMENT WORKSPACE - PASO 06")
    print("JERARQUÍA DE NOTEBOOKS POR JOB")
    print("=" * 70)
    print()

    job_root_rows = load_csv(
        JOBS_FILE
    )

    dependency_rows = load_csv(
        DEPENDENCIES_FILE
    )

    job_inventory_rows = load_csv(
        INVENTORY_FILE
    )

    # --------------------------------------------------------
    # 1. Leer raíces reales de los Jobs
    #
    # Conservamos la lógica de la Herramienta 1:
    # solo roots RESOLVED.
    #
    # A diferencia del script original, validamos que un mismo
    # Job no venga accidentalmente con roots distintos.
    # --------------------------------------------------------

    job_roots = {}
    duplicate_root_rows = 0

    for row in job_root_rows:

        if (
            row.get("status")
            or ""
        ).strip() != "RESOLVED":
            continue

        job = (
            row.get("job")
            or ""
        ).strip()

        root_notebook = (
            row.get("resolved_notebook")
            or ""
        ).strip()

        if not job or not root_notebook:
            continue

        if job in job_roots:

            if (
                job_roots[job]
                != root_notebook
            ):
                raise RuntimeError(
                    "Un mismo Job tiene más de un root "
                    "RESOLVED distinto:\n"
                    f"Job: {job}\n"
                    f"Root 1: {job_roots[job]}\n"
                    f"Root 2: {root_notebook}"
                )

            duplicate_root_rows += 1
            continue

        job_roots[job] = root_notebook

    # --------------------------------------------------------
    # 2. Universo esperado Job -> Notebook del Paso 05
    #
    # Este inventario nos sirve como control cruzado:
    # la jerarquía generada debe cubrir exactamente los mismos
    # notebooks únicos por Job que el Paso 05.
    # --------------------------------------------------------

    expected_by_job = defaultdict(set)

    for row in job_inventory_rows:

        job = (
            row.get("job")
            or ""
        ).strip()

        notebook = (
            row.get("notebook")
            or ""
        ).strip()

        if job and notebook:
            expected_by_job[job].add(
                notebook
            )

    missing_jobs_in_inventory = (
        set(job_roots)
        - set(expected_by_job)
    )

    if missing_jobs_in_inventory:

        raise RuntimeError(
            "Hay Jobs con root RESOLVED que no aparecen "
            "en job_notebook_inventory.csv:\n"
            + "\n".join(
                f" - {job}"
                for job
                in sorted(
                    missing_jobs_in_inventory,
                    key=str.casefold,
                )
            )
        )

    # --------------------------------------------------------
    # 3. Construir grafo de dependencias
    #
    # source -> [(target, relationship), ...]
    #
    # Igual que en la Herramienta 1:
    # SOLO relaciones RESOLVED.
    #
    # Por tanto:
    # - comentarios ya fueron descartados en Paso 02;
    # - NOT_FOUND no crea aristas;
    # - no se intenta "reparar" rutas;
    # - no hay basename/suffix/case-insensitive fallback.
    # --------------------------------------------------------

    graph = defaultdict(list)

    resolved_input_rows = 0
    unresolved_ignored = 0

    for row in dependency_rows:

        status = (
            row.get("status")
            or ""
        ).strip()

        if status != "RESOLVED":
            unresolved_ignored += 1
            continue

        source = (
            row.get("source_notebook")
            or ""
        ).strip()

        target = (
            row.get("resolved_target")
            or ""
        ).strip()

        relationship = (
            row.get("relationship")
            or ""
        ).strip()

        if not source or not target:
            continue

        resolved_input_rows += 1

        graph[source].append(
            (
                target,
                relationship,
            )
        )

    # --------------------------------------------------------
    # 4. Eliminar aristas lógicas duplicadas
    #
    # MISMA consideración de la Herramienta 1:
    #
    # Si un notebook llama 10 veces al mismo hijo usando el
    # mismo tipo de relación, para la jerarquía lógica basta
    # una sola arista.
    #
    # El detalle por celda permanece en
    # notebook_dependencies.csv.
    # --------------------------------------------------------

    duplicate_edges_removed = 0

    for source in list(graph):

        unique_dependencies = []
        seen = set()

        for (
            target,
            relationship,
        ) in graph[source]:

            key = (
                target,
                relationship,
            )

            if key in seen:
                duplicate_edges_removed += 1
                continue

            seen.add(key)

            unique_dependencies.append(
                (
                    target,
                    relationship,
                )
            )

        graph[source] = (
            unique_dependencies
        )

    logical_edge_count = sum(
        len(children)
        for children in graph.values()
    )

    # --------------------------------------------------------
    # 5. Recorrer jerarquía por Job
    #
    # IMPORTANTE:
    #
    # No usamos un "visited global por Job".
    #
    # Un mismo notebook puede aparecer por caminos distintos y
    # eso es información válida de jerarquía.
    #
    # Para evitar loops, solo detectamos si el notebook actual
    # ya existe en la rama (current_path), igual que en la
    # Herramienta 1.
    # --------------------------------------------------------

    rows = []

    traversal_order = 0

    def walk(
        job,
        current_notebook,
        parent_notebook,
        relationship,
        level,
        current_path,
    ):

        nonlocal traversal_order

        traversal_order += 1

        # Ciclo = el notebook ya estaba en ESTA rama.
        is_cycle = (
            current_notebook
            in current_path
        )

        new_path = (
            current_path
            + [current_notebook]
        )

        rows.append({
            "job": job,
            "order": traversal_order,
            "level": level,
            "parent_notebook": parent_notebook,
            "notebook": current_notebook,
            "relationship": relationship,
            "cycle_detected": (
                "YES"
                if is_cycle
                else "NO"
            ),
            "path": " -> ".join(
                new_path
            ),
        })

        # Si se detecta ciclo, registrar la relación pero
        # detener únicamente esa rama.
        if is_cycle:
            return

        children = graph.get(
            current_notebook,
            [],
        )

        for (
            child_notebook,
            child_relationship,
        ) in children:

            walk(
                job=job,
                current_notebook=child_notebook,
                parent_notebook=current_notebook,
                relationship=child_relationship,
                level=level + 1,
                current_path=new_path,
            )

    # --------------------------------------------------------
    # 6. Ejecutar recorrido para cada Job
    # --------------------------------------------------------

    for job in sorted(
        job_roots,
        key=str.casefold,
    ):

        root_notebook = (
            job_roots[job]
        )

        walk(
            job=job,
            current_notebook=root_notebook,
            parent_notebook="",
            relationship="ROOT",
            level=0,
            current_path=[],
        )

    # --------------------------------------------------------
    # 7. Validación cruzada Paso 05 vs Paso 06
    #
    # La jerarquía puede contener más FILAS que el inventario
    # porque un notebook puede aparecer en varios caminos.
    #
    # Pero el conjunto de notebooks únicos por Job debe ser
    # exactamente el mismo.
    # --------------------------------------------------------

    hierarchy_by_job = defaultdict(set)

    for row in rows:

        hierarchy_by_job[
            row["job"]
        ].add(
            row["notebook"]
        )

    inconsistencies = []

    for job in sorted(
        job_roots,
        key=str.casefold,
    ):

        expected = (
            expected_by_job.get(
                job,
                set(),
            )
        )

        actual = (
            hierarchy_by_job.get(
                job,
                set(),
            )
        )

        missing = (
            expected
            - actual
        )

        extra = (
            actual
            - expected
        )

        if missing or extra:
            inconsistencies.append(
                (
                    job,
                    missing,
                    extra,
                )
            )

    if inconsistencies:

        messages = []

        for (
            job,
            missing,
            extra,
        ) in inconsistencies[:10]:

            messages.append(
                f"Job: {job}"
            )

            if missing:
                messages.append(
                    "  Faltantes en jerarquía:"
                )
                messages.extend(
                    f"   - {notebook}"
                    for notebook
                    in sorted(missing)
                )

            if extra:
                messages.append(
                    "  Extras en jerarquía:"
                )
                messages.extend(
                    f"   - {notebook}"
                    for notebook
                    in sorted(extra)
                )

        raise RuntimeError(
            "La jerarquía no coincide con el inventario "
            "del Paso 05.\n"
            + "\n".join(messages)
        )

    # --------------------------------------------------------
    # 8. Generar CSV
    #
    # Contrato EXACTO de la Herramienta 1:
    #
    # job, order, level, parent_notebook, notebook,
    # relationship, cycle_detected, path
    # --------------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "job",
        "order",
        "level",
        "parent_notebook",
        "notebook",
        "relationship",
        "cycle_detected",
        "path",
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

    # --------------------------------------------------------
    # 9. Resumen
    # --------------------------------------------------------

    cycle_count = sum(
        1
        for row in rows
        if row["cycle_detected"] == "YES"
    )

    max_level = max(
        (
            int(row["level"])
            for row in rows
        ),
        default=0,
    )

    unique_hierarchy_pairs = set(
        (
            row["job"],
            row["notebook"],
        )
        for row in rows
    )

    repeated_path_occurrences = (
        len(rows)
        - len(unique_hierarchy_pairs)
    )

    job_row_counts = Counter(
        row["job"]
        for row in rows
    )

    print("--- Entradas ---")
    print(
        f"Jobs con root RESOLVED       : "
        f"{len(job_roots)}"
    )
    print(
        f"Roots duplicados omitidos    : "
        f"{duplicate_root_rows}"
    )
    print(
        f"Dependencias RESOLVED entrada: "
        f"{resolved_input_rows}"
    )
    print(
        f"Dependencias no resueltas    : "
        f"{unresolved_ignored}"
    )
    print(
        f"Aristas duplicadas omitidas  : "
        f"{duplicate_edges_removed}"
    )
    print(
        f"Aristas lógicas del grafo    : "
        f"{logical_edge_count}"
    )
    print()

    print("--- Jerarquía ---")
    print(
        f"Jobs procesados              : "
        f"{len(job_roots)}"
    )
    print(
        f"Filas jerárquicas generadas  : "
        f"{len(rows)}"
    )
    print(
        f"Job->Notebook únicos         : "
        f"{len(unique_hierarchy_pairs)}"
    )
    print(
        f"Repeticiones por otros caminos: "
        f"{repeated_path_occurrences}"
    )
    print(
        f"Nivel máximo                 : "
        f"{max_level}"
    )
    print(
        f"Ciclos detectados            : "
        f"{cycle_count}"
    )
    print()

    print(
        "Validación contra Paso 05    : OK"
    )
    print()

    print("Resumen por Job:")

    for job in sorted(
        job_row_counts,
        key=str.casefold,
    ):

        unique_count = len(
            hierarchy_by_job[job]
        )

        print(
            f" - {job:<45} "
            f"{job_row_counts[job]:>4} filas, "
            f"{unique_count:>3} notebooks únicos"
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
        "RESULTADO: JERARQUÍA GENERADA "
        "Y VALIDADA CORRECTAMENTE"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
