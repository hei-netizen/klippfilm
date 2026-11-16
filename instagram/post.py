#!/usr/bin/env python3
"""Daglig Instagram-posting for @klippfilm.no (kjøres av GitHub Actions, se .github/workflows/instagram.yml).

Kjøres hvert 10. minutt om morgenen (GitHub dropper av og til planlagte kjøringer). Før kl. 09:00 norsk
tid gjør den ingenting; etter 09:00 legger den ut første upubliserte video i instagram/ko.json med dato i dag
eller tidligere, maks én per dag, og sjekker Instagram først så den aldri dobbeltposter. Oppdaterer ko.json (workflowen committer), og sender e-post til Jona
via Brevo: lenke til posten, hvor mange som er igjen, og varsel når køen nærmer seg tom eller noe feiler.

Miljø: IG_TOKEN, IG_USER_ID, BREVO_API_KEY, VARSEL_TIL, DRY_RUN ("true" = lag container, ikke publiser),
       VENT ("false" = ikke vent på 09:00, brukes ved manuell kjøring).
"""
import datetime as dt, json, os, sys, time
from zoneinfo import ZoneInfo
import requests

API = "https://graph.instagram.com/v23.0"
OSLO = ZoneInfo("Europe/Oslo")
KO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ko.json")
TOKEN, UID = os.environ["IG_TOKEN"], os.environ["IG_USER_ID"]
DRY = os.environ.get("DRY_RUN", "false") == "true"
VENT = os.environ.get("VENT", "true") == "true"
LAV_KO = 5


def varsle(emne, html):
    nokkel, til = os.environ.get("BREVO_API_KEY"), os.environ.get("VARSEL_TIL")
    if not nokkel or not til:
        return
    try:
        requests.post("https://api.brevo.com/v3/smtp/email", timeout=30, headers={"api-key": nokkel},
                      json={"sender": {"name": "Klippfilm", "email": "hei@klippfilm.no"}, "to": [{"email": til}],
                            "subject": emne, "htmlContent": f'<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5">{html}</div>'})
    except Exception as e:
        print("Fikk ikke sendt varsel:", e)


def kall(metode, url, **kw):
    r = requests.request(metode, url, timeout=120, **kw)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Instagram svarte {r.status_code}: {data.get('error', data)}")
    return data


def main():
    ko = json.load(open(KO, encoding="utf-8"))
    na = dt.datetime.now(OSLO)
    idag = na.date().isoformat()
    klare = [p for p in ko if not p.get("publisert") and p["dato"] <= idag]
    if not klare:
        igjen = [p for p in ko if not p.get("publisert")]
        print(f"Ingenting å poste i dag ({idag}). {len(igjen)} i køen.")
        if not igjen and na.weekday() == 0 and na.hour == 9 and na.minute < 10:  # én gang, mandag morgen
            varsle("Instagram-køen er tom", "Det ligger ingen videoer i køen for @klippfilm.no. Åpne Claude på Macen og si «lag neste bunke».")
        return
    post = klare[0]

    if VENT and not DRY and na.hour < 9:
        print(f"Klokka er {na:%H:%M} i Oslo. Poster først etter 09:00.")
        return
    if not DRY and any((p.get("publisert") or "").startswith(idag) for p in ko):
        print("Allerede postet i dag.")
        return
    # sperre mot dobbeltpost: har denne captionen allerede blitt lagt ut?
    siste = kall("GET", f"{API}/{UID}/media", params={"fields": "caption,permalink,timestamp", "limit": 10, "access_token": TOKEN})
    for m in siste.get("data", []):
        if (m.get("caption") or "").strip() == post["caption"].strip():
            post["publisert"], post["lenke"] = m["timestamp"][:16], m.get("permalink")
            json.dump(ko, open(KO, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print(f"«{post['navn']}» ligger allerede ute ({m.get('permalink')}). Markert som publisert.")
            return

    c = kall("POST", f"{API}/{UID}/media", data={"media_type": "REELS", "video_url": post["video_url"],
                                                  "caption": post["caption"], "share_to_feed": "true", "access_token": TOKEN})
    cid = c["id"]
    for _ in range(90):
        s = kall("GET", f"{API}/{cid}", params={"fields": "status_code,status", "access_token": TOKEN})
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram avviste videoen «{post['navn']}»: {s}")
        time.sleep(10)
    else:
        raise RuntimeError(f"Instagram brukte for lang tid på å behandle «{post['navn']}» (container {cid}).")

    if DRY:
        print(f"TESTKJØRING OK: «{post['navn']}» er godkjent av Instagram (container {cid}), men ikke publisert.")
        return

    p = kall("POST", f"{API}/{UID}/media_publish", data={"creation_id": cid, "access_token": TOKEN})
    lenke = kall("GET", f"{API}/{p['id']}", params={"fields": "permalink", "access_token": TOKEN}).get("permalink", "")
    post["publisert"] = dt.datetime.now(OSLO).strftime("%Y-%m-%dT%H:%M")
    post["lenke"] = lenke
    json.dump(ko, open(KO, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    igjen = sum(1 for x in ko if not x.get("publisert"))
    print(f"Publisert «{post['navn']}»: {lenke}. {igjen} igjen i køen.")

    tillegg = ""
    if igjen <= LAV_KO:
        tillegg = (f"<br><br><b>Køen er nesten tom: {igjen} video{'er' if igjen != 1 else ''} igjen.</b> "
                   "Åpne Claude på Macen og si «lag neste bunke».")
    varsle(f"Lagt ut på Instagram: {post['navn']}",
           f"Dagens video er lagt ut på @klippfilm.no:<br><a href=\"{lenke}\">{lenke}</a><br><br>{igjen} igjen i køen.{tillegg}")
    # Tokenet fornyes på Macen når en ny bunke lages (ig_post.py forny + gh secret set), ikke her:
    # Instagram gir en ny tokenstreng ved fornyelse, og den kan ikke lagres tilbake som secret herfra.

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FEIL:", e)
        varsle("Instagram-posting feilet", f"Dagens Instagram-post for @klippfilm.no ble ikke lagt ut.<br><br>Feil: {e}")
        sys.exit(1)
