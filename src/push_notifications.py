"""Push notification service voor de Cahn Family Task Assistant."""
import os
import json
from pywebpush import webpush, WebPushException

from .database import (
    get_push_subscriptions_for_member,
    get_all_push_subscriptions,
    delete_push_subscription_by_endpoint
)

# VAPID keys worden opgehaald uit environment variables
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")


def get_vapid_public_key() -> str:
    """Geef de public key terug voor gebruik in de frontend."""
    return VAPID_PUBLIC_KEY


def send_push_notification(member_name: str, title: str, body: str, data: dict = None) -> dict:
    """Stuur een push notificatie naar alle devices van een gezinslid.

    Args:
        member_name: Naam van het gezinslid
        title: Titel van de notificatie
        body: Body tekst van de notificatie
        data: Extra data voor de notificatie (optioneel)

    Returns:
        Dict met success count en failed endpoints
    """
    if not VAPID_PRIVATE_KEY:
        return {"error": "VAPID keys niet geconfigureerd", "success": 0, "failed": 0}

    subscriptions = get_push_subscriptions_for_member(member_name)
    if not subscriptions:
        return {"error": f"Geen subscriptions voor {member_name}", "success": 0, "failed": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {}
    })

    success_count = 0
    failed_endpoints = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth
                    }
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL}
            )
            success_count += 1
        except WebPushException as e:
            # 410 Gone = subscription expired/invalid, verwijder
            if e.response and e.response.status_code == 410:
                delete_push_subscription_by_endpoint(sub.endpoint)
            failed_endpoints.append({
                "endpoint": sub.endpoint[:50] + "...",
                "error": str(e)
            })
        except Exception as e:
            failed_endpoints.append({
                "endpoint": sub.endpoint[:50] + "...",
                "error": str(e)
            })

    return {
        "success": success_count,
        "failed": len(failed_endpoints),
        "failed_details": failed_endpoints if failed_endpoints else None
    }


def send_push_to_all(title: str, body: str, data: dict = None) -> dict:
    """Stuur een push notificatie naar alle geregistreerde devices.

    Args:
        title: Titel van de notificatie
        body: Body tekst van de notificatie
        data: Extra data voor de notificatie (optioneel)

    Returns:
        Dict met results per member
    """
    if not VAPID_PRIVATE_KEY:
        return {"error": "VAPID keys niet geconfigureerd"}

    all_subs = get_all_push_subscriptions()
    if not all_subs:
        return {"error": "Geen subscriptions gevonden", "total": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {}
    })

    results = {"total": len(all_subs), "success": 0, "failed": 0}

    for sub in all_subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh,
                        "auth": sub.auth
                    }
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL}
            )
            results["success"] += 1
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                delete_push_subscription_by_endpoint(sub.endpoint)
            results["failed"] += 1
        except Exception:
            results["failed"] += 1

    return results


def send_morning_reminder(member_name: str, tasks: list[str]) -> dict:
    """Stuur ochtend herinnering met taken voor vandaag.

    Args:
        member_name: Naam van het gezinslid
        tasks: Lijst van taken voor vandaag

    Returns:
        Result dict
    """
    if not tasks:
        return {"skipped": True, "reason": "Geen taken vandaag"}

    task_list = ", ".join(tasks)
    title = f"Goedemorgen {member_name}!"
    body = f"Vandaag: {task_list}"

    return send_push_notification(member_name, title, body, {"type": "morning_reminder"})


def send_evening_reminder(member_name: str, open_tasks: list[str]) -> dict:
    """Stuur avond herinnering voor openstaande taken.

    Args:
        member_name: Naam van het gezinslid
        open_tasks: Lijst van nog niet voltooide taken

    Returns:
        Result dict
    """
    if not open_tasks:
        return {"skipped": True, "reason": "Alle taken gedaan!"}

    task_list = ", ".join(open_tasks)
    title = f"Nog te doen, {member_name}!"
    body = f"Nog open: {task_list}"

    return send_push_notification(member_name, title, body, {"type": "evening_reminder"})


def send_summary_to_endpoint(endpoint: str, p256dh: str, auth: str, title: str, body: str, data: dict = None) -> dict:
    """Stuur een notificatie naar een specifiek endpoint (device).

    Args:
        endpoint: Push endpoint URL
        p256dh: Public key
        auth: Auth secret
        title: Titel van de notificatie
        body: Body tekst
        data: Extra data (optioneel)

    Returns:
        Result dict
    """
    if not VAPID_PRIVATE_KEY:
        return {"error": "VAPID keys niet geconfigureerd", "success": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {}
    })

    try:
        webpush(
            subscription_info={
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth}
            },
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIMS_EMAIL}
        )
        return {"success": 1, "failed": 0}
    except WebPushException as e:
        if e.response and e.response.status_code == 410:
            delete_push_subscription_by_endpoint(endpoint)
        return {"success": 0, "failed": 1, "error": str(e)}
    except Exception as e:
        return {"success": 0, "failed": 1, "error": str(e)}


def send_morning_summary(tasks_by_member: dict[str, list[str]], endpoint: str, p256dh: str, auth: str) -> dict:
    """Stuur ochtend samenvatting met alle taken voor iedereen.

    Args:
        tasks_by_member: Dict van {naam: [taken]}
        endpoint: Push endpoint
        p256dh: Public key
        auth: Auth secret

    Returns:
        Result dict
    """
    # Bouw de samenvatting
    lines = []
    for member, tasks in tasks_by_member.items():
        if tasks:
            lines.append(f"{member}: {', '.join(tasks)}")

    if not lines:
        return {"skipped": True, "reason": "Niemand heeft taken vandaag"}

    title = "Goedemorgen! Taken vandaag:"
    body = "\n".join(lines)

    return send_summary_to_endpoint(endpoint, p256dh, auth, title, body, {"type": "morning_summary"})


def send_evening_summary(open_tasks_by_member: dict[str, list[str]], endpoint: str, p256dh: str, auth: str) -> dict:
    """Stuur avond samenvatting met openstaande taken voor iedereen.

    Args:
        open_tasks_by_member: Dict van {naam: [openstaande taken]}
        endpoint: Push endpoint
        p256dh: Public key
        auth: Auth secret

    Returns:
        Result dict
    """
    # Bouw de samenvatting
    lines = []
    for member, tasks in open_tasks_by_member.items():
        if tasks:
            lines.append(f"{member}: {', '.join(tasks)}")

    if not lines:
        title = "Goed gedaan!"
        body = "Alle taken zijn af vandaag!"
    else:
        title = "Nog te doen vandaag:"
        body = "\n".join(lines)

    return send_summary_to_endpoint(endpoint, p256dh, auth, title, body, {"type": "evening_summary"})
