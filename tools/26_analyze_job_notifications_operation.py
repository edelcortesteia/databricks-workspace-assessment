#!/usr/bin/env python3
from pathlib import Path
from collections import Counter
import csv
import json
import re
import yaml

SNAPSHOT_JOBS_DIR = Path("snapshot/jobs")
UC_JOBS_DIR = Path("input/config/jobs")
MATCHING_FILE = Path("output/job_name_matching.csv")
OUTPUT_FILE = Path("output/job_notifications_operation_analysis.csv")

DEFAULT_FALSE_NOTIFICATION_KEYS = {
    "no_alert_for_skipped_runs",
    "no_alert_for_canceled_runs",
}

def clean(value):
    return "" if value is None else str(value).strip()

def normalize(value):
    return clean(value).replace("\\", "/").strip().lower()

def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def load_json(path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)

def normalize_yaml_text(text):
    text = text.replace("\t", "  ")
    stripped = text.strip()
    if stripped.startswith('"resources:') and stripped.endswith('"'):
        text = stripped[1:-1]
    text = re.sub(
        r':\s*""([^"\r\n]*)""\s*$',
        lambda m: ': "' + m.group(1) + '"',
        text,
        flags=re.MULTILINE,
    )
    return text

def load_yaml(path):
    try:
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
            parse_mode = "DIRECT"
        except Exception:
            data = yaml.safe_load(normalize_yaml_text(text))
            parse_mode = "NORMALIZED"
        return data, parse_mode, ""
    except Exception as e:
        return None, "ERROR", f"{type(e).__name__}: {e}"

def canonical_json(value):
    if value in (None, "", {}, []):
        return ""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

def unique(values):
    result = []
    seen = set()
    for value in values:
        value = clean(value)
        if not value:
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result

def load_exact_matches():
    if not MATCHING_FILE.exists():
        raise SystemExit(
            "Falta output/job_name_matching.csv. Ejecuta primero el Paso 22."
        )
    rows = read_csv(MATCHING_FILE)
    result = {}
    for row in rows:
        method = clean(
            row.get("match_method")
            or row.get("matching_method")
            or row.get("method")
        )
        if method != "EXACT_NAME":
            continue
        pro_job = clean(
            row.get("workspace_job")
            or row.get("pro_job")
            or row.get("job")
            or row.get("workspace_name")
        )
        uc_job = clean(
            row.get("uc_job")
            or row.get("matched_uc_job")
            or row.get("uc_name")
        )
        if pro_job and uc_job:
            result[pro_job] = uc_job
    if not result:
        raise SystemExit("No se encontraron matches EXACT_NAME en el Paso 22.")
    return result

def extract_uc_job(data):
    if not isinstance(data, dict):
        return "", {}
    resources = data.get("resources", {})
    jobs = resources.get("jobs", {})
    if not isinstance(jobs, dict):
        return "", {}
    for _, job_data in jobs.items():
        if isinstance(job_data, dict):
            return clean(job_data.get("name")), job_data
    return "", {}

def normalize_string_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return sorted(
            unique(clean(item) for item in value),
            key=str.casefold,
        )
    scalar = clean(value)
    return [scalar] if scalar else []

def normalize_email_notifications(value):
    if not isinstance(value, dict):
        return {}

    result = {}

    for key, recipients in value.items():
        key_clean = clean(key)
        key_norm = key_clean.casefold()

        if (
            key_norm in DEFAULT_FALSE_NOTIFICATION_KEYS
            and recipients is False
        ):
            continue

        if isinstance(recipients, bool):
            if recipients:
                result[key_clean] = ["true"]
            continue

        normalized_recipients = normalize_string_list(recipients)
        if normalized_recipients:
            result[key_clean] = normalized_recipients

    return dict(sorted(result.items(), key=lambda item: item[0].casefold()))

def normalize_webhook_notifications(value):
    if not isinstance(value, dict):
        return {}

    result = {}

    for key, items in value.items():
        key_clean = clean(key)
        key_norm = key_clean.casefold()

        if (
            key_norm in DEFAULT_FALSE_NOTIFICATION_KEYS
            and items is False
        ):
            continue

        if isinstance(items, bool):
            if items:
                result[key_clean] = ["true"]
            continue

        if not isinstance(items, list):
            items = [items]

        canonical_items = []

        for item in items:
            if isinstance(item, dict):
                canonical_items.append(json.loads(canonical_json(item)))
            elif item not in (None, ""):
                canonical_items.append(item)

        if canonical_items:
            canonical_items = sorted(
                canonical_items,
                key=lambda item: canonical_json(item),
            )
            result[key_clean] = canonical_items

    return dict(sorted(result.items(), key=lambda item: item[0].casefold()))

