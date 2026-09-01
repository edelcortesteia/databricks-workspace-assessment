from pathlib import Path
from collections import Counter
import csv
import json
import re

NOTEBOOKS_FILE = Path('output/notebooks.csv')
JOB_INVENTORY_FILE = Path('output/job_notebook_inventory.csv')
DYNAMIC_INVENTORY_FILE = Path('output/dynamic_configuration_inventory.csv')
PRO_CONFIG_FILE = Path('input/config/0.0_Configuration_PROD.json')
UC_CONFIG_FILE = Path('input/config/0.0_Configuration_UC.json')
OUTPUT_FILE = Path('output/dynamic_variable_trace.csv')

FIELDS = [
    'notebook','cell','variable','source_type','source_category','source_expression',
    'data_source','migration_scope',
    'trace_method','trace_depth','config_paths','pro_values','uc_values','resolved_literals','trace_status',
    'trace','used_by_dynamic_table','dynamic_table_references','jobs'
]
CONFIG_RE = re.compile(r'\bparsedConfiguration\.([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)')


def clean(v): return str(v or '').strip()
def uniq(xs):
    out=[]; seen=set()
    for x in xs:
        x=clean(x)
        if x and x.casefold() not in seen:
            seen.add(x.casefold()); out.append(x)
    return out

