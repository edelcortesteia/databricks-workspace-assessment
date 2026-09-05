#!/usr/bin/env python3
from pathlib import Path
from collections import Counter, defaultdict
import csv
import json
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output"
SNAPSHOT_DIR = PROJECT_ROOT / "snapshot"
INPUT_DIR = PROJECT_ROOT / "input"

HIVE_FILE = OUTPUT_DIR / "hive_table_inventory.csv"
RECON_FILE = OUTPUT_DIR / "table_hive_reconciliation.csv"
TRACE_FILE = OUTPUT_DIR / "dynamic_variable_trace.csv"
NOTEBOOKS_FILE = OUTPUT_DIR / "notebooks.csv"
JOB_INVENTORY_FILE = OUTPUT_DIR / "job_notebook_inventory.csv"
PRO_CONFIG_FILE = INPUT_DIR / "config" / "0.0_Configuration_PROD.json"
UC_CONFIG_FILE = INPUT_DIR / "config" / "0.0_Configuration_UC.json"

UC_INVENTORY_CANDIDATES = [
    INPUT_DIR / "uc" / "00_inventory_tables.csv",
    INPUT_DIR / "00_inventory_tables.csv",
    OUTPUT_DIR / "00_inventory_tables.csv",
]

HIVE_DDL_CANDIDATES = [
    SNAPSHOT_DIR / "ddl" / "hive_ddl.sql",
    INPUT_DIR / "hive_ddl.sql",
    PROJECT_ROOT / "hive_ddl.sql",
]

OUTPUT_HIVE = OUTPUT_DIR / "table_hive_reconciliation_final.csv"
OUTPUT_JDBC = OUTPUT_DIR / "external_jdbc_dependencies.csv"
OUTPUT_WORKING = OUTPUT_DIR / "working_table_references.csv"

HIVE_FIELDS = [
    "tabla_pro","tabla_uc","tipo","usada_en_notebook",
    "physical_exists","ddl_available","physical_status",
    "reconciliation_status","ocurrencias","reference_types",
    "jobs","notebooks","dynamic_variables","configuracion_json_uc",
    "trace_statuses","fuente_mapeo","notas",
    "object_type_pro","provider_pro","location_pro",
    "uc_object_exists","uc_object_type","uc_data_source_format",
    "migration_action",
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


def first_existing(candidates,label):
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"No se encontró {label}. Rutas revisadas:\n" +
        "\n".join(f" - {p}" for p in candidates)
    )


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


def normalize_uc_key(value):
    return clean(value).replace("`","").casefold()


def logical_name_from_uc(value):
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


def resolve_json_path(data,path):
    current=data
    for part in clean(path).split("."):
        if not isinstance(current,dict) or part not in current:
            return None
        current=current[part]
    return current


def flatten_config_value(value):
    out=[]
    if isinstance(value,str):
        out.append(value)
    elif isinstance(value,(int,float,bool)):
        out.append(str(value))
    elif isinstance(value,list):
        for item in value:
            if isinstance(item,dict) and item.get("Destination") is not None:
                out.append(str(item["Destination"]))
            elif isinstance(item,(str,int,float,bool)):
                out.append(str(item))
    return uniq(out)


def build_config_mapping(pro_config,uc_config):
    pro_flat=flatten_config(pro_config)
    uc_flat=flatten_config(uc_config)
    mapping=defaultdict(list)
    mapping_paths=defaultdict(list)
    ambiguous_paths=[]
    for path in sorted(set(pro_flat) | set(uc_flat),key=str.casefold):
        pro_tables=[normalize_pro_table(v) for v in pro_flat.get(path,[])]
        pro_tables=[v for v in pro_tables if v]
        uc_tables=[normalize_uc_table(v) for v in uc_flat.get(path,[])]
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


def remove_comments(code):
    result=[]
    i=0
    n=len(code)
    quote=None
    triple=None
    block=False
    while i<n:
        if block:
            if code[i:i+2]=="*/":
                block=False; i+=2; continue
            if code[i]=="\n":
                result.append("\n")
            i+=1; continue
        if triple:
            if code[i:i+3]==triple:
                result.append(triple); i+=3; triple=None
            else:
                result.append(code[i]); i+=1
            continue
        if quote:
            ch=code[i]; result.append(ch)
            if ch=="\\" and i+1<n:
                result.append(code[i+1]); i+=2; continue
            if ch==quote:
                quote=None
            i+=1; continue
        token3=code[i:i+3]
        if token3 == '"""' or token3 == "'''":
            triple=token3; result.append(token3); i+=3; continue
        if code[i] in {'"',"'"}:
            quote=code[i]; result.append(code[i]); i+=1; continue
        if code[i:i+2]=="/*":
            block=True; i+=2; continue
        if code[i:i+2] in {"//","--"}:
            while i<n and code[i]!="\n":
                i+=1
            continue
        if code[i]=="#":
            while i<n and code[i]!="\n":
                i+=1
            continue
        result.append(code[i]); i+=1
    return "".join(result)