def normalize_notification_settings(value):
    if not isinstance(value, dict):
        return {}

    cleaned = {}

    for key, child in value.items():
        key_clean = clean(key)
        key_norm = key_clean.casefold()

        if (
            key_norm in DEFAULT_FALSE_NOTIFICATION_KEYS
            and child is False
        ):
            continue

        cleaned[key_clean] = child

    if not cleaned:
        return {}

    return json.loads(canonical_json(cleaned))

def normalize_health(value):
    if not isinstance(value, dict):
        return {}

    result = dict(value)
    rules = result.get("rules")

    if isinstance(rules, list):
        normalized_rules = []

        for rule in rules:
            if isinstance(rule, dict):
                normalized_rules.append(json.loads(canonical_json(rule)))
            else:
                normalized_rules.append(rule)

        result["rules"] = sorted(
            normalized_rules,
            key=lambda item: canonical_json(item),
        )

    return json.loads(canonical_json(result))

def format_email_notifications(value):
    if not value:
        return ""
    return "; ".join(
        f"{event}=" + " | ".join(recipients)
        for event, recipients in value.items()
    )

def format_webhook_notifications(value):
    if not value:
        return ""

    parts = []

    for event, hooks in value.items():
        hook_values = []

        for hook in hooks:
            if isinstance(hook, dict):
                hook_values.append(canonical_json(hook))
            else:
                hook_values.append(clean(hook))

        parts.append(f"{event}=" + " | ".join(hook_values))

    return "; ".join(parts)

def classify_notifications(
    pro_email,
    uc_email,
    pro_webhook,
    uc_webhook,
):
    pro_has = bool(pro_email or pro_webhook)
    uc_has = bool(uc_email or uc_webhook)

    if pro_email == uc_email and pro_webhook == uc_webhook:
        if not pro_has and not uc_has:
            return (
                "NO_NOTIFICATIONS",
                "NO",
                "No se detectaron notificaciones operativas reales en PRO ni UC.",
            )
        return (
            "NOTIFICATIONS_ALIGNED_UC",
            "NO",
            "Las notificaciones y destinatarios se mantienen equivalentes entre PRO y UC.",
        )

    if not pro_has and uc_has:
        return (
            "EXPECTED_UC_OPERATIONAL_CHANGE",
            "NO",
            "Unity Catalog incorpora configuración de notificaciones operativas no declarada en PRO. Se considera un cambio esperado de homologación.",
        )

    if pro_has and uc_has:
        return (
            "EXPECTED_UC_OPERATIONAL_CHANGE",
            "NO",
            "PRO y UC contienen notificaciones, pero la configuración o los destinatarios presentan diferencias. El cambio se considera parte de la homologación operativa definida para UC.",
        )

    if pro_has and not uc_has:
        return (
            "REQUIRES_REVIEW",
            "REVIEW",
            "PRO contiene notificaciones operativas reales y no se detectó configuración equivalente en UC. Revisar antes del cierre.",
        )

    return (
        "NO_NOTIFICATIONS",
        "NO",
        "No se detectaron notificaciones operativas reales.",
    )

def classify_health(pro_health, uc_health):
    pro_has = bool(pro_health)
    uc_has = bool(uc_health)

    if not pro_has and not uc_has:
        return (
            "NO_HEALTH_RULES",
            "NO",
            "No se detectaron reglas health.",
        )

    if pro_has and uc_has and pro_health == uc_health:
        return (
            "HEALTH_ALIGNED_UC",
            "NO",
            "La configuración health se mantiene equivalente.",
        )

    if uc_has:
        return (
            "EXPECTED_UC_HEALTH_CHANGE",
            "NO",
            "UC incorpora o modifica reglas health respecto de PRO como parte de la configuración operativa.",
        )

    return (
        "REQUIRES_REVIEW",
        "REVIEW",
        "PRO contiene reglas health y no se detectó configuración equivalente en UC.",
    )

