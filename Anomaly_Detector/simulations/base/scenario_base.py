# simulations/base/scenario_base.py
from __future__ import annotations

import asyncio
import csv
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

from core.csms_server import CentralSystem
from core.charge_point import SimulatedChargePoint, connect_charge_point


def _utc_now_iso() -> str:
    """Timezone-aware UTC timestamp as ISO string (microseconds yok)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ScenarioConfig:
    """Base config that all scenarios can extend. """
    name: str


class ScenarioBase(ABC):
    """
    Tüm senaryoların miras aldığı temel sınıf.

    Sorumluluklar:
      - CSMS server'ı başlat / durdur
      - Simüle istasyonları bağla
      - CSMS event'lerini dinle ve loglara yaz
      - Senaryonun akışını run_for_all_charge_points'e bırak
    """

    # Eski birleşik dataset formatı (merge_logs / ML için)
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
        "soc_percent",
        "label",
        "raw_payload",
    ]

    # --- Gerçek CSMS log tabloları için kolon setleri ---

    # Sayaç / ölçüm verisi
    METER_FIELDNAMES = [
        "timestamp",
        "cp_id",
        "transaction_id",
        "connector_id",
        "power_kw",
        "current_a",
        "voltage_v",
        "soc_percent",
        "raw_payload",
    ]

    # Durum değişimleri
    STATUS_FIELDNAMES = [
        "timestamp",
        "cp_id",
        "connector_id",
        "status",
        "error_code",
        "raw_payload",
    ]

    # Heartbeat / health
    HEARTBEAT_FIELDNAMES = [
        "timestamp",
        "cp_id",
        "raw_payload",
    ]

    # Start / StopTransaction
    TRANSACTION_FIELDNAMES = [
        "timestamp",
        "cp_id",
        "event_type",        # StartTransaction / StopTransaction
        "transaction_id",
        "id_tag",
        "meter_start",
        "meter_stop",
        "reason",
        "raw_payload",
    ]

    # Ham event tablosu (audit / debug)
    RAW_EVENT_FIELDNAMES = [
        "timestamp",
        "cp_id",
        "message_type",
        "raw_payload",
    ]

    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

        # CSMS + CP durumları
        self._csms: Optional[CentralSystem] = None
        self._csms_task: Optional[asyncio.Task] = None
        self._cps: List[SimulatedChargePoint] = []

        # Eski tek CSV (birleşik dataset)
        self._csv_file = None
        self._csv_writer: Optional[csv.DictWriter] = None

        # Gerçek log tabloları
        self._mv_file = None
        self._mv_writer: Optional[csv.DictWriter] = None

        self._status_file = None
        self._status_writer: Optional[csv.DictWriter] = None

        self._hb_file = None
        self._hb_writer: Optional[csv.DictWriter] = None

        self._tx_file = None
        self._tx_writer: Optional[csv.DictWriter] = None

        self._raw_file = None
        self._raw_writer: Optional[csv.DictWriter] = None

        # Dahili state
        self._step_counter: int = 0
        self._mode: str = "normal"

        # İsteğe bağlı dış hook'lar (UI vs. için)
        self.event_hooks: List[Callable[[str, Dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # DIŞ API
    # ------------------------------------------------------------------
    async def run(
        self,
        mode: str,
        duration: int,
        stations: int,
        output_path: str,
    ) -> None:
        """
        run_simulation.py tarafından çağrılan ana giriş noktası.

        Akış:
          - CSV writer'ları hazırla
          - CSMS server'ı başlat
          - İstasyonları bağla
          - Senaryonun akışını çalıştır
          - En sonda tüm bağlantı / dosya cleanup
        """
        self._mode = mode
        self._step_counter = 0

        base_path = Path(output_path)
        base_path.parent.mkdir(parents=True, exist_ok=True)

        # --------------------------------------------------------------
        # 1) Eski birleşik CSV (ML / merge_logs için)
        # --------------------------------------------------------------
        self._csv_file = base_path.open("w", newline="", encoding="utf-8")
        self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=self.FIELDNAMES)
        self._csv_writer.writeheader()

        # --------------------------------------------------------------
        # 2) Gerçek CSMS logları (çoklu CSV)
        # --------------------------------------------------------------
        stem = base_path.stem
        suffix = base_path.suffix or ".csv"
        log_dir = base_path.parent

        def _open(name: str, fieldnames: List[str]):
            path = log_dir / f"{stem}_{name}{suffix}"
            f = path.open("w", newline="", encoding="utf-8")
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            return f, w

        self._mv_file, self._mv_writer = _open("meter_values", self.METER_FIELDNAMES)
        self._status_file, self._status_writer = _open("status_notifications", self.STATUS_FIELDNAMES)
        self._hb_file, self._hb_writer = _open("heartbeats", self.HEARTBEAT_FIELDNAMES)
        self._tx_file, self._tx_writer = _open("transactions", self.TRANSACTION_FIELDNAMES)
        self._raw_file, self._raw_writer = _open("events_raw", self.RAW_EVENT_FIELDNAMES)

        # --------------------------------------------------------------
        # 3) CSMS server'ını ayağa kaldır
        # --------------------------------------------------------------
        self._csms = CentralSystem(host="0.0.0.0", port=9000)
        # bütün event'ler buradan düşecek
        self._csms.event_callback = self._on_event
        self._csms_task = asyncio.create_task(self._csms.start())

        # Server gerçekten dinlemeye başlasın diye küçük bir bekleme
        await asyncio.sleep(0.2)

        # --------------------------------------------------------------
        # 4) İstasyonları bağla
        # --------------------------------------------------------------
        self._cps = []
        for idx in range(1, stations + 1):
            cp_id = f"CP_{idx:03d}"
            csms_url = f"ws://localhost:9000/{cp_id}"
            cp = await connect_charge_point(cp_id=cp_id, csms_url=csms_url)
            self._cps.append(cp)

        # --------------------------------------------------------------
        # 5) Senaryoya özel akışı çalıştır
        # --------------------------------------------------------------
        try:
            await self.run_for_all_charge_points(
                cps=self._cps,
                mode=mode,
                duration=duration,
            )
        finally:
            # --- CP bağlantılarını kapat ---
            for cp in self._cps:
                try:
                    await cp.connection.close()
                except Exception:
                    pass

            # --- CSMS server'ını durdur ---
            if self._csms is not None:
                try:
                    await self._csms.stop()
                except Exception:
                    pass

            if self._csms_task is not None:
                self._csms_task.cancel()
                try:
                    await self._csms_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    print(f"[WARN] CSMS task hata ile bitti ama loglama devam ediyor: {e!r}")

            # --- Tüm CSV dosyalarını kapat ---
            self._close_all_csv_files()

    # ------------------------------------------------------------------
    # EVENT HANDLING & LOGGING
    # ------------------------------------------------------------------
    def _close_all_csv_files(self) -> None:
        """Tüm CSV dosyalarını güvenli şekilde kapat."""
        for f in [
            self._csv_file,
            self._mv_file,
            self._status_file,
            self._hb_file,
            self._tx_file,
            self._raw_file,
        ]:
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass

        self._csv_file = None
        self._mv_file = None
        self._status_file = None
        self._hb_file = None
        self._tx_file = None
        self._raw_file = None

    def _on_event(self, message_type: str, event: Dict[str, Any]) -> None:
        """
        CentralSystem.event_callback tarafından çağrılır.

        - Dış hook'ları tetikler
        - Gerçek CSMS loglarını (çoklu CSV) yazar
        - Birleşik dataset için tek satırlık row üretir
        """
        # Hook'lar
        for hook in self.event_hooks:
            try:
                hook(message_type, event)
            except Exception:
                pass

        # Gerçek loglar
        self._log_realistic(message_type, event)

        # Birleşik CSV
        if self._csv_writer is None:
            return

        row = self._event_to_row(message_type, event, mode=self._mode)
        if row is None:
            return

        self._csv_writer.writerow(row)
        if self._csv_file is not None:
            self._csv_file.flush()

    # simulations/base/scenario_base.py

    def _log_realistic(self, message_type: str, raw_event: Dict[str, Any]) -> None:
        """Gerçek CSMS tablolarına karşılık gelen CSV'lere yazar."""
        event = dict(raw_event)
        ts = event.get("timestamp") or _utc_now_iso()
        cp_id = event.get("cp_id")

        # 1) Ham event tablosu (her şey buraya da gider)
        if self._raw_writer is not None:
            self._raw_writer.writerow(
                {
                    "timestamp": ts,
                    "cp_id": cp_id,
                    "message_type": message_type,
                    "raw_payload": json.dumps(event, ensure_ascii=False),
                }
            )
            if self._raw_file is not None:
                self._raw_file.flush()

        # 2) Mesaj tipine göre ayrı tablolar
        if message_type == "Heartbeat":
            # 👉 HEARTBEAT'LER BURAYA DÜŞÜYOR
            if self._hb_writer is not None:
                self._hb_writer.writerow(
                    {
                        "timestamp": ts,
                        "cp_id": cp_id,
                        "raw_payload": json.dumps(event, ensure_ascii=False),
                    }
                )
                if self._hb_file is not None:
                    self._hb_file.flush()

        elif message_type == "StatusNotification":
            if self._status_writer is not None:
                row = {
                    "timestamp": ts,
                    "cp_id": cp_id,
                    "connector_id": event.get("connector_id"),
                    "status": event.get("status"),
                    "error_code": event.get("error_code"),
                    "raw_payload": json.dumps(event, ensure_ascii=False),
                }
                self._status_writer.writerow(row)
                if self._status_file is not None:
                    self._status_file.flush()

        elif message_type == "MeterValues":
            if self._mv_writer is not None:
                power_kw: Optional[float] = None
                current_a: Optional[float] = None
                voltage_v: Optional[float] = None
                soc_percent: Optional[float] = None

                meter_value = event.get("meter_value", [])

                try:
                    if isinstance(meter_value, list) and meter_value:
                        first_mv = meter_value[0]

                        if isinstance(first_mv, dict):
                            sampled_values = (
                                first_mv.get("sampledValue")
                                or first_mv.get("sampled_value")
                                or []
                            )
                        else:
                            sampled_values = (
                                getattr(first_mv, "sampledValue", None)
                                or getattr(first_mv, "sampled_value", None)
                                or []
                            )

                        for sv in sampled_values:
                            if isinstance(sv, dict):
                                meas = sv.get("measurand")
                                val_str = sv.get("value")
                            else:
                                meas = getattr(sv, "measurand", None)
                                val_str = getattr(sv, "value", None)

                            if val_str is None:
                                continue

                            try:
                                val = float(val_str)
                            except (TypeError, ValueError):
                                continue

                            if meas == "Power.Active.Import":
                                power_kw = val
                            elif meas == "Current.Import":
                                current_a = val
                            elif meas == "Voltage":
                                voltage_v = val
                            elif meas in ("SoC", "StateOfCharge"):
                                soc_percent = val
                except Exception:
                    # Log kırılmasın diye yutuyoruz
                    pass

                row = {
                    "timestamp": ts,
                    "cp_id": cp_id,
                    "transaction_id": event.get("transaction_id"),
                    "connector_id": event.get("connector_id"),
                    "power_kw": power_kw,
                    "current_a": current_a,
                    "voltage_v": voltage_v,
                    "soc_percent": soc_percent,
                    "raw_payload": json.dumps(event, ensure_ascii=False),
                }
                self._mv_writer.writerow(row)
                if self._mv_file is not None:
                    self._mv_file.flush()

        elif message_type in ("StartTransaction", "StopTransaction"):
            if self._tx_writer is not None:
                row = {
                    "timestamp": ts,
                    "cp_id": cp_id,
                    "event_type": message_type,
                    "transaction_id": event.get("transaction_id"),
                    "id_tag": event.get("id_tag"),
                    "meter_start": event.get("meter_start"),
                    "meter_stop": event.get("meter_stop"),
                    "reason": event.get("reason"),
                    "raw_payload": json.dumps(event, ensure_ascii=False),
                }
                self._tx_writer.writerow(row)
                if self._tx_file is not None:
                    self._tx_file.flush()

    # ------------------------------------------------------------------
    # Birleşik dataset (ML) için row üretici
    # ------------------------------------------------------------------
    def _event_to_row(
        self,
        message_type: str,
        raw_event: Dict[str, Any],
        mode: str,
    ) -> Optional[Dict[str, Any]]:
        """
        CSMS'ten gelen event'i tek tip CSV satırına çevirir.
        (Eski pipeline / merge_logs ve anomaly çalışmaları için.)
        """
        event = dict(raw_event)

        timestamp = event.get("timestamp") or _utc_now_iso()
        cp_id = event.get("cp_id") or event.get("charge_point_id")
        connector_id = event.get("connector_id")
        transaction_id = event.get("transaction_id")
        id_tag = event.get("id_tag")

        power_kw: Optional[float] = None
        current_a: Optional[float] = None
        voltage_v: Optional[float] = None
        soc_percent: Optional[float] = None

        if message_type == "MeterValues":
            meter_value = event.get("meter_value", [])
            try:
                if isinstance(meter_value, list) and meter_value:
                    first_mv = meter_value[0]

                    if isinstance(first_mv, dict):
                        sampled_values = (
                            first_mv.get("sampledValue")
                            or first_mv.get("sampled_value")
                            or []
                        )
                    else:
                        sampled_values = (
                            getattr(first_mv, "sampledValue", None)
                            or getattr(first_mv, "sampled_value", None)
                            or []
                        )

                    for sv in sampled_values:
                        if isinstance(sv, dict):
                            meas = sv.get("measurand")
                            val_str = sv.get("value")
                        else:
                            meas = getattr(sv, "measurand", None)
                            val_str = getattr(sv, "value", None)

                        if val_str is None:
                            continue

                        try:
                            val = float(val_str)
                        except (TypeError, ValueError):
                            continue

                        if meas == "Power.Active.Import":
                            power_kw = val
                        elif meas == "Current.Import":
                            current_a = val
                        elif meas == "Voltage":
                            voltage_v = val
                        elif meas in ("SoC", "StateOfCharge"):
                            soc_percent = val
            except Exception:
                pass


        label = self.get_label_for_event(event, mode=mode)

        # step'i her event'te bir artır
        self._step_counter += 1
        step = self._step_counter

        row: Dict[str, Any] = {
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
            "soc_percent": soc_percent,
            "label": label,
            "raw_payload": json.dumps(event, ensure_ascii=False),
        }
        return row

    # ------------------------------------------------------------------
    # ALT SINIFLARIN UYGULAYACAĞI METOTLAR
    # ------------------------------------------------------------------
    @abstractmethod
    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """Senaryo akışını burada implement ediyorsun."""
        raise NotImplementedError

    @abstractmethod
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Her event için anomaly label'ı belirler.

        Örn:
          - normal → "normal"
          - attack modda MeterValues → "oscillatory_load_attack"
          - meta eventler → "attack_meta" gibi
        """
        raise NotImplementedError
