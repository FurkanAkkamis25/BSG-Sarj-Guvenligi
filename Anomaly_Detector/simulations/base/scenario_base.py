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
        cp_list: list[str] | None = None,
    ) -> None:
        """
        run_simulation.py tarafından çağrılan ana giriş noktası.

        Akış:
          - CSV writer'ları hazırla
          - CSMS server'ı başlat
          - İstasyonları bağla
          - Senaryonun akışını çalıştır
          - En sonda tüm bağlantı / dosya cleanup
        
        Parametreler:
          cp_list: Opsiyonel CP ID listesi. Verilmezse CP_001'den başlayarak
                   stations kadar CP oluşturulur (geriye uyumlu).
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
        # Raw log klasör yapısı:
        #   logs/raw/{normal|attack}/{scenario_name}/{YYYYMMDD_HHMMSS}/
        # içine ayrı CSV'ler (meter_values, status_notifications, ...)
        label_dir = "normal" if mode == "normal" else "attack"
        ts_folder = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        raw_dir = Path("logs") / "raw" / label_dir / self.config.name / ts_folder
        raw_dir.mkdir(parents=True, exist_ok=True)

        def _open(name: str, fieldnames: List[str]):
            path = raw_dir / f"{name}.csv"
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
        # TLS'i aktifleştirmek için use_tls=True kullanıyoruz. Sertifika dosyaları
        # bulunamazsa csms_server otomatik olarak WS moduna geri düşer.
        self._csms = CentralSystem(host="0.0.0.0", port=9000, use_tls=True)
        # bütün event'ler buradan düşecek
        self._csms.event_callback = self._on_event
        self._csms_task = asyncio.create_task(self._csms.start())

        # Server gerçekten dinlemeye başlasın diye bekleme (1 saniye)
        await asyncio.sleep(1.0)

        # --------------------------------------------------------------
        # 4) İstasyonları bağla (paralel bağlantı için)
        # --------------------------------------------------------------
        self._cps = []
        
        # CSMS TLS kullanıyor mu? Buna göre ws / wss seçelim
        scheme = "wss" if getattr(self._csms, "use_tls", False) else "ws"
        print(f"[INFO] CSMS bağlantı şeması: {scheme.upper()} (TLS={'ON' if scheme == 'wss' else 'OFF'})")
        
        async def connect_with_retry(cp_id: str, csms_url: str, max_retries: int = 3):
            """Bağlantıyı retry ile dene"""
            for attempt in range(max_retries):
                try:
                    cp = await connect_charge_point(cp_id=cp_id, csms_url=csms_url)
                    print(f"[OK] {cp_id} başarıyla bağlandı (deneme {attempt + 1}/{max_retries})")
                    return cp
                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = 0.5 * (attempt + 1)
                        print(
                            f"[RETRY] {cp_id} bağlantı hatası (deneme {attempt + 1}/{max_retries}): "
                            f"{e}. {wait_time:.1f} saniye bekleniyor..."
                        )
                        await asyncio.sleep(wait_time)  # Basit backoff
                        continue
                    else:
                        print(f"[ERROR] {cp_id} {max_retries} denemeden sonra bağlanamadı: {e}")
                        return None
        
        # CP listesini belirle
        if cp_list is not None:
            # Kullanıcı CP listesi vermiş - bunları kullan
            target_cp_ids = cp_list
            print(f"[INFO] Kullanıcı tarafından belirtilen {len(target_cp_ids)} CP bağlanacak: {', '.join(target_cp_ids)}")
        else:
            # Geriye uyumlu: CP_001'den başlayarak stations kadar
            target_cp_ids = [f"CP_{idx:03d}" for idx in range(1, stations + 1)]
            print(f"[INFO] {stations} istasyon otomatik oluşturulacak: {', '.join(target_cp_ids)}")
        
        # Paralel bağlantı (batch'ler halinde)
        batch_size = 5  # Her seferinde 5 istasyon bağla (CSMS'e yük bindirmemek için)
        print(f"[INFO] {len(target_cp_ids)} istasyon {batch_size}'li batch'ler halinde bağlanacak...")
        
        for batch_start in range(0, len(target_cp_ids), batch_size):
            batch_end = min(batch_start + batch_size, len(target_cp_ids))
            batch_tasks = []
            
            batch_cp_ids = target_cp_ids[batch_start:batch_end]
            print(f"[INFO] Batch {batch_start+1}-{batch_end} bağlanıyor: {', '.join(batch_cp_ids)}")
            
            for cp_id in batch_cp_ids:
                # !!! BURASI ARTIK TLS-DOSTU
                csms_url = f"{scheme}://localhost:9000/{cp_id}"
                batch_tasks.append(connect_with_retry(cp_id, csms_url))
            
            # Bu batch'i paralel bağla
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            for result in batch_results:
                if result is not None and not isinstance(result, Exception):
                    self._cps.append(result)
                elif isinstance(result, Exception):
                    print(f"[ERROR] Bağlantı hatası: {result}")
            
            # Batch'ler arası bekleme (CSMS'e yük bindirmemek için)
            if batch_end < len(target_cp_ids):
                await asyncio.sleep(0.5)  # 0.2'den 0.5'e çıkarıldı
        
        print(f"[INFO] {len(self._cps)}/{len(target_cp_ids)} istasyon başarıyla bağlandı.")

        # Boş CP listesi kontrolü
        if not self._cps:
            print(f"[ERROR] Hiçbir istasyon bağlanamadı! Simülasyon durduruluyor.")
            print(f"[ERROR] Lütfen CSMS server'ın çalıştığından ve port 9000'in açık olduğundan emin olun.")
            return


        # --------------------------------------------------------------
        # 5) Senaryoya özel akışı çalıştır
        # --------------------------------------------------------------
        try:
            print(f"[INFO] Senaryo akışı başlatılıyor ({len(self._cps)} CP ile)...")
            await self.run_for_all_charge_points(
                cps=self._cps,
                mode=mode,
                duration=duration,
            )
            print(f"[INFO] Senaryo akışı tamamlandı.")
        except Exception as e:
            print(f"[ERROR] Senaryo akışında hata oluştu: {e}")
            import traceback
            print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
            raise
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
        if message_type == "Heartbeat":
            return None
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