def classify_notification_settings(pro_settings, uc_settings):
    if pro_settings == uc_settings:
        return (
            "ALIGNED_UC",
            "NO",
            "notification_settings se mantiene equivalente o sólo contenía defaults false materializados por Workspace.",
        )

    if not pro_settings and uc_settings:
        return (
            "EXPECTED_UC_OPERATIONAL_CHANGE",
            "NO",
            "UC incorpora notification_settings como parte de la homologación operativa.",
        )

    if pro_settings and uc_settings:
        return (
            "EXPECTED_UC_OPERATIONAL_CHANGE",
            "NO",
            "notification_settings presenta diferencias esperadas entre PRO y UC.",
        )

    return (
        "REQUIRES_REVIEW",
        "REVIEW",
        "PRO contiene notification_settings no-default y no se detectó configuración equivalente en UC.",
    )

def load_pro_jobs():
    result = {}

    for path in sorted(SNAPSHOT_JOBS_DIR.glob("*.json")):
        try:
            data = load_json(path)
        except Exception as e:
            print(f"ADVERTENCIA leyendo {path}: {e}")
            continue

        if not isinstance(data, dict):
            continue

        job_name = clean(data.get("name"))
        if not job_name:
            continue

        result[job_name] = {
            "data": data,
            "source": str(path),
        }

    return result

def load_uc_jobs():
    result = {}
    yaml_files = []

    for pattern in ["UC_*.yml", "UC_*.yaml"]:
        yaml_files.extend(UC_JOBS_DIR.glob(pattern))

    for path in sorted(yaml_files):
        data, parse_mode, error = load_yaml(path)

        if data is None:
            print(f"ADVERTENCIA leyendo {path}: {error}")
            continue

        job_name, job_data = extract_uc_job(data)
        if not job_name:
            continue

        result[job_name] = {
            "data": job_data,
            "source": str(path),
            "parse_mode": parse_mode,
        }

    return result

