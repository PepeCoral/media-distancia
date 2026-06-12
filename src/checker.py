import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

from scraper import RenfeScraper

STATIONS_PATH = Path(__file__).resolve().parent.parent / "assets" / "stations.json"
MEDIA_DISTANCIA_TYPES = {"MEDIA DISTANCIA", "MEDIA DISTANCIA - MD"}
DATE_FORMAT = "%d/%m/%Y"


def main():
    origin_name = os.environ.get("ORIGIN")
    dest_name = os.environ.get("DESTINATION")
    target_date_str = os.environ.get("TARGET_DATE")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    missing = [k for k, v in [
        ("ORIGIN", origin_name),
        ("DESTINATION", dest_name),
        ("TARGET_DATE", target_date_str),
        ("TELEGRAM_BOT_TOKEN", bot_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ] if not v]

    if missing:
        print(f"Faltan variables de entorno: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    try:
        target_date = datetime.strptime(target_date_str, DATE_FORMAT)
    except ValueError:
        print(f"Formato de fecha inválido: {target_date_str}. Usa DD/MM/YYYY", file=sys.stderr)
        sys.exit(1)

    stations = _load_stations(STATIONS_PATH)
    origin_code = _lookup_station(stations, origin_name)
    dest_code = _lookup_station(stations, dest_name)

    scraper = RenfeScraper(origin_name, origin_code, dest_name, dest_code, target_date)

    try:
        raw = scraper.get_trains()
    except Exception as e:
        msg = f"Error al consultar Renfe: {e}"
        print(msg, file=sys.stderr)
        _send_telegram(bot_token, chat_id, f"⚠️ {msg}")
        sys.exit(1)

    all_trains = scraper.parse_trains(raw, origin_name, dest_name)
    md_trains = [t for t in all_trains if _is_media_distancia(t["train_type"])]
    available = [t for t in md_trains if t["available"]]

    date_str = target_date.strftime("%d/%m/%Y")
    lines = [f"📅 {date_str}  |  🚉 {origin_name} → {dest_name}"]

    if not all_trains:
        lines.insert(0, "❌ No se encontraron trenes en la consulta")
        body = "\n".join(lines)
    elif not md_trains:
        lines.insert(0, "ℹ️ Hay trenes pero ninguno es Media Distancia")
        body = "\n".join(lines)
    elif not available:
        lines.insert(0, "❌ No hay trenes de Media Distancia disponibles")
        for t in md_trains[:5]:
            lines.append(f"  {t['departure']}–{t['arrival']}  ({t['train_type']})  AGOTADO")
        body = "\n".join(lines)
    else:
        lines.insert(0, "✅ ¡Trenes de Media Distancia disponibles!")
        for t in available:
            price = f"{t['price']}€" if t['price'] else "?"
            lines.append(
                f"  🕐 {t['departure']}–{t['arrival']}  "
                f"{t['duration_min']}min  {price}"
            )
        body = "\n".join(lines)

    _send_telegram(bot_token, chat_id, body)


def _load_stations(path: Path) -> dict:
    if not path.exists():
        print(f"Fichero de estaciones no encontrado: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _lookup_station(stations: dict, name: str) -> str:
    upper = name.upper().strip()
    if upper in stations:
        return stations[upper]["clave"]

    for key, val in stations.items():
        if val["desgEstacion"].upper() == upper:
            return val["clave"]

    print(f"Estación no encontrada: {name}", file=sys.stderr)
    print("Estaciones disponibles (primeras 20):", file=sys.stderr)
    for k in sorted(stations.keys())[:20]:
        print(f"  {k}", file=sys.stderr)
    sys.exit(1)


def _is_media_distancia(train_type: str) -> bool:
    return train_type.upper() in MEDIA_DISTANCIA_TYPES or "MEDIA" in train_type.upper()


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Error al enviar Telegram: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