def project_path(relative):
    return PROJECT_ROOT / Path(clean(relative).replace("\\","/"))


def notebook_indexes():
    notebooks=load_csv(NOTEBOOKS_FILE)
    jobs=load_csv(JOB_INVENTORY_FILE)
    path_to_local={}
    for row in notebooks:
        workspace=clean(row.get("workspace_path") or row.get("path"))
        local=clean(row.get("local_file") or row.get("path"))
        if workspace and local:
            path_to_local[workspace]=local
    jobs_by_notebook=defaultdict(list)
    for row in jobs:
        nb=clean(row.get("notebook")); job=clean(row.get("job"))
        if nb and job:
            jobs_by_notebook[nb].append(job)
    return path_to_local,{k:uniq(v) for k,v in jobs_by_notebook.items()}


LITERAL_TABLE_RE = re.compile(
    r'''(?ix)\.\s*table\s*\(\s*["'](?P<table>[A-Za-z0-9_`-]+\.[A-Za-z0-9_`-]+)["']\s*\)'''
)
DIRECT_CONFIG_TABLE_RE = re.compile(
    r'''(?ix)\.\s*table\s*\(\s*parsedConfiguration\.(?P<path>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\)'''
)
CONFIG_ASSIGN_RE = re.compile(
    r'''(?ix)\b(?:val|var)\s+(?P<var>[A-Za-z_]\w*)(?:\s*:\s*[^=\n]+)?\s*=\s*parsedConfiguration\.(?P<path>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'''
)


def variable_used_as_table(code,var):
    escaped=re.escape(var)
    patterns=[
        rf"\.\s*table\s*\(\s*{escaped}\s*\)",
        rf"\bsaveAsTable\s*\(\s*{escaped}\s*\)",
        rf"\binsertInto\s*\(\s*{escaped}\s*\)",
        rf"\btableExists\s*\(\s*{escaped}\s*\)",
        rf"\$\{{\s*{escaped}\s*\}}",
        rf"\$\s*{escaped}\b",
    ]
    return any(re.search(p,code,re.I) for p in patterns)


JDBC_CONFIG_PREFIXES = (
    "PostgreSqlExplotacion.",
    "PostgreSqlReportes.",
    "PostgreSqlCedulas.",
    "PostgreSqlReportesCv.",
    "PostgreSqlMonitoreo.",
    "Reportes.",
    "Recepcion.",
    "Incidencias.",
    "Estadisticos.",
    "CatalogosReportesPostgreSql.",
    "Consulta.",
    "Monitoreo.",
    "TablasPostgresSQL_UC.",
    "SPsPostgresSQL_UC.",
)


def classify_config_reference(config_path):
    path = clean(config_path)
    if any(path.startswith(prefix) for prefix in JDBC_CONFIG_PREFIXES):
        return "JDBC"
    return "SPARK_HIVE"


def infer_standard_uc_target(pro_table):
    """
    Inferencia estándar COVOL:
      schema.table -> u_impin_convol.cv_schema.table

    Se usa únicamente para objetos PRO físicamente existentes y usados
    cuando el JSON UC todavía conserva un valor legacy de dos partes o
    no expone un mapping de tres partes.
    """
    normalized = normalize_pro_table(pro_table)
    if not normalized:
        return ""
    schema, table = normalized.split(".", 1)
    return f"u_impin_convol.cv_{schema}.{table}"