def main():
    exact_matches = load_exact_matches()
    pro_jobs = load_pro_jobs()
    uc_jobs = load_uc_jobs()

    missing_pro = [
        job for job in exact_matches
        if job not in pro_jobs
    ]

    missing_uc = [
        uc_job for uc_job in exact_matches.values()
        if uc_job not in uc_jobs
    ]

    if missing_pro:
        raise SystemExit(
            "Jobs EXACT_NAME no encontrados en snapshot PRO:\n - "
            + "\n - ".join(missing_pro)
        )

    if missing_uc:
        raise SystemExit(
            "Jobs EXACT_NAME no encontrados en YAML UC:\n - "
            + "\n - ".join(missing_uc)
        )

    output_rows = []

    for pro_job in sorted(exact_matches, key=str.casefold):
        uc_job = exact_matches[pro_job]

        pro_data = pro_jobs[pro_job]["data"]
        uc_data = uc_jobs[uc_job]["data"]

        pro_email = normalize_email_notifications(
            pro_data.get("email_notifications")
        )
        uc_email = normalize_email_notifications(
            uc_data.get("email_notifications")
        )

        pro_webhook = normalize_webhook_notifications(
            pro_data.get("webhook_notifications")
        )
        uc_webhook = normalize_webhook_notifications(
            uc_data.get("webhook_notifications")
        )

        pro_notification_settings = normalize_notification_settings(
            pro_data.get("notification_settings")
        )
        uc_notification_settings = normalize_notification_settings(
            uc_data.get("notification_settings")
        )

        pro_health = normalize_health(
            pro_data.get("health")
        )
        uc_health = normalize_health(
            uc_data.get("health")
        )

        (
            notification_status,
            notification_action,
            notification_notes,
        ) = classify_notifications(
            pro_email,
            uc_email,
            pro_webhook,
            uc_webhook,
        )

        (
            health_status,
            health_action,
            health_notes,
        ) = classify_health(
            pro_health,
            uc_health,
        )

        (
            settings_status,
            settings_action,
            settings_notes,
        ) = classify_notification_settings(
            pro_notification_settings,
            uc_notification_settings,
        )

        requires_action = (
            "REVIEW"
            if (
                notification_action == "REVIEW"
                or health_action == "REVIEW"
                or settings_action == "REVIEW"
            )
            else "NO"
        )

        if requires_action == "REVIEW":
            migration_status = "REQUIRES_REVIEW"
        elif (
            notification_status == "EXPECTED_UC_OPERATIONAL_CHANGE"
            or health_status == "EXPECTED_UC_HEALTH_CHANGE"
            or settings_status == "EXPECTED_UC_OPERATIONAL_CHANGE"
        ):
            migration_status = "EXPECTED_UC_OPERATIONAL_CHANGE"
        else:
            migration_status = "ALIGNED_UC"

        notes = " ".join(
            [
                notification_notes,
                health_notes,
                settings_notes,
            ]
        ).strip()

        output_rows.append({
            "job": pro_job,
            "uc_job": uc_job,
            "pro_email_notifications": format_email_notifications(pro_email),
            "uc_email_notifications": format_email_notifications(uc_email),
            "pro_webhook_notifications": format_webhook_notifications(pro_webhook),
            "uc_webhook_notifications": format_webhook_notifications(uc_webhook),
            "pro_notification_settings": canonical_json(pro_notification_settings),
            "uc_notification_settings": canonical_json(uc_notification_settings),
            "pro_health": canonical_json(pro_health),
            "uc_health": canonical_json(uc_health),
            "notification_status": notification_status,
            "health_status": health_status,
            "settings_status": settings_status,
            "migration_status": migration_status,
            "requires_action": requires_action,
            "notes": notes,
            "pro_source": pro_jobs[pro_job]["source"],
            "uc_yaml": uc_jobs[uc_job]["source"],
            "pro_parse_mode": "WORKSPACE_JSON",
            "uc_parse_mode": uc_jobs[uc_job]["parse_mode"],
        })

    output_rows.sort(
        key=lambda row: normalize(row["job"])
    )

    fieldnames = [
        "job",
        "uc_job",
        "pro_email_notifications",
        "uc_email_notifications",
        "pro_webhook_notifications",
        "uc_webhook_notifications",
        "pro_notification_settings",
        "uc_notification_settings",
        "pro_health",
        "uc_health",
        "notification_status",
        "health_status",
        "settings_status",
        "migration_status",
        "requires_action",
        "notes",
        "pro_source",
        "uc_yaml",
        "pro_parse_mode",
        "uc_parse_mode",
    ]

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    notification_counter = Counter(
        row["notification_status"]
        for row in output_rows
    )
    health_counter = Counter(
        row["health_status"]
        for row in output_rows
    )
    settings_counter = Counter(
        row["settings_status"]
        for row in output_rows
    )
    migration_counter = Counter(
        row["migration_status"]
        for row in output_rows
    )
    action_counter = Counter(
        row["requires_action"]
        for row in output_rows
    )

    jobs_with_review = [
        row["job"]
        for row in output_rows
        if row["requires_action"] == "REVIEW"
    ]

    print("=" * 72)
    print("ASSESSMENT WORKSPACE - PASO 26")
    print("ALERTAS Y OPERACION PRO -> UNITY CATALOG")
    print("=" * 72)
    print()
    print(f"Jobs Workspace en snapshot       : {len(pro_jobs)}")
    print(f"Jobs UC disponibles              : {len(uc_jobs)}")
    print(f"Jobs en alcance (EXACT_NAME)     : {len(output_rows)}")
    print(f"Jobs fuera de alcance / sin UC   : {len(pro_jobs) - len(output_rows)}")
    print()

    print("Resumen de notificaciones:")
    for status in sorted(notification_counter):
        print(f" - {status:<34}: {notification_counter[status]}")

    print()
    print("Resumen Health:")
    for status in sorted(health_counter):
        print(f" - {status:<34}: {health_counter[status]}")

    print()
    print("Resumen notification_settings:")
    for status in sorted(settings_counter):
        print(f" - {status:<34}: {settings_counter[status]}")

    print()
    print("Resumen migración:")
    for status in sorted(migration_counter):
        print(f" - {status:<34}: {migration_counter[status]}")

    print()
    print("Resumen de acciones:")
    for action in sorted(action_counter):
        print(f" - {action:<34}: {action_counter[action]}")

    print()
    print(f"Jobs con revisión pendiente      : {len(jobs_with_review)}")

    if jobs_with_review:
        print()
        print("Jobs con pendientes:")
        for job in sorted(jobs_with_review, key=str.casefold):
            print(f" - {job}")

    print()
    print(
        "Nota: este paso valida configuración declarada. "
        "No prueba el envío real de correos/webhooks ni "
        "el disparo efectivo de reglas Health."
    )
    print()
    print(f"Archivo generado: {OUTPUT_FILE}")
    print()
    print("=" * 72)

if __name__ == "__main__":
    main()
