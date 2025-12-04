# core/charge_point.py
import asyncio
import logging
from typing import Optional

import websockets
from ocpp.v16 import ChargePoint as CpBase
from ocpp.v16 import call, call_result
from ocpp.v16.enums import RegistrationStatus
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)


class SimulatedChargePoint(CpBase):
    """
    OCPP 1.6-J üzerinden CSMS'e bağlanan simüle şarj istasyonu.

    Bu sınıf:
    - BootNotification
    - Authorize (RFID / kullanıcı kimlik doğrulama)
    - StartTransaction / StopTransaction
    - MeterValues (güç, akım, gerilim)

    gönderebilen genel bir client gibi çalışır.

    Anomali senaryoları (normal, dalgalı yük, kimlik hırsızlığı vb.)
    'simulations/' içindeki dosyalardan bu sınıfın metodlarını kullanarak
    OCPP akışını tetikleyecek.
    """

    # ------------------------------------------------------------------
    # BOOT
    # ------------------------------------------------------------------
    async def send_boot_notification(self):
        """
        CP ilk açıldığında CSMS'e kendini tanıtır.
        """
        req = call.BootNotification(
            charge_point_model="BSG-Simulated-CP",
            charge_point_vendor="BSG-Team",
        )
        res: call_result.BootNotification = await self.call(req)

        if res.status == RegistrationStatus.accepted:
            logging.info("[%s] BootNotification accepted", self.id)
        else:
            logging.warning(
                "[%s] BootNotification NOT accepted: %s",
                self.id,
                res.status,
            )

    # ------------------------------------------------------------------
    # AUTHORIZE (Kimlik Doğrulama)
    # ------------------------------------------------------------------
    async def send_authorize(self, id_tag: str) -> call_result.Authorize:
        """
        RFID kart / kullanıcı kimlik doğrulama isteği.
        Kimlik hırsızlığı anomalisi için senaryo bu fonksiyonu
        sahte / klonlanmış id_tag'lerle çağırabilir.
        """
        req = call.Authorize(
            id_tag=id_tag
        )
        res: call_result.Authorize = await self.call(req)

        logging.info(
            "[%s] Authorize result for id_tag=%s -> %s",
            self.id,
            id_tag,
            getattr(res.id_tag_info, "status", "Unknown"),
        )

        return res

    # ------------------------------------------------------------------
    # START TRANSACTION
    # ------------------------------------------------------------------
    async def send_start_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int = 0,
    ) -> call_result.StartTransaction:
        """
        Şarj oturumu başlangıcı.
        """
        now = datetime.now(tz=timezone.utc).isoformat()

        req = call.StartTransaction(
            connector_id=connector_id,
            id_tag=id_tag,
            meter_start=meter_start,
            timestamp=now,
        )

        res: call_result.StartTransaction = await self.call(req)

        logging.info(
            "[%s] StartTransaction -> tx_id=%s, status=%s",
            self.id,
            res.transaction_id,
            getattr(res.id_tag_info, "status", "Unknown"),
        )

        return res

    # ------------------------------------------------------------------
    # METER VALUES
    # ------------------------------------------------------------------
    async def send_meter_values(
        self,
        connector_id: int,
        power_kw: float,
        current_a: float,
        voltage_v: float,
        transaction_id: Optional[int] = None,
    ):
        """
        Detaylı MeterValues payload'ı gönderiyoruz:
        - Power.Active.Import (kW)
        - Current.Import (A)
        - Voltage (V)

        Dalgalı Yük Saldırısı (Oscillatory Load) senaryosunda özellikle
        power_kw ve current_a anomali üretmek için kullanılacak.
        """
        timestamp = datetime.now(tz=timezone.utc).isoformat()

        sampled_values = [
            {
                "value": str(power_kw),
                "measurand": "Power.Active.Import",
                "unit": "kW",
            },
            {
                "value": str(current_a),
                "measurand": "Current.Import",
                "unit": "A",
            },
            {
                "value": str(voltage_v),
                "measurand": "Voltage",
                "unit": "V",
            },
        ]

        payload = {
            "connector_id": connector_id,
            "meter_value": [
                {
                    "timestamp": timestamp,
                    "sampledValue": sampled_values,
                }
            ],
        }

        # OCPP 1.6'da transactionId opsiyonel alan, doluysa ekleyelim
        if transaction_id is not None:
            payload["transaction_id"] = transaction_id

        req = call.MeterValues(**payload)
        await self.call(req)

        logging.debug(
            "[%s] MeterValues sent | conn=%s tx_id=%s P=%.3f kW I=%.3f A V=%.1f V",
            self.id,
            connector_id,
            transaction_id,
            power_kw,
            current_a,
            voltage_v,
        )

    # ------------------------------------------------------------------
    # STOP TRANSACTION
    # ------------------------------------------------------------------
    async def send_stop_transaction(
        self,
        transaction_id: int,
        meter_stop: int = 0,
    ) -> call_result.StopTransaction:
        """
        Şarj oturumu bitişi.
        """
        now = datetime.now(tz=timezone.utc).isoformat()

        req = call.StopTransaction(
            transaction_id=transaction_id,
            meter_stop=meter_stop,
            timestamp=now,
        )

        res: call_result.StopTransaction = await self.call(req)

        logging.info(
            "[%s] StopTransaction -> tx_id=%s",
            self.id,
            transaction_id,
        )

        return res


# ----------------------------------------------------------------------
# Yardımcı fonksiyon: Charge Point'e bağlan
# ----------------------------------------------------------------------
async def connect_charge_point(cp_id: str, csms_url: str) -> SimulatedChargePoint:
    """
    Senaryo tarafında kullanacağımız yardımcı fonksiyon.

    - CSMS websocket'ine bağlanır
    - SimulatedChargePoint nesnesini oluşturur
    - cp.start() için background task başlatır
    - BootNotification gönderir
    - Hazır ChargePoint nesnesini geri döner

    Örnek kullanım (senaryo tarafında):

        cp = await connect_charge_point("CP_1", "ws://127.0.0.1:9000/CP_1")
        await cp.send_authorize("TAG123")
        res = await cp.send_start_transaction(1, "TAG123")
        tx_id = res.transaction_id
        await cp.send_meter_values(1, 7.2, 18.0, 400.0, transaction_id=tx_id)
        await cp.send_stop_transaction(tx_id)
    """
    # CSMS'e websocket ile bağlan
    ws = await websockets.connect(csms_url, subprotocols=["ocpp1.6"])
    cp = SimulatedChargePoint(cp_id, ws)

    # CSMS'ten gelen mesajları dinleyen loop'u background'da çalıştır
    asyncio.create_task(cp.start())

    # BootNotification gönder
    await cp.send_boot_notification()

    logging.info("[%s] Connected to CSMS and BootNotification sent.", cp_id)

    return cp