def scan_functional_source_usage(pro_config):
    path_to_local,jobs_by_notebook=notebook_indexes()
    findings=defaultdict(list)
    for notebook,jobs in jobs_by_notebook.items():
        local=path_to_local.get(notebook)
        if not local:
            continue
        file_path=project_path(local)
        if not file_path.exists():
            continue
        code=remove_comments(file_path.read_text(encoding="utf-8",errors="ignore"))

        for m in LITERAL_TABLE_RE.finditer(code):
            pro_table=normalize_pro_table(m.group("table"))
            if pro_table:
                findings[pro_table].append({
                    "notebook":notebook,"jobs":jobs,
                    "reference_type":"SOURCE_SCAN_LITERAL_TABLE",
                    "config_path":"",
                    "data_source":"SPARK_HIVE",
                    "note":"Referencia literal .table(schema.table) detectada por Paso 14 V5.1.",
                })

        for m in DIRECT_CONFIG_TABLE_RE.finditer(code):
            config_path=m.group("path")
            pro_values=flatten_config_value(resolve_json_path(pro_config,config_path))
            for raw in pro_values:
                pro_table=normalize_pro_table(raw)
                if pro_table:
                    findings[pro_table].append({
                        "notebook":notebook,"jobs":jobs,
                        "reference_type":"SOURCE_SCAN_DIRECT_CONFIG_TABLE",
                        "config_path":config_path,
                        "data_source":classify_config_reference(config_path),
                        "note":"Configuración usada directamente como argumento de .table(...).",
                    })

        for m in CONFIG_ASSIGN_RE.finditer(code):
            variable=m.group("var")
            config_path=m.group("path")
            if not variable_used_as_table(code,variable):
                continue
            pro_values=flatten_config_value(resolve_json_path(pro_config,config_path))
            for raw in pro_values:
                pro_table=normalize_pro_table(raw)
                if pro_table:
                    findings[pro_table].append({
                        "notebook":notebook,"jobs":jobs,
                        "reference_type":"SOURCE_SCAN_CONFIG_VARIABLE_TABLE",
                        "config_path":config_path,
                        "data_source":classify_config_reference(config_path),
                        "note":f"{variable} se resuelve desde parsedConfiguration.{config_path} y se usa como referencia de tabla.",
                    })
    return findings


def parse_hive_ddl(path):
    result={}
    if not path or not path.exists():
        return result
    text=path.read_text(encoding="utf-8",errors="ignore")
    marker=re.compile(r"(?m)^--\s*====\s*([A-Za-z0-9_]+\.[A-Za-z0-9_]+)\s*====\s*$")
    matches=list(marker.finditer(text))
    for index,m in enumerate(matches):
        pro_table=normalize_pro_table(m.group(1))
        if not pro_table:
            continue
        start=m.end(); end=matches[index+1].start() if index+1<len(matches) else len(text)
        block=text[start:end]
        if re.search(r"(?i)\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b",block):
            object_type="VIEW"
        elif re.search(r"(?i)\bCREATE\s+TABLE\b",block):
            object_type="TABLE"
        else:
            object_type="UNKNOWN"
        provider_match=re.search(r"(?i)\bUSING\s+([A-Za-z0-9_]+)",block)
        location_match=re.search(r"(?i)\bLOCATION\s+'([^']+)'",block)
        result[pro_table]={
            "object_type_pro":object_type,
            "provider_pro":provider_match.group(1) if provider_match else "",
            "location_pro":location_match.group(1) if location_match else "",
        }
    return result


def build_uc_inventory(rows):
    index={}
    for row in rows:
        full=clean(row.get("full_table_name") or row.get("full_table_name_normalized"))
        key=normalize_uc_key(full)
        if key:
            index[key]={
                "full_table_name":full,
                "table_type":clean(row.get("table_type")),
                "data_source_format":clean(row.get("data_source_format")),
            }
    return index


