import random
import re
import string
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

import json5
import requests

SEARCH_URL = "https://venta.renfe.com/vol/buscarTren.do?Idioma=es&Pais=ES"

DWR_ENDPOINT = "https://venta.renfe.com/vol/dwr/call/plaincall/"
SYSTEM_ID_URL = f"{DWR_ENDPOINT}__System.generateId.dwr"
UPDATE_SESSION_URL = f"{DWR_ENDPOINT}buyEnlacesManager.actualizaObjetosSesion.dwr"
TRAIN_LIST_URL = f"{DWR_ENDPOINT}trainEnlacesManager.getTrainsList.dwr"


class RenfeScraper:
    def __init__(
        self,
        origin_name: str,
        origin_code: str,
        dest_name: str,
        dest_code: str,
        departure_date: datetime,
    ):
        self.origin_name = origin_name
        self.origin_code = origin_code
        self.dest_name = dest_name
        self.dest_code = dest_code
        self.departure_date = departure_date

        self.session = requests.Session()
        self.search_id = _create_search_id()
        self.batch_id = _get_idx()

        self.dwr_token: Optional[str] = None
        self.script_session_id: Optional[str] = None

    def get_trains(self) -> Dict[str, Any]:
        self._do_search()
        self._do_get_dwr_token()
        self._do_update_session_objects()
        return self._do_get_train_list()

    def _do_search(self) -> None:
        data = _create_search_payload(
            self.origin_name, self.origin_code,
            self.dest_name, self.dest_code,
            self.departure_date,
        )
        cookie = _create_cookiedict(self.origin_name, self.origin_code, self.dest_name, self.dest_code)
        self.session.cookies.set(**cookie)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept-Encoding": "gzip, deflate",
            "Accept": "*/*",
            "Connection": "keep-alive",
        })

        r = self.session.post(SEARCH_URL, data=data, allow_redirects=True)
        r.raise_for_status()

    def _do_get_dwr_token(self) -> None:
        payload = _create_generate_id_payload(self.search_id, next(self.batch_id))
        self.session.post(SYSTEM_ID_URL, data=payload)

        payload = _create_generate_id_payload(self.search_id, next(self.batch_id))
        r = self.session.post(SYSTEM_ID_URL, data=payload)
        r.raise_for_status()

        self.dwr_token = _extract_dwr_token(r.text)
        self.session.cookies.set("DWRSESSIONID", self.dwr_token, path="/vol", domain="venta.renfe.com")
        self.script_session_id = _create_session_script_id(self.dwr_token)

    def _do_update_session_objects(self) -> None:
        payload = _create_update_session_objects_payload(
            self.search_id, self.script_session_id, next(self.batch_id),
        )
        r = self.session.post(UPDATE_SESSION_URL, data=payload)
        r.raise_for_status()

    def _do_get_train_list(self) -> Dict[str, Any]:
        payload = _create_get_train_list_payload(
            self.search_id, self.script_session_id,
            self.departure_date, next(self.batch_id),
        )
        r = self.session.post(TRAIN_LIST_URL, data=payload)
        r.raise_for_status()
        return _extract_train_list(r.text)

    @staticmethod
    def is_train_available(train: Dict[str, Any]) -> bool:
        return (
            not train["completo"]
            and train["razonNoDisponible"] in ("", "8")
            and train.get("tarifaMinima") is not None
            and not train["soloPlazaH"]
        )

    @staticmethod
    def parse_trains(raw: Dict[str, Any], origin: str, destination: str) -> List[Dict[str, Any]]:
        trains = []
        for way in raw.get("listadoTrenes", []):
            for train in way.get("listviajeViewEnlaceBean", []):
                trains.append({
                    "origin": origin,
                    "destination": destination,
                    "departure": train.get("horaSalida", ""),
                    "arrival": train.get("horaLlegada", ""),
                    "duration_min": train.get("duracionViajeTotalEnMinutos", 0),
                    "price": train.get("tarifaMinima"),
                    "available": RenfeScraper.is_train_available(train),
                    "train_type": train.get("tipoTrenUno", "N/A"),
                })
        return trains


def _get_idx() -> Generator:
    num = 0
    while True:
        yield num
        num += 1


def _create_search_id() -> str:
    search_id = "_"
    for _ in range(4):
        search_id += random.choice(string.ascii_letters + string.digits)
    return search_id


def _create_cookiedict(
    origin_name: str, origin_code: str,
    dest_name: str, dest_code: str,
) -> Dict[str, Any]:
    search = {
        "origen": {"code": origin_code, "name": origin_name},
        "destino": {"code": dest_code, "name": dest_name},
        "pasajerosAdultos": 1,
        "pasajerosNinos": 0,
        "pasajerosSpChild": 0,
    }
    return {"name": "Search", "value": str(search), "domain": ".renfe.com", "path": "/"}


