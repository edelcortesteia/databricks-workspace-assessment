#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd


def split_pipe(value):
    if pd.isna(value) or str(value).strip() == "":
        return []
    return [x.strip() for x in str(value).split("|") if x.strip()]


def uniq_join(values):
    out = []
    seen = set()
    for value in values:
        for item in split_pipe(value):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return " | ".join(out)


def make_action(action_key, change_type, title, description, rows,
                config_change, code_change, target_value,
                priority="HIGH", status="PENDING_IMPLEMENTATION",
                source_steps="STEP_15 | STEP_19 | STEP_20", notes=""):
    notebooks = uniq_join(rows.get("notebook", pd.Series(dtype=str)).tolist())
    jobs = uniq_join(rows.get("jobs", rows.get("job", pd.Series(dtype=str))).tolist())
    return {
        "action_key": action_key,
        "change_type": change_type,
        "title": title,
        "description": description,
        "affected_notebooks": notebooks,
        "affected_notebook_count": len(split_pipe(notebooks)),
        "affected_jobs": jobs,
        "affected_job_count": len(split_pipe(jobs)),
        "config_change": config_change,
        "code_change": code_change,
        "target_value": target_value,
        "priority": priority,
        "status": status,
        "source_steps": source_steps,
        "notes": notes,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate consolidated master migration actions")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    out = Path(args.output_dir)
    storage_path = out / "storage_migration_analysis.csv"
    work_path = out / "dynamic_working_tables.csv"
    backlog_path = out / "notebook_migration_backlog.csv"
    readiness_path = out / "job_migration_readiness.csv"
    table_path = out / "table_hive_reconciliation_final.csv"
    output_path = out / "master_migration_actions.csv"

    missing = [p for p in [storage_path, work_path, backlog_path, readiness_path, table_path] if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing required inputs: " + ", ".join(str(p) for p in missing))

    storage = pd.read_csv(storage_path)
    working = pd.read_csv(work_path)
    backlog = pd.read_csv(backlog_path)
    readiness = pd.read_csv(readiness_path)
    tables = pd.read_csv(table_path)

    actions = []

    # ACT: Cedulas DBFS -> ABFSS
    s = storage[(storage["requires_action"].astype(str).str.upper() == "YES") &
                (storage["migration_status"] == "CONFIG_ABFSS_URI_REQUIRED")]
    if not s.empty:
        config_path = s["config_path"].dropna().astype(str).iloc[0] if s["config_path"].notna().any() else "Cedulas.MountContenedorCedulas"
        target = s["uc_value"].dropna().astype(str).iloc[0] if s["uc_value"].notna().any() else "URI abfss:// completa"
        if target and not target.startswith("abfss://"):
            target = "abfss://cedulas-recepcion@cu1uatstaucimpinconvolln.dfs.core.windows.net"
        actions.append(make_action(
            f"STORAGE_ABFSS::{config_path.lower()}", "STORAGE",
            "Migrar ruta Cedulas de DBFS a ABFSS",
            "La ruta de Cedulas debe dejar de construirse mediante dbfs:/ y pasar a utilizar una URI ABFSS completa.",
            s,
            f"Actualizar {config_path} con URI abfss:// completa.",
            "Eliminar el prefijo dbfs:/ del notebook y consumir directamente el valor configurado.",
            target,
        ))

    # ACT: hardcoded config file path
    s = storage[(storage["requires_action"].astype(str).str.upper() == "YES") &
                (storage["migration_status"] == "ENV_CONFIG_PATH_REQUIRED")]
    if not s.empty:
        actions.append(make_action(
            "ENV_CONFIG_PATH::CV_EXPLOTACION_CONFIG_FILE_PATH", "CONFIG_PATH",
            "Eliminar hardcode del archivo de configuración",
            "Eliminar la ruta /mnt hardcodeada al archivo 0.0_Configuration.json y utilizar la variable de entorno del job.",
            s,
            "Garantizar que el job exponga CV_EXPLOTACION_CONFIG_FILE_PATH.",
            'Usar sys.env("CV_EXPLOTACION_CONFIG_FILE_PATH").',
            "CV_EXPLOTACION_CONFIG_FILE_PATH",
            source_steps="STEP_15 | STEP_16 | STEP_19 | STEP_20",
        ))

    # ACT: dynamic work schema
    w = working[(working["requires_action"].astype(str).str.upper() == "YES") &
                (working["migration_status"] == "SCHEMA_CONFIGURATION_REQUIRED")]
    if not w.empty:
        config_path = w["config_path"].dropna().astype(str).iloc[0] if w["config_path"].notna().any() else "EsquemasTrabajoDbks_UC.Default"
        # Working table file uses 'job', normalize for helper.
        w2 = w.copy()
        w2["jobs"] = w2["job"]
        actions.append(make_action(
            f"DYNAMIC_WORK_SCHEMA::{config_path.lower()}", "WORK_SCHEMA",
            "Configurar schema dinámico de trabajo cv_work",
            "Sustituir el uso dinámico del esquema legacy default por un schema de trabajo gobernado por Unity Catalog.",
            w2,
            f"Agregar/usar {config_path}",
            f"Construir las tablas dinámicas usando {config_path} en lugar de default.${{nombreTablaSinBaseDeDatos}}.",
            "u_impin_convol.cv_work",
            source_steps="STEP_18 | STEP_19 | STEP_20",
        ))

    # NEW ACT: configured ABFSS path but code still assumes dbfs:<mount>
    s = storage[(storage["requires_action"].astype(str).str.upper() == "YES") &
                (storage["migration_status"] == "CONFIG_DBFS_PREFIX_INCOMPATIBLE_WITH_ABFSS")]
    if not s.empty:
        # Group by config_path so future independent properties become independent master actions.
        for config_path, grp in s.groupby(s["config_path"].fillna("UNRESOLVED_CONFIG_PATH"), dropna=False):
            target = grp["uc_value"].dropna().astype(str).iloc[0] if grp["uc_value"].notna().any() else "ABFSS URI configured in UC"
            occurrences = int(pd.to_numeric(grp.get("occurrences", 0), errors="coerce").fillna(0).sum()) if "occurrences" in grp.columns else 0
            lines = uniq_join(grp.get("line_numbers", pd.Series(dtype=str)).tolist()) if "line_numbers" in grp.columns else ""
            note = f"Active occurrences detected: {occurrences}. Scanner line references: {lines}." if occurrences else ""
            actions.append(make_action(
                f"STORAGE_DBFS_PREFIX::{str(config_path).lower()}", "STORAGE",
                "Ajustar extracción de listados para rutas ABFSS",
                "La configuración UC ya contiene una URI ABFSS válida, pero el código consumidor todavía construye o busca el prefijo legacy dbfs:<mount>. Esto puede impedir derivar correctamente BlobName y BlobPath.",
                grp,
                f"Sin cambio requerido en {config_path}; conservar la URI ABFSS configurada en UC.",
                "Eliminar la dependencia de replaceFirst(dbfs:<mount>). Derivar BlobName como ruta relativa al contenedor/basePath y construir BlobPath explícitamente, preservando la semántica esperada por Parser y controles posteriores.",
                target,
                notes=note,
            ))


    # ACT: external/business tables required by functional notebooks but absent in UC
    t = tables[
        (tables["usada_en_notebook"].astype(str).str.lower() == "true") &
        (
            (tables["migration_action"] == "REGISTER_OR_MIGRATE_TO_UC") |
            (tables["reconciliation_status"] == "EXISTS_AND_USED_UC_NOT_FOUND")
        )
    ].copy()

    if not t.empty:
        # Normalize columns expected by helper.
        if "jobs" not in t.columns:
            t["jobs"] = ""
        if "notebook" not in t.columns:
            t["notebook"] = t.get("notebooks", "")
        else:
            t["notebook"] = t["notebook"].fillna("")
            if "notebooks" in t.columns:
                t.loc[t["notebook"].astype(str).str.strip() == "", "notebook"] = t["notebooks"]

        # helper split_pipe supports pipe-delimited notebook/job lists.
        locations = uniq_join(t.get("location_pro", pd.Series(dtype=str)).tolist())
        pro_objects = uniq_join(t.get("tabla_pro", pd.Series(dtype=str)).tolist())
        uc_objects = uniq_join(t.get("tabla_uc", pd.Series(dtype=str)).tolist())

        notes = (
            "Objetos PRO: " + pro_objects +
            ". Locations PRO: " + locations +
            ". La solicitud de registro/migración debe coordinarse con el equipo responsable de los datos externos."
        )

        actions.append(make_action(
            "DATA_OBJECTS::REGISTER_EXTERNAL_TABLES_UC",
            "DATA_OBJECTS",
            "Registrar/migrar tablas externas requeridas en Unity Catalog",
            "Existen tablas Delta externas utilizadas por notebooks funcionales del assessment que tienen destino UC definido, pero no fueron encontradas en el inventario físico de Unity Catalog.",
            t,
            "Mantener/validar los destinos UC de tres partes definidos para las tablas requeridas.",
            "No se requiere cambio de lógica de lectura una vez que las tablas estén registradas en UC; validar únicamente que los notebooks/configuración consuman el nombre UC correspondiente.",
            uc_objects,
            source_steps="STEP_14 | STEP_19 | STEP_20",
            notes=notes,
        ))

    # ACT: persistent view used by functional notebooks but absent in UC
    v = tables[
        (tables["usada_en_notebook"].astype(str).str.lower() == "true") &
        (
            (tables["migration_action"] == "CREATE_VIEW_IN_UC") |
            (tables["reconciliation_status"] == "USED_VIEW_UC_NOT_FOUND")
        )
    ].copy()

    if not v.empty:
        if "jobs" not in v.columns:
            v["jobs"] = ""
        if "notebook" not in v.columns:
            v["notebook"] = v.get("notebooks", "")
        else:
            v["notebook"] = v["notebook"].fillna("")
            if "notebooks" in v.columns:
                v.loc[v["notebook"].astype(str).str.strip() == "", "notebook"] = v["notebooks"]

        pro_views = uniq_join(v.get("tabla_pro", pd.Series(dtype=str)).tolist())
        uc_views = uniq_join(v.get("tabla_uc", pd.Series(dtype=str)).tolist())
        config_paths = uniq_join(v.get("configuracion_json_uc", pd.Series(dtype=str)).tolist())

        actions.append(make_action(
            "DATA_VIEW::CREATE_MISSING_UC_VIEW",
            "VIEW",
            "Recrear vista persistente requerida en Unity Catalog",
            "Una vista persistente utilizada por notebooks funcionales existe en Hive PRO, pero no fue encontrada en Unity Catalog.",
            v,
            (
                f"Actualizar la configuración UC asociada ({config_paths}) con el nombre UC de tres partes."
                if config_paths
                else "Actualizar la configuración UC asociada con el nombre UC de tres partes."
            ),
            "Recrear la VIEW en UC adaptando el DDL para referenciar objetos UC de tres partes.",
            uc_views,
            source_steps="STEP_14 | STEP_19 | STEP_20",
            notes=f"Vista PRO: {pro_views}.",
        ))

    # Keep output deterministic and IDs stable by semantic ordering.
    # Preserve the historical first three actions and append new action(s).
    order = {
        "STORAGE_ABFSS": 10,
        "ENV_CONFIG_PATH": 20,
        "DYNAMIC_WORK_SCHEMA": 30,
        "STORAGE_DBFS_PREFIX": 40,
        "DATA_OBJECTS": 50,
        "DATA_VIEW": 60,
    }
    actions.sort(key=lambda a: (order.get(a["action_key"].split("::", 1)[0], 999), a["action_key"]))

    rows = []
    for i, action in enumerate(actions, start=1):
        rows.append({"action_id": f"ACT-{i:03d}", **action})

    columns = [
        "action_id", "action_key", "change_type", "title", "description",
        "affected_notebooks", "affected_notebook_count", "affected_jobs",
        "affected_job_count", "config_change", "code_change", "target_value",
        "priority", "status", "source_steps", "notes"
    ]
    result = pd.DataFrame(rows, columns=columns)
    result.to_csv(output_path, index=False)

    print(f"Generated: {output_path}")
    print(f"Master actions: {len(result)}")
    if not result.empty:
        print(result[["action_id", "change_type", "title", "affected_notebook_count", "affected_job_count"]].to_string(index=False))


if __name__ == "__main__":
    main()