def main():
    required=[HIVE_FILE,RECON_FILE,TRACE_FILE,NOTEBOOKS_FILE,JOB_INVENTORY_FILE,PRO_CONFIG_FILE,UC_CONFIG_FILE]
    missing=[p for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError("Faltan archivos requeridos:\n" + "\n".join(f" - {p}" for p in missing))

    uc_inventory_file=first_existing(UC_INVENTORY_CANDIDATES,"inventario físico de Unity Catalog (00_inventory_tables.csv)")
    hive_ddl_file=next((p for p in HIVE_DDL_CANDIDATES if p.exists()),None)

    hive_rows=load_csv(HIVE_FILE)
    recon_rows=load_csv(RECON_FILE)
    trace_rows=load_csv(TRACE_FILE)
    uc_inventory_rows=load_csv(uc_inventory_file)
    pro_config=load_json(PRO_CONFIG_FILE)
    uc_config=load_json(UC_CONFIG_FILE)

    direct_map,direct_paths,logical_uc_map,logical_uc_paths,ambiguous_config_paths,pro_flat = build_config_mapping(pro_config,uc_config)
    uc_inventory=build_uc_inventory(uc_inventory_rows)
    ddl_metadata=parse_hive_ddl(hive_ddl_file)

    hive_index={}
    for row in hive_rows:
        key=normalize_pro_table(row.get("full_name") or f"{row.get('schema','')}.{row.get('tabla','')}")
        if key:
            hive_index[key]=row

    state={}
    def ensure(pro_table):
        if pro_table not in state:
            state[pro_table]={
                "used":False,"types":[],"occurrences":0,"reference_types":[],"jobs":[],"notebooks":[],
                "dynamic_variables":[],"trace_statuses":[],"notes":[],"source_config_paths":[],
            }
        return state[pro_table]

    for pro_table in hive_index:
        ensure(pro_table)

    for row in recon_rows:
        if clean(row.get("data_source"))!="SPARK_HIVE":
            continue
        if clean(row.get("source_kind"))!="PHYSICAL_REFERENCE":
            continue
        status=clean(row.get("reconciliation_status"))
        if status not in {"EXISTS_AND_USED","EXISTS_DDL_UNAVAILABLE","REFERENCED_NOT_FOUND"}:
            continue
        pro_table=normalize_pro_table(row.get("normalized_reference") or row.get("table_reference"))
        if not pro_table:
            continue
        item=ensure(pro_table); item["used"]=True; item["types"].append("LITERAL")
        try:
            item["occurrences"] += int(clean(row.get("occurrences")) or "0")
        except ValueError:
            pass
        item["reference_types"].extend(split_pipe(row.get("reference_types")))
        item["jobs"].extend(split_pipe(row.get("jobs")))
        item["notebooks"].extend(split_pipe(row.get("notebooks")))
        item["notes"].append("Referencia literal Spark/Hive detectada en Paso 11.")

    working_rows=[]; working_seen=set(); dynamic_hive_traces=0; dynamic_persistent_materialized=0
    for row in trace_rows:
        if not truth(row.get("used_by_dynamic_table")) or clean(row.get("migration_scope"))!="HMS_TO_UC":
            continue
        dynamic_hive_traces += 1
        variable=clean(row.get("variable")); expression=clean(row.get("source_expression"))
        references=split_pipe(row.get("dynamic_table_references")); config_paths=split_pipe(row.get("config_paths"))
        for reference in references:
            if is_working_reference(reference):
                for path in config_paths:
                    raw_values=uniq(pro_flat.get(path,[])); pro_values=apply_transform(expression,raw_values)
                    for pro_value in pro_values:
                        working_table=materialize_pro_reference(reference,variable,pro_value)
                        if not working_table:
                            continue
                        key=(working_table,reference,variable,clean(row.get("notebook")))
                        if key in working_seen:
                            continue
                        working_seen.add(key)
                        working_rows.append({
                            "working_table":working_table,"dynamic_reference":reference,"variable":variable,
                            "notebook":clean(row.get("notebook")),"jobs":clean(row.get("jobs")),
                            "config_paths":join(config_paths),"trace_status":clean(row.get("trace_status")),
                            "migration_scope":"WORKING_TABLE",
                            "notes":"Referencia dinámica default.* separada del inventario persistente; se analiza en working tables.",
                        })
                continue
            for path in config_paths:
                raw_values=uniq(pro_flat.get(path,[])); pro_values=apply_transform(expression,raw_values)
                for pro_value in pro_values:
                    pro_table=normalize_pro_table(pro_value) if clean(reference)=="${"+variable+"}" else materialize_pro_reference(reference,variable,pro_value)
                    if not pro_table:
                        continue
                    item=ensure(pro_table); item["used"]=True; item["types"].append("DYNAMIC"); item["occurrences"]+=1
                    item["reference_types"].append("DYNAMIC_CONFIG_VALUE"); item["jobs"].extend(split_pipe(row.get("jobs")))
                    item["notebooks"].append(clean(row.get("notebook"))); item["dynamic_variables"].append(variable)
                    item["trace_statuses"].append(clean(row.get("trace_status"))); item["source_config_paths"].append(path)
                    item["notes"].append(f"Referencia dinámica materializada desde {reference}.")
                    dynamic_persistent_materialized += 1

    source_findings=scan_functional_source_usage(pro_config); source_scan_occurrences=0
    source_jdbc_findings=defaultdict(list)

    for pro_table,evidences in source_findings.items():
        for evidence in evidences:
            source_scan_occurrences += 1

            if evidence.get("data_source") == "JDBC":
                source_jdbc_findings[pro_table].append(evidence)
                continue

            item=ensure(pro_table)
            item["used"]=True; item["types"].append("SOURCE_SCAN"); item["occurrences"]+=1
            item["reference_types"].append(evidence["reference_type"]); item["jobs"].extend(evidence["jobs"])
            item["notebooks"].append(evidence["notebook"])
            if evidence["config_path"]:
                item["source_config_paths"].append(evidence["config_path"])
            item["notes"].append(evidence["note"])

    final_rows=[]; status_counts=Counter(); mapping_counts=Counter(); action_counts=Counter()
    used_physical=set(); used_missing_pro=set(); used_uc_missing=set()
    all_pro_tables=sorted(set(hive_index)|set(state),key=str.casefold)

    for pro_table in all_pro_tables:
        item=ensure(pro_table); physical=hive_index.get(pro_table); exists=physical is not None
        uc_candidates=[]; config_paths=[]; mapping_source=""
        if pro_table in direct_map:
            uc_candidates.extend(direct_map[pro_table]); config_paths.extend(direct_paths.get(pro_table,[])); mapping_source="CONFIG_PATH_PRO_UC"
        if not uc_candidates and pro_table in logical_uc_map:
            uc_candidates.extend(logical_uc_map[pro_table]); config_paths.extend(logical_uc_paths.get(pro_table,[])); mapping_source="UC_LOGICAL_MATCH"

        # V5.1: si el objeto existe en PRO, está funcionalmente usado y el
        # JSON UC no aporta un mapping de tres partes, inferir el patrón
        # estándar u_impin_convol.cv_<schema>.<tabla>. Esto permite resolver
        # casos como sumarizado_vista y capa_consumo_conteos_conciliacion.
        if not uc_candidates and exists and item["used"]:
            inferred_uc = infer_standard_uc_target(pro_table)
            if inferred_uc:
                uc_candidates.append(inferred_uc)
                mapping_source="STANDARD_COVOL_UC_INFERENCE"

        config_paths.extend(item["source_config_paths"]); uc_candidates=uniq(uc_candidates); config_paths=uniq(config_paths)

        if len(uc_candidates)==1:
            tabla_uc=uc_candidates[0]; mapping_status=mapping_source or "RESOLVED"
        elif len(uc_candidates)>1:
            tabla_uc=join(uc_candidates); mapping_status="AMBIGUOUS_UC_MAPPING"
        else:
            tabla_uc=""; mapping_status="NO_UC_MAPPING"
        mapping_counts[mapping_status]+=1

        physical_status=clean(physical.get("physical_status")) if physical else "NOT_FOUND_IN_HIVE_SNAPSHOT"
        ddl_available=clean(physical.get("ddl_available")) if physical else ""
        ddl_info=ddl_metadata.get(pro_table,{})
        object_type_pro=clean(ddl_info.get("object_type_pro")) or "TABLE"
        provider_pro=clean(ddl_info.get("provider_pro")); location_pro=clean(ddl_info.get("location_pro"))

        uc_matches=[]
        for candidate in uc_candidates:
            match=uc_inventory.get(normalize_uc_key(candidate))
            if match:
                uc_matches.append(match)

        if len(uc_candidates)==1:
            if uc_matches:
                uc_object_exists="true"; uc_object_type=clean(uc_matches[0].get("table_type")); uc_data_source_format=clean(uc_matches[0].get("data_source_format"))
            else:
                uc_object_exists="false"; uc_object_type=""; uc_data_source_format=""
        elif len(uc_candidates)>1:
            uc_object_exists="AMBIGUOUS"; uc_object_type=join(m.get("table_type","") for m in uc_matches); uc_data_source_format=join(m.get("data_source_format","") for m in uc_matches)
        else:
            uc_object_exists="NO_MAPPING"; uc_object_type=""; uc_data_source_format=""

        migration_action="NO_ACTION"
        if item["used"]:
            if not exists:
                used_missing_pro.add(pro_table); reconciliation_status="REFERENCED_NOT_FOUND"; migration_action="REVIEW_SOURCE_OBJECT"
            elif mapping_status=="AMBIGUOUS_UC_MAPPING":
                used_physical.add(pro_table); reconciliation_status="EXISTS_AND_USED_AMBIGUOUS_UC_MAPPING"; migration_action="RESOLVE_UC_MAPPING"
            elif mapping_status=="NO_UC_MAPPING":
                used_physical.add(pro_table); reconciliation_status="EXISTS_AND_USED_NO_UC_MAPPING"; migration_action="DEFINE_UC_MAPPING"
            elif uc_object_exists=="false":
                used_physical.add(pro_table); used_uc_missing.add(pro_table)
                if object_type_pro=="VIEW":
                    reconciliation_status="USED_VIEW_UC_NOT_FOUND"; migration_action="CREATE_VIEW_IN_UC"
                else:
                    reconciliation_status="EXISTS_AND_USED_UC_NOT_FOUND"; migration_action="REGISTER_OR_MIGRATE_TO_UC"
            else:
                used_physical.add(pro_table)
                reconciliation_status="EXISTS_DDL_UNAVAILABLE_AND_USED" if physical_status=="EXISTS_DDL_UNAVAILABLE" else "EXISTS_AND_USED_UC_FOUND"
        else:
            reconciliation_status="EXISTS_DDL_UNAVAILABLE" if physical_status=="EXISTS_DDL_UNAVAILABLE" else "EXISTS_NOT_USED"

        types=uniq(item["types"])
        if "LITERAL" in types and "DYNAMIC" in types:
            tipo="LITERAL_AND_DYNAMIC"
        elif "SOURCE_SCAN" in types and "DYNAMIC" in types:
            tipo="DYNAMIC_AND_SOURCE_SCAN"
        elif "SOURCE_SCAN" in types and "LITERAL" in types:
            tipo="LITERAL_AND_SOURCE_SCAN"
        elif "DYNAMIC" in types:
            tipo="DYNAMIC_CONFIG"
        elif "LITERAL" in types:
            tipo="LITERAL"
        elif "SOURCE_SCAN" in types:
            tipo="SOURCE_SCAN"
        else:
            tipo="HIVE_INVENTORY"

        status_counts[reconciliation_status]+=1; action_counts[migration_action]+=1
        final_rows.append({
            "tabla_pro":pro_table,"tabla_uc":tabla_uc,"tipo":tipo,"usada_en_notebook":"true" if item["used"] else "false",
            "physical_exists":"true" if exists else "false","ddl_available":ddl_available,"physical_status":physical_status,
            "reconciliation_status":reconciliation_status,"ocurrencias":item["occurrences"],"reference_types":join(item["reference_types"]),
            "jobs":join(item["jobs"]),"notebooks":join(item["notebooks"]),"dynamic_variables":join(item["dynamic_variables"]),
            "configuracion_json_uc":join(config_paths),"trace_statuses":join(item["trace_statuses"]),"fuente_mapeo":mapping_status,
            "notas":join(item["notes"]),"object_type_pro":object_type_pro,"provider_pro":provider_pro,"location_pro":location_pro,
            "uc_object_exists":uc_object_exists,"uc_object_type":uc_object_type,"uc_data_source_format":uc_data_source_format,
            "migration_action":migration_action,
        })

    OUTPUT_HIVE.parent.mkdir(parents=True,exist_ok=True)
    with OUTPUT_HIVE.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=HIVE_FIELDS); writer.writeheader(); writer.writerows(final_rows)

    jdbc_rows=[]
    jdbc_seen=set()

    for row in recon_rows:
        if clean(row.get("reconciliation_status"))!="OUT_OF_SCOPE_JDBC":
            continue

        normalized = clean(row.get("normalized_reference"))
        key = normalized.casefold()
        jdbc_seen.add(key)

        jdbc_rows.append({
            "table_reference":clean(row.get("table_reference")),"normalized_reference":normalized,
            "name_format":clean(row.get("name_format")),"occurrences":clean(row.get("occurrences")),"reference_types":clean(row.get("reference_types")),
            "jobs":clean(row.get("jobs")),"notebooks":clean(row.get("notebooks")),"dynamic_variables":clean(row.get("dynamic_variables")),
            "data_source":"JDBC","migration_scope":"OUT_OF_SCOPE_JDBC","reconciliation_status":"OUT_OF_SCOPE_JDBC","notes":clean(row.get("notes")),
        })

    # V5.1: referencias PostgreSQL detectadas por el source scan no deben
    # contaminar el universo Hive/UC como REFERENCED_NOT_FOUND.
    for table_reference, evidences in sorted(source_jdbc_findings.items()):
        key = table_reference.casefold()
        if key in jdbc_seen:
            continue

        jdbc_rows.append({
            "table_reference":table_reference,
            "normalized_reference":table_reference,
            "name_format":"TWO_PART",
            "occurrences":str(len(evidences)),
            "reference_types":join(e.get("reference_type","") for e in evidences),
            "jobs":join(job for e in evidences for job in e.get("jobs",[])),
            "notebooks":join(e.get("notebook","") for e in evidences),
            "dynamic_variables":"",
            "data_source":"JDBC",
            "migration_scope":"OUT_OF_SCOPE_JDBC",
            "reconciliation_status":"OUT_OF_SCOPE_JDBC",
            "notes":join(
                [
                    "Referencia PostgreSQL detectada desde configuración durante el source scan V5.1.",
                    *[f"Config: {e.get('config_path','')}" for e in evidences if e.get("config_path")]
                ]
            ),
        })
        jdbc_seen.add(key)
    with OUTPUT_JDBC.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=JDBC_FIELDS); writer.writeheader(); writer.writerows(jdbc_rows)
    with OUTPUT_WORKING.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=WORKING_FIELDS); writer.writeheader(); writer.writerows(working_rows)

    print("="*78)
    print("ASSESSMENT WORKSPACE - PASO 14 V5.1")
    print("INVENTARIO FINAL DE OBJETOS PRO -> UNITY CATALOG")
    print("="*78)
    print()
    print("--- Entradas ---")
    print(f"Objetos físicos Hive PRO        : {len(hive_index)}")
    print(f"Objetos reconciliados Paso 11   : {len(recon_rows)}")
    print(f"Trazas Paso 13                  : {len(trace_rows)}")
    print(f"Inventario físico UC            : {len(uc_inventory)}")
    print(f"DDL Hive                        : {hive_ddl_file or 'NO DISPONIBLE'}")
    print(f"Inventario UC                   : {uc_inventory_file}")
    print()
    print("--- Referencias funcionales ---")
    print(f"Trazas activas HMS_TO_UC        : {dynamic_hive_traces}")
    print(f"Persistentes materializadas     : {dynamic_persistent_materialized}")
    print(f"Hallazgos source scan V5        : {source_scan_occurrences}")
    print(f"Working tables separadas        : {len(working_rows)}")
    print()
    print("--- Inventario final ---")
    print(f"Objetos consolidados            : {len(final_rows)}")
    print(f"Objetos PRO usados              : {len(used_physical | used_missing_pro)}")
    print(f"Referencias no encontradas PRO  : {len(used_missing_pro)}")
    print(f"Objetos usados ausentes en UC   : {len(used_uc_missing)}")
    print()
    print("Resumen por estado:")
    for status in sorted(status_counts):
        print(f" - {status:<44}: {status_counts[status]}")
    print()
    print("Resumen por acción:")
    for action in sorted(action_counts):
        print(f" - {action:<44}: {action_counts[action]}")
    print()
    print("--- Objetos usados que NO existen en UC ---")
    pending=[row for row in final_rows if row["migration_action"] in {"REGISTER_OR_MIGRATE_TO_UC","CREATE_VIEW_IN_UC"}]
    if pending:
        for row in pending:
            print(f" - {row['tabla_pro']} -> {row['tabla_uc']} | {row['object_type_pro']} | {row['migration_action']}")
    else:
        print(" - Ninguno")
    print()
    print("--- Mapeo PRO -> UC ---")
    for status in sorted(mapping_counts):
        print(f" - {status:<44}: {mapping_counts[status]}")
    print(f"Config paths ambiguos ignorados : {len(ambiguous_config_paths)}")
    print()
    print(f"Objetos JDBC fuera de alcance   : {len(jdbc_rows)}")
    print()
    print(f"Archivo objetos : {OUTPUT_HIVE.resolve()}")
    print(f"Archivo JDBC    : {OUTPUT_JDBC.resolve()}")
    print(f"Archivo working : {OUTPUT_WORKING.resolve()}")
    print()
    print("="*78)
    print("RESULTADO: INVENTARIO FINAL PRO -> UC GENERADO")
    print("="*78)


if __name__=="__main__":
    main()