def _create_search_payload(
    origin_name: str, origin_code: str,
    dest_name: str, dest_code: str,
    departure_date: datetime,
) -> Dict[str, str]:
    date_format = "%d/%m/%Y"
    return {
        "tipoBusqueda": "autocomplete",
        "currenLocation": "menuBusqueda",
        "vengoderenfecom": "SI",
        "desOrigen": origin_name,
        "desDestino": dest_name,
        "cdgoOrigen": origin_code,
        "cdgoDestino": dest_code,
        "idiomaBusqueda": "ES",
        "FechaIdaSel": departure_date.strftime(date_format),
        "FechaVueltaSel": "",
        "_fechaIdaVisual": departure_date.strftime(date_format),
        "_fechaVueltaVisual": "",
        "adultos_": "1",
        "ninos_": "0",
        "ninosMenores": "0",
        "codPromocional": "",
        "plazaH": "false",
        "sinEnlace": "false",
        "asistencia": "false",
        "franjaHoraI": "",
        "franjaHoraV": "",
        "Idioma": "es",
        "Pais": "ES",
    }


def _create_generate_id_payload(search_id: str, batch_id: int) -> str:
    if search_id is None:
        page = "page=%2Fvol%2FbuscarTrenEnlaces.do\n"
    else:
        page = f"page=%2Fvol%2FbuscarTrenEnlaces.do%3Fc%3D{search_id}\n"

    return (
        "callCount=1\n"
        "c0-scriptName=__System\n"
        "c0-methodName=generateId\n"
        "c0-id=0\n"
        f"batchId={batch_id}\n"
        "instanceId=0\n"
        f"{page}"
        "scriptSessionId=\n"
        "windowName=\n"
    )


def _create_update_session_objects_payload(search_id: str, script_session_id: str, batch_id: int) -> str:
    return (
        "callCount=1\n"
        "windowName=\n"
        "c0-scriptName=buyEnlacesManager\n"
        "c0-methodName=actualizaObjetosSesion\n"
        "c0-id=0\n"
        f"c0-e1=string:{search_id}\n"
        "c0-e2=string:\n"
        "c0-param0=array:[reference:c0-e1,reference:c0-e2]\n"
        f"batchId={batch_id}\n"
        "instanceId=0\n"
        f"page=%2Fvol%2FbuscarTrenEnlaces.do%3Fc%3D{search_id}\n"
        f"scriptSessionId={script_session_id}\n"
    )


def _create_get_train_list_payload(
    search_id: str, script_session_id: str,
    departure_date: datetime, batch_id: int,
) -> str:
    date_format = "%d/%m/%Y"
    departure_str = departure_date.strftime(date_format)
    payload = (
        "callCount=1\n"
        "windowName=\n"
        "c0-scriptName=trainEnlacesManager\n"
        "c0-methodName=getTrainsList\n"
        "c0-id=0\n"
        "c0-e1=string:false\n"
        "c0-e2=string:false\n"
        "c0-e3=string:false\n"
        "c0-e4=string:\n"
        "c0-e5=string:\n"
        "c0-e6=string:\n"
        "c0-e7=string:\n"
        f"c0-e8=string:{urllib.parse.quote_plus(departure_str)}\n"
        "c0-e9=string:\n"
        "c0-e10=string:1\n"
        "c0-e11=string:0\n"
        "c0-e12=string:0\n"
        "c0-e13=string:I\n"
        "c0-e14=string:\n"
        "c0-param0=Object_Object:{atendo:reference:c0-e1, sinEnlace:reference:c0-e2, "
        "plazaH:reference:c0-e3, tipoFranjaI:reference:c0-e4, tipoFranjaV:reference:c0-e5, "
        "horaFranjaIda:reference:c0-e6, horaFranjaVuelta:reference:c0-e7, fechaSalida:reference"
        ":c0-e8, fechaVuelta:reference:c0-e9, adultos:reference:c0-e10, ninos:reference:c0-e11, "
        "ninosMenores:reference:c0-e12, trayecto:reference:c0-e13, idaVuelta:reference:c0-e14}\n"
        f"batchId={batch_id}\n"
        "instanceId=0\n"
        f"page=%2Fvol%2FbuscarTrenEnlaces.do%3Fc%3D{search_id}\n"
        f"scriptSessionId={script_session_id}\n"
    )
    return payload


def _extract_dwr_token(response_text: str) -> str:
    pattern = r'r\.handleCallback\("[^"]+","[^"]+","([^"]+)"\)'
    match = re.search(pattern, response_text)
    if not match:
        raise RuntimeError("Could not extract DWR token from response")
    return match.group(1)


def _extract_train_list(response_text: str) -> Dict[str, Any]:
    match = re.search(r"r\.handleCallback\([^,]+,\s*[^,]+,\s*(\{.*\})\);", response_text, re.DOTALL)
    if not match:
        raise RuntimeError("Could not extract train list from DWR response")
    return json5.loads(match.group(1))


def _create_session_script_id(dwr_token: str) -> str:
    date_token = _tokenify(int(datetime.now().timestamp() * 1000))
    random_token = _tokenify(int(random.random() * 1e16))
    return f"{dwr_token}/{date_token}-{random_token}"


def _tokenify(number: int) -> str:
    charmap = "1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ*$"
    tokenbuf = []
    remainder = number
    while remainder > 0:
        tokenbuf.append(charmap[remainder & 0x3F])
        remainder //= 64
    return "".join(tokenbuf)
