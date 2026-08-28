from pathlib import Path
import csv
from collections import Counter


# ============================================================
# CONFIGURACIÓN
# ============================================================

INPUT_FILE = Path(
    "output/notebook_reachability.csv"
)

OUTPUT_FILE = Path(
    "output/job_notebook_inventory.csv"
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
    print("ASSESSMENT WORKSPACE - PASO 05")
    print("INVENTARIO DE NOTEBOOKS POR JOB")
    print("=" * 70)
    print()

    reachability = load_csv(
        INPUT_FILE
    )

    rows = []
    seen = set()

    unreachable_ignored = 0
    reachable_without_job = []
    duplicate_relations = 0

    for row in reachability:

        notebook = (
            row.get("notebook")
            or ""
        ).strip()

        status = (
            row.get("status")
            or ""
        ).strip()

        jobs_raw = (
            row.get("jobs")
            or ""
        ).strip()

        # Igual que en la Herramienta 1:
        # un notebook UNREACHABLE no pertenece al alcance
        # de ningún Job.
        if status == "UNREACHABLE":
            unreachable_ignored += 1
            continue

        # ROOT y REACHABLE deben tener al menos un Job.
        # Si no lo tienen, hay una inconsistencia entre
        # los Pasos 04 y 05 y no debemos ocultarla.
        if not jobs_raw:
            reachable_without_job.append(
                (
                    notebook,
                    status,
                )
            )
            continue

        jobs = [
            job.strip()
            for job in jobs_raw.split("|")
            if job.strip()
        ]

        for job in jobs:

            key = (
                job,
                notebook,
                status,
            )

            if key in seen:
                duplicate_relations += 1
                continue

            seen.add(key)

            rows.append({
                "job": job,
                "notebook": notebook,
                "relationship": status,
            })

    if reachable_without_job:

        examples = "\n".join(
            f" - {status}: {notebook}"
            for notebook, status
            in reachable_without_job[:10]
        )

        raise RuntimeError(
            "Se encontraron notebooks ROOT/REACHABLE "
            "sin Job asociado:\n"
            f"{examples}"
        )

    # Mismo orden lógico de la Herramienta 1:
    # primero ROOT y después descendientes.
    rows.sort(
        key=lambda row: (
            row["job"].casefold(),
            0
            if row["relationship"] == "ROOT"
            else 1,
            row["notebook"],
        )
    )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Contrato idéntico al Assessment 1:
    # job,notebook,relationship
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "job",
                "notebook",
                "relationship",
            ],
        )

        writer.writeheader()
        writer.writerows(rows)

    # --------------------------------------------------------
    # Resumen por Job
    # --------------------------------------------------------

    job_counts = Counter()
    job_roots = Counter()
    job_reachable = Counter()

    for row in rows:

        job = row["job"]

        job_counts[job] += 1

        if row["relationship"] == "ROOT":
            job_roots[job] += 1

        elif row["relationship"] == "REACHABLE":
            job_reachable[job] += 1

    jobs = sorted(
        job_counts,
        key=str.casefold,
    )

    print(
        f"Registros reachability leídos : "
        f"{len(reachability)}"
    )
    print(
        f"UNREACHABLE ignorados         : "
        f"{unreachable_ignored}"
    )
    print(
        f"Duplicados omitidos           : "
        f"{duplicate_relations}"
    )
    print()

    print(
        f"Relaciones Job -> Notebook    : "
        f"{len(rows)}"
    )
    print(
        f"Jobs representados            : "
        f"{len(jobs)}"
    )
    print()

    print("Resumen por Job:")

    for job in jobs:
        print(
            f" - {job:<45} "
            f"{job_counts[job]:>3} notebooks "
            f"(ROOT: {job_roots[job]}, "
            f"REACHABLE: {job_reachable[job]})"
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
        "RESULTADO: INVENTARIO JOB -> NOTEBOOK "
        "GENERADO CORRECTAMENTE"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()