def load_csv(path):
    if not path.exists(): raise FileNotFoundError(f'No existe: {path}')
    with path.open('r', newline='', encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def project_path(v): return Path('.') / Path(clean(v).replace('\\','/'))


def load_json(path):
    if not path.exists():
        raise FileNotFoundError(f'No existe: {path}')
    with path.open('r', encoding='utf-8-sig') as f:
        return json.load(f)

def resolve_json_path(data, path):
    current=data
    for part in path.split('.'):
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
            if isinstance(item,dict) and item.get('Destination') is not None:
                out.append(str(item['Destination']))
            elif isinstance(item,(str,int,float,bool)):
                out.append(str(item))
    return uniq(out)

def values_from_config_path(path,pro_config,uc_config):
    return (
        flatten_config_value(resolve_json_path(pro_config,path)),
        flatten_config_value(resolve_json_path(uc_config,path)),
    )

def empty(): return {'paths':[],'literals':[],'trace':[],'resolved':False,'cycle':False,'depth':0}
def merge(a,b):
    a['paths'] += b['paths']; a['literals'] += b['literals']; a['trace'] += b['trace']
    a['resolved'] |= b['resolved']; a['cycle'] |= b['cycle']; a['depth']=max(a['depth'],b['depth']); return a

# Databricks Source: separar COMMAND y activar MAGIC.
def blocks(path):
    text=path.read_text(encoding='utf-8', errors='ignore')
    parts=re.split(r'(?m)^\s*(?://|#|--)\s*COMMAND\s*-+\s*$', text)
    out=[]
    for part in parts:
        lines=[]
        for line in part.splitlines():
            m=re.match(r'^\s*(?://|#|--)\s*MAGIC\s?(.*)$', line, re.I)
            lines.append(m.group(1) if m else line)
        out.append('\n'.join(lines))
    return out

# Misma regla endurecida de Assessment 2: //, /* */, # y -- son comentarios
# fuera de strings, sin depender del lenguaje declarado.
def remove_comments(code):
    out=[]; i=0; n=len(code); quote=None; triple=None; block=False
    while i<n:
        if block:
            if code[i:i+2]=='*/': block=False; i+=2
            else:
                if code[i]=='\n': out.append('\n')
                i+=1
            continue
        if triple:
            if code[i:i+3]==triple: out.append(triple); i+=3; triple=None
            else: out.append(code[i]); i+=1
            continue
        if quote:
            ch=code[i]; out.append(ch)
            if ch=='\\' and i+1<n: out.append(code[i+1]); i+=2; continue
            if ch==quote: quote=None
            i+=1; continue
        t3=code[i:i+3]
        if t3=='"""' or t3=="'''": triple=t3; out.append(t3); i+=3; continue
        if code[i] in {'"',"'"}: quote=code[i]; out.append(code[i]); i+=1; continue
        if code[i:i+2]=='/*': block=True; i+=2; continue
        if code[i:i+2] in {'//','--'}:
            while i<n and code[i]!='\n': i+=1
            continue
        if code[i]=='#':
            while i<n and code[i]!='\n': i+=1
            continue
        out.append(code[i]); i+=1
    return ''.join(out)

def matching(text,start,op='(',cl=')'):
    if start<0 or start>=len(text) or text[start]!=op: return -1
    level=0; quote=None; esc=False
    for i in range(start,len(text)):
        c=text[i]
        if quote:
            if esc: esc=False
            elif c=='\\': esc=True
            elif c==quote: quote=None
            continue
        if c in {'"',"'"}: quote=c; continue
        if c==op: level+=1
        elif c==cl:
            level-=1
            if level==0: return i
    return -1

def split_args(text):
    parts=[]; cur=[]; p=b=r=0; quote=None; esc=False
    for c in text:
        if quote:
            cur.append(c)
            if esc: esc=False
            elif c=='\\': esc=True
            elif c==quote: quote=None
            continue
        if c in {'"',"'"}: quote=c; cur.append(c); continue
        if c=='(': p+=1
        elif c==')': p-=1
        elif c=='[': b+=1
        elif c==']': b-=1
        elif c=='{': r+=1
        elif c=='}': r-=1
        if c==',' and p==b==r==0: parts.append(''.join(cur).strip()); cur=[]
        else: cur.append(c)
    if cur: parts.append(''.join(cur).strip())
    return parts

def config_paths(text): return uniq(CONFIG_RE.findall(text or ''))

def assignment(var,code):
    pat=re.compile(rf'(?ix)\b(?:lazy\s+val|val|var)\s+{re.escape(var)}(?:\s*:\s*[^=\n]+)?\s*=\s*')
    m=pat.search(code)
    if not m: return ''
    tail=code[m.end():]; lines=tail.splitlines(); got=[]; p=b=r=0; q=None; esc=False; opened=False
    for line in lines:
        got.append(line)
        for c in line:
            if q:
                if esc: esc=False
                elif c=='\\': esc=True
                elif c==q: q=None
                continue
            if c in {'"',"'"}: q=c; continue
            if c=='(': p+=1; opened=True
            elif c==')': p-=1
            elif c=='[': b+=1; opened=True
            elif c==']': b-=1
            elif c=='{': r+=1; opened=True
            elif c=='}': r-=1
        if not opened or (p<=0 and b<=0 and r<=0 and q is None): break
    return '\n'.join(got).strip().rstrip(';')

def function_name(expr):
    m=re.search(r'parameter\s+of\s+([A-Za-z_]\w*)',expr,re.I); return m.group(1) if m else ''

def parameter_index(fn,var,code):
    m=re.search(rf'\bdef\s+{re.escape(fn)}\s*\(',code)
    if not m: return None
    op=code.find('(',m.start()); cl=matching(code,op)
    if cl<0: return None
    for i,p in enumerate(split_args(code[op+1:cl])):
        if p.split(':')[0].strip()==var: return i
    return None


def functions_with_parameter(variable,code):
    found=[]
    token=re.compile(r'\bdef\s+([A-Za-z_]\w*)\s*\(')
    for m in token.finditer(code):
        fn=m.group(1)
        op=code.find('(',m.start()); cl=matching(code,op)
        if cl<0: continue
        for param in split_args(code[op+1:cl]):
            if param.split(':')[0].strip()==variable:
                found.append(fn); break
    return uniq(found)

def calls(fn,code):
    out=[]; token=re.compile(rf'\b{re.escape(fn)}\s*\(')
    for m in token.finditer(code):
        if re.search(r'\bdef\s*$',code[max(0,m.start()-30):m.start()]): continue
        op=code.find('(',m.start()); cl=matching(code,op)
        if cl<0: continue
        out.append((code[op+1:cl], code[max(0,m.start()-3000):min(len(code),cl+1000)]))
    return out

def iterator_collections(var,code):
    pat=re.compile(rf'(?ix)([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*(?:map|foreach|flatMap|filter)\s*\(\s*{re.escape(var)}\s*=>')
    return uniq(m.group(1) for m in pat.finditer(code))


def collection_producers(target,code):
    """Detecta source.foreach(target.add)."""
    pat=re.compile(
        rf'(?ix)([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)'
        rf'\s*\.\s*foreach\s*\(\s*{re.escape(target)}\.add\s*\)'
    )
    return uniq(m.group(1) for m in pat.finditer(code))

def strip_suffixes(x):
    x=x.strip(); changed=True
    while changed:
        changed=False
        for s in ('.asScala','.par','.toList','.collect'):
            if x.endswith(s): x=x[:-len(s)].strip(); changed=True
    return x

def resolve_collection(expr,local,whole,visited=None,depth=0,all_code=None):
    visited=set() if visited is None else set(visited); res=empty(); res['depth']=depth; expr=clean(expr)
    if not expr: return res
    key='COL:'+expr
    if key in visited: res['cycle']=True; res['trace'].append('CYCLE: '+expr); return res
    visited.add(key); res['trace'].append('COLLECTION: '+expr)
    ps=config_paths(expr)
    if ps: res['paths']+=ps; res['resolved']=True
    if re.match(r'^List\s*\(',expr,re.I):
        op=expr.find('('); cl=matching(expr,op)
        if cl>=0:
            for a in split_args(expr[op+1:cl]): merge(res,resolve_expr(a,local,whole,visited,depth+1,all_code))
        return res
    stripped=strip_suffixes(expr)
    if stripped!=expr: merge(res,resolve_collection(stripped,local,whole,visited,depth+1,all_code)); return res
    if re.fullmatch(r'[A-Za-z_]\w*',stripped):
        a=assignment(stripped,local) or assignment(stripped,whole)
        if a:
            res['trace'].append(f'{stripped} -> {a}')
            merge(res,resolve_collection(a,local,whole,visited,depth+1,all_code))
            if res['resolved']: return res
        producers=uniq(collection_producers(stripped,local)+collection_producers(stripped,whole))
        for producer in producers:
            res['trace'].append(f'{producer}.foreach({stripped}.add)')
            merge(res,resolve_collection(producer,local,whole,visited,depth+1,all_code))
        return res
    # Concatenaciones simples de colecciones observadas en la herramienta 1.
    if '++' in expr:
        for part in expr.split('++'): merge(res,resolve_collection(part.strip(),local,whole,visited,depth+1))
    m=re.match(r'^([A-Za-z_]\w*)\s*\.\s*map\s*\(',expr)
    if m: merge(res,resolve_collection(m.group(1),local,whole,visited,depth+1))
    return res

def transform(expr,vals):
    m=re.search(r'\.split\(\s*["\']\\\\\.["\']\s*\)\s*\(\s*(\d+)\s*\)',expr)
    if not m: return vals
    idx=int(m.group(1)); out=[]
    for v in vals:
        p=str(v).split('.')
        if 0<=idx<len(p): out.append(p[idx])
    return out


def select_items(expr):
    ms=list(re.finditer(r'\.select\s*\(',expr))
    if not ms: return []
    op=expr.find('(',ms[-1].start()); cl=matching(expr,op)
    return split_args(expr[op+1:cl]) if cl>=0 else []

def output_name(item):
    m=re.search(r'\.(?:alias|as)\(\s*["\']([^"\']+)["\']\s*\)',item)
    if m: return m.group(1)
    m=re.search(r'\$["\'](?:[A-Za-z_]\w*\.)?([^"\']+)["\']',item)
    if m: return m.group(1)
    m=re.search(r'\bcol\(\s*["\'](?:[A-Za-z_]\w*\.)?([^"\']+)["\']\s*\)',item)
    return m.group(1) if m else ''

def source_column(item):
    m=re.search(r'\$["\'](?:[A-Za-z_]\w*\.)?([^"\']+)["\']',item)
    if not m:
        m=re.search(r'\bcol\(\s*["\'](?:[A-Za-z_]\w*\.)?([^"\']+)["\']\s*\)',item)
    return m.group(1) if m else ''

def before_select(expr):
    pos=expr.find('.select')
    if pos<0: return ''
    m=re.match(r'^([A-Za-z_]\w*)',expr[:pos].strip())
    return m.group(1) if m else ''

def resolve_df_column(expr,column,local,whole,visited=None,depth=0):
    visited=set() if visited is None else set(visited)
    res=empty(); res['depth']=depth; expr=clean(expr)
    if not expr: return res
    key=f'DFCOL:{expr}:{column}'
    if key in visited:
        res['cycle']=True; res['trace'].append('CYCLE DFCOL: '+key); return res
    visited.add(key); res['trace'].append(f'DATAFRAME COLUMN: {column} @ {expr}')

    if re.fullmatch(r'[A-Za-z_]\w*',expr):
        a=assignment(expr,local) or assignment(expr,whole)
        if a:
            res['trace'].append(f'{expr} -> {a}')
            merge(res,resolve_df_column(a,column,local,whole,visited,depth+1))
            return res

    items=select_items(expr)
    for item in items:
        if output_name(item)!=column: continue
        res['trace'].append(f'SELECT {column} <- {item.strip()}')
        col=source_column(item)
        source=before_select(expr)
        if col and source:
            merge(res,resolve_df_column(source,col,local,whole,visited,depth+1))
        else:
            merge(res,resolve_expr(item,local,whole,visited,depth+1))
    if res['resolved']: return res

    m=re.match(r'^([A-Za-z_]\w*)',expr)
    if m:
        a=assignment(m.group(1),local) or assignment(m.group(1),whole)
        if a: merge(res,resolve_df_column(a,column,local,whole,visited,depth+1))
    return res

def resolve_row_index(expr,local,whole,visited=None,depth=0):
    visited=set() if visited is None else set(visited)
    res=empty(); res['depth']=depth
    m=re.fullmatch(r'([A-Za-z_]\w*)\s*\(\s*(\d+)\s*\)(?:\.toString)?',clean(expr))
    if not m: return res
    var=m.group(1); idx=int(m.group(2))
    collections=uniq(iterator_collections(var,local)+iterator_collections(var,whole))
    for collection in collections:
        stripped=strip_suffixes(collection)
        a=assignment(stripped,local) or assignment(stripped,whole)
        res['trace'].append(f'ROW INDEX: {var}({idx}) from {collection}')
        if not a: continue
        items=select_items(a)
        if 0<=idx<len(items):
            col=output_name(items[idx])
            res['trace'].append(f'ROW INDEX {idx} -> {col or items[idx].strip()}')
            if col: merge(res,resolve_df_column(a,col,local,whole,visited,depth+1))
    return res

def resolve_expr(expr,local,whole,visited=None,depth=0,all_code=None):
    visited=set() if visited is None else set(visited); res=empty(); res['depth']=depth; expr=clean(expr).rstrip(';')
    if not expr: return res
    key='EXPR:'+expr
    if key in visited: res['cycle']=True; res['trace'].append('CYCLE: '+expr); return res
    visited.add(key)
    ps=config_paths(expr)
    if ps: res['paths']+=ps; res['trace'].append(expr); res['resolved']=True; return res
    m=re.fullmatch(r'["\'](.*?)["\']',expr,re.S)
    if m: res['literals'].append(m.group(1)); res['trace'].append('LITERAL: '+m.group(1)); res['resolved']=True; return res
    if re.fullmatch(r'[A-Za-z_]\w*',expr):
        a=assignment(expr,local) or assignment(expr,whole)
        if a: res['trace'].append(f'{expr} -> {a}'); merge(res,resolve_expr(a,local,whole,visited,depth+1,all_code))
        if not res['resolved']:
            for c in uniq(iterator_collections(expr,local)+iterator_collections(expr,whole)):
                res['trace'].append(f'{expr} iterates {c}')
                merge(res,resolve_collection(c,local,whole,visited,depth+1,all_code))
        if not res['resolved'] and all_code:
            for fn in functions_with_parameter(expr,whole):
                merge(res,resolve_parameter_cross(fn,expr,whole,all_code))
        return res
    row_result=resolve_row_index(expr,local,whole,visited,depth+1)
    merge(res,row_result)
    if res['resolved']: return res
    m=re.match(r'^([A-Za-z_]\w*)[\.\(\[]',expr)
    if m:
        base=m.group(1); res['trace'].append(expr); a=assignment(base,local) or assignment(base,whole)
        if a: res['trace'].append(f'{base} -> {a}'); merge(res,resolve_expr(a,local,whole,visited,depth+1,all_code))
        if not res['resolved']:
            for c in uniq(iterator_collections(base,local)+iterator_collections(base,whole)):
                res['trace'].append(f'{base} iterates {c}')
                merge(res,resolve_collection(c,local,whole,visited,depth+1,all_code))
        if not res['resolved'] and all_code:
            for fn in functions_with_parameter(base,whole):
                merge(res,resolve_parameter_cross(fn,base,whole,all_code))
        if res['resolved'] and res['literals']: res['literals']=transform(expr,res['literals'])
        return res
    merge(res,resolve_collection(expr,local,whole,visited,depth+1)); return res


def resolve_parameter_cross(fn,var,definition_code,all_code):
    res=empty(); idx=parameter_index(fn,var,definition_code)
    res['trace'] += [f'FUNCTION: {fn or "UNKNOWN"}',f'PARAMETER: {var}']
    if idx is None:
        res['trace'].append('PARAMETER_POSITION_NOT_FOUND'); return res
    res['trace'].append(f'PARAMETER_POSITION: {idx}')
    for caller_nb,caller_code in all_code.items():
        for text,context in calls(fn,caller_code):
            args=split_args(text)
            if idx>=len(args): continue
            arg=args[idx].strip()
            res['trace'].append(f'CALL: {caller_nb} -> {arg}')
            merge(res,resolve_expr(arg,context,caller_code,depth=1,all_code=all_code))
    return res

def resolve_iterator(expr,local,whole,all_code=None):
    res=empty(); m=re.match(r'^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.(?:map|foreach|flatMap|filter)',expr)
    if not m: return res
    c=m.group(1); res['trace'].append('ITERATOR SOURCE: '+c); merge(res,resolve_collection(c,local,whole,depth=1)); return res

def resolve_parameter(fn,var,local,whole):
    res=empty(); idx=parameter_index(fn,var,local)
    if idx is None: idx=parameter_index(fn,var,whole)
    res['trace'] += [f'FUNCTION: {fn or "UNKNOWN"}',f'PARAMETER: {var}']
    if idx is None: res['trace'].append('PARAMETER_POSITION_NOT_FOUND'); return res
    res['trace'].append(f'PARAMETER_POSITION: {idx}')
    for text,context in calls(fn,whole):
        args=split_args(text)
        if idx>=len(args): continue
        arg=args[idx].strip(); res['trace'].append('CALL_ARGUMENT: '+arg); merge(res,resolve_expr(arg,context,whole,depth=1))
    return res

def main():
    print('='*70); print('ASSESSMENT WORKSPACE - PASO 13'); print('TRAZABILIDAD DE VARIABLES DINAMICAS - V3.2'); print('='*70); print()
    pro_config=load_json(PRO_CONFIG_FILE); uc_config=load_json(UC_CONFIG_FILE)
    nbs=load_csv(NOTEBOOKS_FILE); jobs=load_csv(JOB_INVENTORY_FILE); rows=load_csv(DYNAMIC_INVENTORY_FILE)
    mapping={clean(r.get('workspace_path')):project_path(r.get('local_file') or r.get('path')) for r in nbs if clean(r.get('workspace_path'))}
    used={clean(r.get('notebook')) for r in jobs if clean(r.get('notebook'))}
    all_blocks={}; all_code={}; missing=[]
    for nb in sorted(used,key=str.casefold):
        p=mapping.get(nb)
        if p is None or not p.exists(): missing.append((nb,str(p or 'SIN_MAPEO_LOCAL'))); continue
        bs=[remove_comments(x) for x in blocks(p)]; all_blocks[nb]=bs; all_code[nb]='\n'.join(bs)
    pending=[r for r in rows if clean(r.get('trace_required')).casefold()=='true']
    output=[]
    for row in pending:
        nb=clean(row.get('notebook')); var=clean(row.get('variable')); typ=clean(row.get('source_type')); expr=clean(row.get('source_expression'))
        data_source=clean(row.get('data_source')) or 'UNKNOWN'
        migration_scope=clean(row.get('migration_scope')) or 'REQUIRES_REVIEW'
        whole=all_code.get(nb,''); bs=all_blocks.get(nb,[])
        try: ci=int(clean(row.get('cell')))-1
        except: ci=-1
        local=bs[ci] if 0<=ci<len(bs) else whole
        if typ=='DIRECT_ASSIGNMENT': method='DERIVED_EXPRESSION'; result=resolve_expr(expr,local,whole,all_code=all_code)
        elif typ=='ITERATOR_VARIABLE': method='ITERATOR_COLLECTION'; result=resolve_iterator(expr,local,whole,all_code)
        elif typ=='FUNCTION_PARAMETER': method='FUNCTION_CALL'; result=resolve_parameter_cross(function_name(expr),var,whole,all_code)
        else: method='GENERIC_EXPRESSION'; result=resolve_expr(expr,local,whole)
        ps=uniq(result['paths']); lits=uniq(result['literals']); tr=uniq(result['trace'])
        pro_values=[]; uc_values=[]
        for config_path in ps:
            pv,uv=values_from_config_path(config_path,pro_config,uc_config)
            pro_values.extend(pv); uc_values.extend(uv)
        pro_values=uniq(pro_values); uc_values=uniq(uc_values)
        if result['cycle'] and not result['resolved']: status='CYCLE_DETECTED'
        elif result['resolved']:
            if ps:
                if not pro_values and not uc_values:
                    status='CONFIG_PATH_NOT_FOUND'
                elif len(ps)>1:
                    status='RESOLVED_MULTIPLE_CONFIG_PATHS'
                elif pro_values and uc_values:
                    status='RESOLVED_CONFIG_VALUES'
                elif pro_values:
                    status='RESOLVED_PRO_VALUE_ONLY'
                else:
                    status='RESOLVED_UC_VALUE_ONLY'
            else:
                status='RESOLVED_LITERAL' if lits else 'RESOLVED_VARIABLE_CHAIN'
        elif method=='FUNCTION_CALL': status='UNRESOLVED_FUNCTION_ARGUMENT'
        elif method=='ITERATOR_COLLECTION': status='UNRESOLVED_ITERATOR_SOURCE'
        else: status='PARTIAL_TRACE'
        output.append({'notebook':nb,'cell':clean(row.get('cell')),'variable':var,'source_type':typ,'source_category':clean(row.get('source_category')),'source_expression':expr,'data_source':data_source,'migration_scope':migration_scope,'trace_method':method,'trace_depth':result['depth'],'config_paths':' | '.join(ps),'pro_values':' | '.join(pro_values),'uc_values':' | '.join(uc_values),'resolved_literals':' | '.join(lits),'trace_status':status,'trace':' -> '.join(tr),'used_by_dynamic_table':clean(row.get('used_by_dynamic_table')),'dynamic_table_references':clean(row.get('dynamic_table_references')),'jobs':clean(row.get('jobs'))})
    output.sort(key=lambda r:(r['notebook'].casefold(),r['variable'].casefold(),int(r['cell']) if r['cell'].isdigit() else 0))
    OUTPUT_FILE.parent.mkdir(parents=True,exist_ok=True)
    with OUTPUT_FILE.open('w',newline='',encoding='utf-8-sig') as f: w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(output)
    methods=Counter(r['trace_method'] for r in output); statuses=Counter(r['trace_status'] for r in output)
    data_sources=Counter(r['data_source'] for r in output); scopes=Counter(r['migration_scope'] for r in output)
    active=[r for r in output if clean(r['used_by_dynamic_table']).casefold()=='true']
    resolved=[r for r in active if r['trace_status'].startswith('RESOLVED_')]
    active_hive=[r for r in active if r['migration_scope']=='HMS_TO_UC']
    active_hive_resolved=[r for r in active_hive if r['trace_status'].startswith('RESOLVED_')]
    active_jdbc=[r for r in active if r['migration_scope']=='OUT_OF_SCOPE_JDBC']
    print('--- Entradas ---'); print(f'Configuración PRO              : {PRO_CONFIG_FILE}'); print(f'Configuración UC               : {UC_CONFIG_FILE}'); print(f'Variables Paso 12              : {len(rows)}'); print(f'Variables que requieren trace  : {len(pending)}'); print(f'Notebooks de jobs cargados     : {len(all_code)}'); print(f'Archivos faltantes             : {len(missing)}'); print(); print('--- Resultado ---'); print(f'Trazas generadas               : {len(output)}'); print(f'Trazas activas en tablas       : {len(active)}'); print(f'Activas resueltas              : {len(resolved)}'); print(f'Activas pendientes             : {len(active)-len(resolved)}'); print(f' - HMS_TO_UC activas           : {len(active_hive)}'); print(f'   resueltas                   : {len(active_hive_resolved)}'); print(f'   pendientes                  : {len(active_hive)-len(active_hive_resolved)}'); print(f' - JDBC fuera de alcance       : {len(active_jdbc)}'); print(); print('Resumen por data_source:')
    for k in sorted(data_sources): print(f' - {k:<30}: {data_sources[k]}')
    print(); print('Resumen por migration_scope:')
    for k in sorted(scopes): print(f' - {k:<30}: {scopes[k]}')
    print(); print('Resumen por método:')
    for k in sorted(methods): print(f' - {k:<30}: {methods[k]}')
    print(); print('Resumen por estado:')
    for k in sorted(statuses): print(f' - {k:<36}: {statuses[k]}')
    pend=[r for r in active_hive if not r['trace_status'].startswith('RESOLVED_')]
    if pend:
        print(); print('Pendientes activos HMS_TO_UC:')
        for r in pend: print(f" - {Path(r['notebook']).name} | cell {r['cell']} | {r['variable']} | {r['trace_status']} | {r['source_expression']}")
    print(); print(f'Archivo generado: {OUTPUT_FILE.resolve()}'); print(); print('='*70); print('RESULTADO: TRAZABILIDAD DINAMICA GENERADA CORRECTAMENTE'); print('='*70)

if __name__=='__main__': main()
