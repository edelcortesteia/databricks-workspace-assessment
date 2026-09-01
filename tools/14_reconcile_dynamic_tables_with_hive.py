#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import re

HIVE_FILE = Path("output/hive_table_inventory.csv")
RECON_FILE = Path("output/table_hive_reconciliation.csv")
TRACE_FILE = Path("output/dynamic_variable_trace.csv")
PRO_CONFIG_FILE = Path("input/config/0.0_Configuration_PROD.json")
UC_CONFIG_FILE = Path("input/config/0.0_Configuration_UC.json")

OUTPUT_HIVE = Path("output/table_hive_reconciliation_final.csv")
OUTPUT_JDBC = Path("output/external_jdbc_dependencies.csv")
OUTPUT_WORKING = Path("output/working_table_references.csv")

HIVE_FIELDS = [
    "tabla_pro","tabla_uc","tipo","usada_en_notebook",
    "physical_exists","ddl_available","physical_status",
    "reconciliation_status","ocurrencias","reference_types",
    "jobs","notebooks","dynamic_variables","configuracion_json_uc",
    "trace_statuses","fuente_mapeo","notas",
]

JDBC_FIELDS = [
    "table_reference","normalized_reference","name_format",
    "occurrences","reference_types","jobs","notebooks",
    "dynamic_variables","data_source","migration_scope",
    "reconciliation_status","notes",
]

WORKING_FIELDS = [
    "working_table","dynamic_reference","variable","notebook",
    "jobs","config_paths","trace_status","migration_scope","notes",
]


def clean(v):
    return str(v or "").strip()


def truth(v):
    return clean(v).casefold() in {"true","1","yes","si","sí"}


def uniq(values):
    result=[]; seen=set()
    for value in values:
        value=clean(value)
        if not value:
            continue
        key=value.casefold()
        if key not in seen:
            seen.add(key); result.append(value)
    return result


def join(values):
    return " | ".join(uniq(values))


def split_pipe(value):
    return [x.strip() for x in clean(value).split(" | ") if x.strip()]


def load_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))


def load_json(path):
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    with path.open("r",encoding="utf-8-sig") as f:
        return json.load(f)


def normalize_pro_table(value):
    value=clean(value).replace("`","")
    parts=[p.strip() for p in value.split(".") if p.strip()]
    if len(parts)==3 and parts[0].casefold()=="hive_metastore":
        parts=parts[1:]
    if len(parts)!=2:
        return ""
    return ".".join(parts).casefold()


def normalize_uc_table(value):
    value=clean(value).replace("`","")
    parts=[p.strip() for p in value.split(".") if p.strip()]
    if len(parts)!=3:
        return ""
    return ".".join(parts)


def logical_name_from_uc(value):
    """
    u_impin_convol.cv_bronce_recepcion.errores_intentos
    -> bronce_recepcion.errores_intentos
    """
    value=normalize_uc_table(value)
    if not value:
        return ""
    _,schema,table=value.split(".",2)
    if schema.casefold().startswith("cv_"):
        schema=schema[3:]
    return normalize_pro_table(f"{schema}.{table}")


def flatten_config(data,prefix=""):
    result=defaultdict(list)

    if isinstance(data,dict):
        for key,value in data.items():
            path=f"{prefix}.{key}" if prefix else key

            if isinstance(value,dict):
                nested=flatten_config(value,path)
                for nested_path,values in nested.items():
                    result[nested_path].extend(values)

            elif isinstance(value,list):
                destination_values=[]
                for item in value:
                    if isinstance(item,dict) and item.get("Destination") is not None:
                        destination_values.append(str(item["Destination"]))
                    elif isinstance(item,(str,int,float,bool)):
                        destination_values.append(str(item))

                if destination_values:
                    result[path].extend(destination_values)

            elif isinstance(value,(str,int,float,bool)):
                result[path].append(str(value))

    return {path:uniq(values) for path,values in result.items()}


