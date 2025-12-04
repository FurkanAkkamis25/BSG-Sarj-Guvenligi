# simulations/base/scenario_base.py
from __future__ import annotations

import asyncio
import csv
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from core.csms_server import CentralSystem
from core.charge_point import SimulatedChargePoint, connect_charge_point


@dataclass
class ScenarioConfig:
    name: str


class ScenarioBase(ABC):
    """
    Tüm senaryoların miras aldığı temel sınıf.

    - CSMS'i ayağa kaldırır
    - Sanal charge point'leri (SimulatedChargePoint) bağlar
    - Gelen TÜM OCPP event'leri (BootNotification, Authorize, StartTransaction,
      MeterValues, StopTransaction) event_callback ile CSV'ye yazar
    - Satırları _event_to_row() ile normalize eder
    - Etiketleme (label) işini alt sınıflardaki get_label_for_event() yapar
    """

    # CSV kolonları
    FIELDNAMES = [
        "timestamp",
        "charge_point_id",
        "scenario",
        "mode",
        "step",
        "message_type",
        "transaction_id",
        "connector_id",
        "id_tag",
        "power_kw",
        "current_a",
        "voltage_v",
        "label",
        "raw_payload",
    ]

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Ana çalışma akışı
    # ------------------------------------------------------------------
    async def run(
        self,
        mode: str,
        duration: int,
        stations: int,
        output_path: str,
    ) -> None:
        """
        Tüm senaryolar için ortak akış:

        1) CSMS server ayağa kalkar
        2) 'stations' kadar SimulatedChargePoint CSMS'e bağlanır
        3) Alt sınıfın run_for_all_charge_points(...) fonksiyonu çalışır
           (MeterValues, Authorize, StartTransaction vs. çağrıları burada)
        4) CSMS kapatılır, tüm websocket bağlantıları temizlenir
        5) Tüm OCPP event'ler CSV'ye satır satır yazılır
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(exist_ok=True, parents=True)

        # Her charge point için step counter tutuyoruz
        step_counters: Dict[str, int] = {}

        csms = CentralSystem()

        with output_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDNAMES)
            writer.writeheader()

            # CSMS'ten gelen her event burada satıra dönüştürülüp CSV'ye yazılır
            #
            # DİKKAT:
            #   csms_server._fire_event(name, payload) -> buraya 2 argüman gelir:
            #     - message_type: "BootNotification", "Authorize", "MeterValues" ...
            #     - raw_event   : genelde dict, bazen payload yapısı
            def event_callback(message_type: str, raw_event: Any) -> None:
                # Normalize et: her durumda _event_to_row'un beklediği "event" dict'ini üret.
                if isinstance(raw_event, dict):
                    # Kopya al ki _event_to_row içinde değiştirilse bile orijinali bozulmasın.
                    event: Dict[str, Any] = dict(raw_event)
                    # message_type yoksa set et
                    event.setdefault("message_type", message_type)
                    # timestamp yoksa şimdi ver
                    event.setdefault("timestamp", datetime.utcnow().isoformat())
                else:
                    # Dict değilse (örneğin MeterValues için liste v.b.)
                    # hepsini payload altına koyup minimal bir event objesi oluştur.
                    event = {
                        "timestamp": datetime.utcnow().isoformat(),
                        "charge_point_id": None,
                        "message_type": message_type,
                        "payload": raw_event,
                    }

                # cp_id -> charge_point_id normalize et
                if "cp_id" in event and "charge_point_id" not in event:
                    event["charge_point_id"] = event["cp_id"]

                row = self._event_to_row(
                    event=event,
                    mode=mode,
                    step_counters=step_counters,
                )
                writer.writerow(row)
                # İstersen hemen diske yazması için:
                f.flush()

            # CSMS tarafına callback'i ver
            csms.event_callback = event_callback

            # CSMS'i background task olarak başlat
            csms_task = asyncio.create_task(csms.start())
            # Server ayağa kalksın diye küçük gecikme
            await asyncio.sleep(0.2)

            # Charge point'leri bağla
            cps: List[SimulatedChargePoint] = []
            for i in range(1, stations + 1):
                cp_id = f"CP{i:03d}"
                csms_url = f"ws://{csms.host}:{csms.port}/{cp_id}"
                cp = await connect_charge_point(cp_id, csms_url)
                cps.append(cp)

            try:
                # Senaryonun özel akışını alt sınıf yürütür
                await self.run_for_all_charge_points(
                    cps=cps,
                    mode=mode,
                    duration=duration,
                )
            finally:
                # Ne olursa olsun CSMS'i kapat
                await csms.stop()
                # CSMS task'ini temizle, CancelledError gelirse umursama
                try:
                    await csms_task
                except asyncio.CancelledError:
                    pass

    # ------------------------------------------------------------------
    # Event -> CSV satırı dönüştürücü
    # ------------------------------------------------------------------
    def _event_to_row(
        self,
        event: Dict[str, Any],
        mode: str,
        step_counters: Dict[str, int],
    ) -> Dict[str, Any]:
        """
        CSMS'ten gelen ham event sözlüğünü tek tip CSV satırına dönüştürür.
        """
        timestamp = event.get("timestamp")

        # CSMS bazen "cp_id" gönderiyor, biz CSV'de "charge_point_id" istiyoruz
        cp_id = (
            event.get("charge_point_id")
            or event.get("cp_id")
            or "UNKNOWN_CP"
        )

        message_type = event.get("message_type")
        payload = event.get("payload", {}) or {}

        # step sayacı (charge point bazlı)
        prev_step = step_counters.get(cp_id, 0)
        step = prev_step + 1
        step_counters[cp_id] = step

        # Ortak alanlar (connector, tx_id, id_tag)
        connector_id = (
            event.get("connector_id")
            or payload.get("connector_id")
        )

        transaction_id = (
            event.get("transaction_id")
            or event.get("tx_id")
            or payload.get("transaction_id")
            or payload.get("tx_id")
        )

        id_tag = (
            event.get("id_tag")
            or payload.get("id_tag")
        )

        power_kw = None
        current_a = None
        voltage_v = None

        # Sadece MeterValues mesajları için güç/akım/gerilim çıkar
        if message_type == "MeterValues":
            try:
                # Asıl meter_value event'in kökünde, yoksa payload'dan dene
                meter_values = (
                    event.get("meter_value")
                    or payload.get("meter_value")
                    or []
                )
                if meter_values:
                    mv0 = meter_values[0]

                    # Hem camelCase (sampledValue) hem snake_case (sampled_value) destekle
                    sampled_values = (
                        mv0.get("sampledValue")
                        or mv0.get("sampled_value")
                        or []
                    )

                    for sv in sampled_values:
                        meas = sv.get("measurand")
                        val_str = sv.get("value")
                        if val_str is None:
                            continue
                        try:
                            val = float(val_str)
                        except (ValueError, TypeError):
                            continue

                        if meas == "Power.Active.Import":
                            power_kw = val
                        elif meas == "Current.Import":
                            current_a = val
                        elif meas == "Voltage":
                            voltage_v = val
            except Exception:
                # parsing patlarsa sessizce geç
                pass

        # Etiket (anomaly label) alt senaryonun kararına göre
        label = self.get_label_for_event(event=event, mode=mode)

        row = {
            "timestamp": timestamp,
            "charge_point_id": cp_id,
            "scenario": self.config.name,
            "mode": mode,
            "step": step,
            "message_type": message_type,
            "transaction_id": transaction_id,
            "connector_id": connector_id,
            "id_tag": id_tag,
            "power_kw": power_kw,
            "current_a": current_a,
            "voltage_v": voltage_v,
            "label": label,
            # Debug kolay olsun diye tüm event'i raw_payload içine gömüyoruz
            "raw_payload": json.dumps(event, ensure_ascii=False),
        }
        return row

    # ------------------------------------------------------------------
    # Alt sınıfların implemente etmesi gereken kısımlar
    # ------------------------------------------------------------------
    @abstractmethod
    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Alt sınıf burada senaryonun asıl OCPP akışını tanımlar:

        - Authorize
        - StartTransaction
        - periyodik MeterValues
        - StopTransaction
        vs.
        """
        raise NotImplementedError

    @abstractmethod
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Her event için anomaly label'ı belirler.

        Örneğin:
            - normal modda: "normal"
            - attack modda, MeterValues için: "oscillatory_load_attack"
            - attack modda, meta eventler için: "attack_meta"
        """
        raise NotImplementedError