def build_config_mapping(pro_config,uc_config):
    """
    Construye PRO -> UC preservando la misma config_path cuando sea posible,
    con las mismas reglas de cardinalidad usadas en Herramienta 1.
    """
    pro_flat=flatten_config(pro_config)
    uc_flat=flatten_config(uc_config)

    mapping=defaultdict(list)
    mapping_paths=defaultdict(list)
    ambiguous_paths=[]

    for path in sorted(set(pro_flat) | set(uc_flat),key=str.casefold):
        pro_tables=[
            normalize_pro_table(v)
            for v in pro_flat.get(path,[])
        ]
        pro_tables=[v for v in pro_tables if v]

        uc_tables=[
            normalize_uc_table(v)
            for v in uc_flat.get(path,[])
        ]
        uc_tables=[v for v in uc_tables if v]

        if not pro_tables or not uc_tables:
            continue

        if len(pro_tables)==len(uc_tables):
            pairs=list(zip(pro_tables,uc_tables))
        elif len(pro_tables)==1:
            pairs=[(pro_tables[0],uc) for uc in uc_tables]
        elif len(uc_tables)==1:
            pairs=[(pro,uc_tables[0]) for pro in pro_tables]
        else:
            ambiguous_paths.append(path)
            continue

        for pro_table,uc_table in pairs:
            mapping[pro_table].append(uc_table)
            mapping_paths[pro_table].append(path)

    logical_uc=defaultdict(list)
    logical_paths=defaultdict(list)

    for path,values in uc_flat.items():
        for value in values:
            uc_table=normalize_uc_table(value)
            if not uc_table:
                continue
            logical=logical_name_from_uc(uc_table)
            if logical:
                logical_uc[logical].append(uc_table)
                logical_paths[logical].append(path)

    return (
        {k:uniq(v) for k,v in mapping.items()},
        {k:uniq(v) for k,v in mapping_paths.items()},
        {k:uniq(v) for k,v in logical_uc.items()},
        {k:uniq(v) for k,v in logical_paths.items()},
        ambiguous_paths,
        pro_flat,
    )


def apply_transform(expression,values):
    expression=clean(expression)
    match=re.search(
        r"\.split\s*\(\s*[\"'](?:\\\\\.|\\\.|\.)[\"']\s*\)\s*\(\s*(\d+)\s*\)",
        expression,
    )
    if not match:
        return uniq(values)

    index=int(match.group(1))
    out=[]
    for value in values:
        parts=clean(value).split(".")
        if 0 <= index < len(parts):
            out.append(parts[index])
    return uniq(out)


def materialize_pro_reference(reference,variable,pro_value):
    token="${" + variable + "}"
    if token not in reference:
        return ""
    return normalize_pro_table(reference.replace(token,pro_value))


def is_working_reference(reference):
    compact=re.sub(r"\s+","",clean(reference)).casefold()
    return compact.startswith("default.${")


def main():
    hive_rows=load_csv(HIVE_FILE)
    recon_rows=load_csv(RECON_FILE)
    trace_rows=load_csv(TRACE_FILE)
    pro_config=load_json(PRO_CONFIG_FILE)
    uc_config=load_json(UC_CONFIG_FILE)

    (
        direct_map,
        direct_paths,
        logical_uc_map,
        logical_uc_paths,
        ambiguous_config_paths,
        pro_flat,
    )=build_config_mapping(pro_config,uc_config)

    hive_index={}
    for row in hive_rows:
        key=normalize_pro_table(
            row.get("full_name")
            or f"{row.get('schema','')}.{row.get('tabla','')}"
        )
        if key:
            hive_index[key]=row

    state={}

    def ensure(pro_table):
        if pro_table not in state:
            state[pro_table]={
                "used":False,"types":[],"occurrences":0,
                "reference_types":[],"jobs":[],"notebooks":[],
                "dynamic_variables":[],"trace_statuses":[],"notes":[],
            }
        return state[pro_table]

    for pro_table in hive_index:
        ensure(pro_table)

    # 1. Literales Spark/Hive ya conciliadas por Paso 11.
    for row in recon_rows:
        if clean(row.get("data_source"))!="SPARK_HIVE":
            continue
        if clean(row.get("source_kind"))!="PHYSICAL_REFERENCE":
            continue

        status=clean(row.get("reconciliation_status"))
        if status not in {
            "EXISTS_AND_USED","EXISTS_DDL_UNAVAILABLE","REFERENCED_NOT_FOUND"
        }:
            continue

        pro_table=normalize_pro_table(
            row.get("normalized_reference") or row.get("table_reference")
        )
        if not pro_table:
            continue

        item=ensure(pro_table)
        item["used"]=True
        item["types"].append("LITERAL")

        try:
            item["occurrences"] += int(clean(row.get("occurrences")) or "0")
        except ValueError:
            pass

        item["reference_types"].extend(split_pipe(row.get("reference_types")))
        item["jobs"].extend(split_pipe(row.get("jobs")))
        item["notebooks"].extend(split_pipe(row.get("notebooks")))
        item["notes"].append("Referencia literal Spark/Hive detectada en Paso 11")

    # 2. Dinámicas HMS_TO_UC.
    working_rows=[]
    working_seen=set()
    dynamic_hive_traces=0
    dynamic_persistent_materialized=0

    for row in trace_rows:
        if not truth(row.get("used_by_dynamic_table")):
            continue
        if clean(row.get("migration_scope"))!="HMS_TO_UC":
            continue

        dynamic_hive_traces += 1

        variable=clean(row.get("variable"))
        expression=clean(row.get("source_expression"))
        references=split_pipe(row.get("dynamic_table_references"))
        config_paths=split_pipe(row.get("config_paths"))

        for reference in references:
            # Las default.${...} son working tables, no tablas persistentes.
            if is_working_reference(reference):
                for path in config_paths:
                    raw_values=uniq(pro_flat.get(path,[]))
                    pro_values=apply_transform(expression,raw_values)

                    for pro_value in pro_values:
                        working_table=materialize_pro_reference(
                            reference,variable,pro_value
                        )
                        if not working_table:
                            continue

                        key=(
                            working_table,reference,variable,
                            clean(row.get("notebook"))
                        )
                        if key in working_seen:
                            continue

                        working_seen.add(key)
                        working_rows.append({
                            "working_table":working_table,
                            "dynamic_reference":reference,
                            "variable":variable,
                            "notebook":clean(row.get("notebook")),
                            "jobs":clean(row.get("jobs")),
                            "config_paths":join(config_paths),
                            "trace_status":clean(row.get("trace_status")),
                            "migration_scope":"WORKING_TABLE",
                            "notes":(
                                "Referencia dinámica default.* separada del "
                                "inventario físico Hive; su destino UC se resuelve "
                                "en el análisis específico de working tables."
                            ),
                        })
                continue

            # Dinámica persistente.
            for path in config_paths:
                raw_values=uniq(pro_flat.get(path,[]))
                pro_values=apply_transform(expression,raw_values)

                for pro_value in pro_values:
                    if clean(reference)=="${" + variable + "}":
                        pro_table=normalize_pro_table(pro_value)
                    else:
                        pro_table=materialize_pro_reference(
                            reference,variable,pro_value
                        )

                    if not pro_table:
                        continue

                    item=ensure(pro_table)
                    item["used"]=True
                    item["types"].append("DYNAMIC")
                    item["occurrences"] += 1
                    item["reference_types"].append("DYNAMIC_CONFIG_VALUE")
                    item["jobs"].extend(split_pipe(row.get("jobs")))
                    item["notebooks"].append(clean(row.get("notebook")))
                    item["dynamic_variables"].append(variable)
                    item["trace_statuses"].append(clean(row.get("trace_status")))
                    item["notes"].append(
                        f"Referencia dinámica materializada desde {reference}"
                    )
                    dynamic_persistent_materialized += 1

    # 3. Inventario final + mapping PRO -> UC para TODAS las tablas físicas.
    final_rows=[]
    status_counts=Counter()
    mapping_counts=Counter()
    used_physical=set()
    used_missing=set()

    all_pro_tables=sorted(set(hive_index) | set(state),key=str.casefold)

    for pro_table in all_pro_tables:
        item=ensure(pro_table)
        physical=hive_index.get(pro_table)
        exists=physical is not None

        uc_candidates=[]
        config_paths=[]
        mapping_source=""

        if pro_table in direct_map:
            uc_candidates.extend(direct_map[pro_table])
            config_paths.extend(direct_paths.get(pro_table,[]))
            mapping_source="CONFIG_PATH_PRO_UC"

        if not uc_candidates and pro_table in logical_uc_map:
            uc_candidates.extend(logical_uc_map[pro_table])
            config_paths.extend(logical_uc_paths.get(pro_table,[]))
            mapping_source="UC_LOGICAL_MATCH"

        uc_candidates=uniq(uc_candidates)
        config_paths=uniq(config_paths)

        if len(uc_candidates)==1:
            tabla_uc=uc_candidates[0]
            mapping_status=mapping_source or "RESOLVED"
        elif len(uc_candidates)>1:
            tabla_uc=join(uc_candidates)
            mapping_status="AMBIGUOUS_UC_MAPPING"
        else:
            tabla_uc=""
            mapping_status="NO_UC_MAPPING"

        mapping_counts[mapping_status] += 1

        physical_status=(
            clean(physical.get("physical_status"))
            if physical else "NOT_FOUND_IN_HIVE_SNAPSHOT"
        )
        ddl_available=clean(physical.get("ddl_available")) if physical else ""

        if item["used"]:
            if exists:
                used_physical.add(pro_table)
                reconciliation_status=(
                    "EXISTS_DDL_UNAVAILABLE_AND_USED"
                    if physical_status=="EXISTS_DDL_UNAVAILABLE"
                    else "EXISTS_AND_USED"
                )
            else:
                used_missing.add(pro_table)
                reconciliation_status="REFERENCED_NOT_FOUND"
        else:
            reconciliation_status=(
                "EXISTS_DDL_UNAVAILABLE"
                if physical_status=="EXISTS_DDL_UNAVAILABLE"
                else "EXISTS_NOT_USED"
            )

        types=uniq(item["types"])
        if "LITERAL" in types and "DYNAMIC" in types:
            tipo="LITERAL_AND_DYNAMIC"
        elif "DYNAMIC" in types:
            tipo="DYNAMIC_CONFIG"
        elif "LITERAL" in types:
            tipo="LITERAL"
        else:
            tipo="HIVE_INVENTORY"

        status_counts[reconciliation_status] += 1

        final_rows.append({
            "tabla_pro":pro_table,
            "tabla_uc":tabla_uc,
            "tipo":tipo,
            "usada_en_notebook":"true" if item["used"] else "false",
            "physical_exists":"true" if exists else "false",
            "ddl_available":ddl_available,
            "physical_status":physical_status,
            "reconciliation_status":reconciliation_status,
            "ocurrencias":item["occurrences"],
            "reference_types":join(item["reference_types"]),
            "jobs":join(item["jobs"]),
            "notebooks":join(item["notebooks"]),
            "dynamic_variables":join(item["dynamic_variables"]),
            "configuracion_json_uc":join(config_paths),
            "trace_statuses":join(item["trace_statuses"]),
            "fuente_mapeo":mapping_status,
            "notas":join(item["notes"]),
        })

    OUTPUT_HIVE.parent.mkdir(parents=True,exist_ok=True)

    with OUTPUT_HIVE.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=HIVE_FIELDS)
        writer.writeheader(); writer.writerows(final_rows)

    # 4. JDBC separado.
    jdbc_rows=[]
    for row in recon_rows:
        if clean(row.get("reconciliation_status"))!="OUT_OF_SCOPE_JDBC":
            continue
        jdbc_rows.append({
            "table_reference":clean(row.get("table_reference")),
            "normalized_reference":clean(row.get("normalized_reference")),
            "name_format":clean(row.get("name_format")),
            "occurrences":clean(row.get("occurrences")),
            "reference_types":clean(row.get("reference_types")),
            "jobs":clean(row.get("jobs")),
            "notebooks":clean(row.get("notebooks")),
            "dynamic_variables":clean(row.get("dynamic_variables")),
            "data_source":"JDBC",
            "migration_scope":"OUT_OF_SCOPE_JDBC",
            "reconciliation_status":"OUT_OF_SCOPE_JDBC",
            "notes":clean(row.get("notes")),
        })

    with OUTPUT_JDBC.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=JDBC_FIELDS)
        writer.writeheader(); writer.writerows(jdbc_rows)

    # 5. Working tables separadas.
    with OUTPUT_WORKING.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=WORKING_FIELDS)
        writer.writeheader(); writer.writerows(working_rows)

    print("="*70)
    print("ASSESSMENT WORKSPACE - PASO 14")
    print("INVENTARIO FINAL DE TABLAS PRO -> UC - V4")
    print("="*70)
    print()

    print("--- Entradas ---")
    print(f"Tablas físicas Hive              : {len(hive_index)}")
    print(f"Objetos reconciliados Paso 11    : {len(recon_rows)}")
    print(f"Trazas Paso 13                   : {len(trace_rows)}")
    print(f"Trazas activas HMS_TO_UC         : {dynamic_hive_traces}")
    print()

    print("--- Referencias dinámicas ---")
    print(f"Persistentes materializadas      : {dynamic_persistent_materialized}")
    print(f"Working tables separadas         : {len(working_rows)}")
    print()

    print("--- Inventario físico Hive ---")
    print(f"Objetos consolidados             : {len(final_rows)}")
    print(f"Tablas físicas usadas            : {len(used_physical)}")
    print(f"Referencias Hive no encontradas  : {len(used_missing)}")
    print()

    print("Resumen por estado:")
    for status in sorted(status_counts):
        print(f" - {status:<38}: {status_counts[status]}")

    print()
    print("--- Mapeo PRO -> UC ---")
    for status in sorted(mapping_counts):
        print(f" - {status:<38}: {mapping_counts[status]}")
    print(f"Config paths ambiguos ignorados  : {len(ambiguous_config_paths)}")
    print()

    print("--- Dependencias externas ---")
    print(f"Objetos JDBC fuera de alcance    : {len(jdbc_rows)}")
    print()

    print(f"Archivo tablas : {OUTPUT_HIVE.resolve()}")
    print(f"Archivo JDBC   : {OUTPUT_JDBC.resolve()}")
    print(f"Archivo working: {OUTPUT_WORKING.resolve()}")
    print()

    print("="*70)
    print("RESULTADO: INVENTARIO FINAL DE TABLAS GENERADO")
    print("="*70)


if __name__=="__main__":
    main()
