import asyncio
import logging
from datetime import datetime
from typing import Callable, Dict, Any, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp.v16 import call_result
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16.enums import RegistrationStatus, AuthorizationStatus
from ocpp.routing import on


logger = logging.getLogger(__name__)

# Basit bir IDTag listesi. İleride bunu dosyadan / DB'den okuyabilirsin.
VALID_TAGS: Dict[str, str] = {
    "YUNUS_TAG": "Yunus Emeç",
    "AYSE_TAG": "Ayşe Yılmaz",
    "TEST123": "Test Kullanıcısı",
}


class CSMSChargePoint(OcppChargePoint):
    """
    Merkez sistem tarafındaki ChargePoint implementasyonu.
    BootNotification, Authorize, StartTransaction, MeterValues, StopTransaction,
    Heartbeat ve StatusNotification çağrılarını karşılar.
    """

    def __init__(
        self,
        charge_point_id: str,
        connection: WebSocketServerProtocol,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(charge_point_id, connection)
        self.id = charge_point_id

        self.event_callback = event_callback

        # CSMS tarafında transaction id sayacı
        self._next_tx_id: int = 1

        # Heartbeat takibi
        self._last_heartbeat: Optional[datetime] = None
        self._heartbeat_interval: int = 10  # saniye

        # Connector durumları
        # connector_id -> {"status": str, "error_code": str, "timestamp": str}
        self._connectors: Dict[int, Dict[str, Any]] = {}
                # Connector durumları
        # connector_id -> {"status": str, "error_code": str, "timestamp": str}
        self._connectors: Dict[int, Dict[str, Any]] = {}

        # Bu bağlantı boyunca yetkilendirilmiş IDTag'ler
        # (Authorize çağrısında Accepted dönenler buraya kaydedilecek)
        self._authorized_tags = set()


    # ------------------------------------------------------------------
    # Yardımcı: Event'i senaryo / üst kata fırlat
    # ------------------------------------------------------------------
    def _fire_event(self, message_type: str, data: Dict[str, Any]) -> None:
        if not self.event_callback:
            return

        event = dict(data)
        event.setdefault("cp_id", self.id)
        event.setdefault("message_type", message_type)
        event.setdefault("timestamp", datetime.utcnow().isoformat())

        try:
            self.event_callback(message_type, event)
        except Exception:
            logger.exception(
                "[CSMS] Event callback hata verdi (type=%s, cp_id=%s)",
                message_type,
                self.id,
            )

    # ------------------------------------------------------------------
    # OCPP HANDLER'LAR
    # ------------------------------------------------------------------

    @on("BootNotification")
    async def on_boot_notification(
        self,
        charge_point_vendor: str,
        charge_point_model: str,
        **kwargs,
    ):
        """
        CP bağlandığında ilk gelen mesaj. CSMS burada CP'yi kayıt altına alır
        ve heartbeat interval döner.
        """
        now = datetime.utcnow().isoformat()
        self._last_heartbeat = datetime.utcnow()

        payload = {
            "cp_id": self.id,
            "vendor": charge_point_vendor,
            "model": charge_point_model,
            "payload": kwargs,
        }
        self._fire_event("BootNotification", payload)

        logger.info(
            "[CSMS] BootNotification from %s (vendor=%s, model=%s)",
            self.id,
            charge_point_vendor,
            charge_point_model,
        )

        return call_result.BootNotification(
            current_time=now,
            interval=self._heartbeat_interval,
            status=RegistrationStatus.accepted,
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **kwargs):
        """
        CP'nin hala hayatta olduğunu gösteren ping.
        """
        self._last_heartbeat = datetime.utcnow()

        payload = {
            "cp_id": self.id,
            "payload": kwargs,
        }
        self._fire_event("Heartbeat", payload)

        now = datetime.utcnow().isoformat()
        return call_result.Heartbeat(current_time=now)

    @on("StatusNotification")
    async def on_status_notification(
        self,
        connector_id: int,
        error_code: str,
        status: str,
        timestamp: Optional[str] = None,
        **kwargs,
    ):
        """
        Connector durumu değiştiğinde (Available, Preparing, Charging, Finishing, Faulted vs.)
        """
        ts = timestamp or datetime.utcnow().isoformat()

        self._connectors[connector_id] = {
            "status": status,
            "error_code": error_code,
            "timestamp": ts,
            "payload": kwargs,
        }

        payload = {
            "cp_id": self.id,
            "connector_id": connector_id,
            "status": status,
            "error_code": error_code,
            "timestamp": ts,
            "payload": kwargs,
        }
        self._fire_event("StatusNotification", payload)

        logger.info(
            "[CSMS] StatusNotification from %s: conn=%s, status=%s, error=%s",
            self.id,
            connector_id,
            status,
            error_code,
        )

        return call_result.StatusNotification()

    @on("Authorize")
    async def on_authorize(self, id_tag: str, **kwargs):
        """
        Kimlik doğrulama.
        """
        # VALID_TAGS sözlüğünü kullan (üstte tanımlı)
        if id_tag in VALID_TAGS:
            status = AuthorizationStatus.accepted
            # Bu IDTag bu bağlantı için yetkili kabul edildi
            self._authorized_tags.add(id_tag)
        else:
            status = AuthorizationStatus.invalid

        event = {
            "cp_id": self.id,
            "id_tag": id_tag,
            "status": status.value,
            "authorized": status == AuthorizationStatus.accepted,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._fire_event("Authorize", event)

        logger.info(
            "[CSMS] Authorize from %s: id_tag=%s, status=%s",
            self.id,
            id_tag,
            status.value,
        )

        return call_result.Authorize(
            id_tag_info={"status": status}
        )

    @on("StartTransaction")
    async def on_start_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int,
        timestamp: str,
        **kwargs,
    ):
        """
        Şarj oturumu başlatma çağrısı.
        """

        # 1) IDTag daha önce Authorize ile ACCEPTED edilmemişse reddet
        authorized = id_tag in self._authorized_tags

        if not authorized:
            payload = {
                "cp_id": self.id,
                "connector_id": connector_id,
                "id_tag": id_tag,
                "meter_start": meter_start,
                "timestamp": timestamp,
                "authorized": False,
                "reason": "id_tag_not_authorized",
                "payload": kwargs,
            }
            # İstersen burada farklı bir event adı kullanabilirsin
            self._fire_event("StartTransactionRejected", payload)

            logger.warning(
                "[CSMS] StartTransaction REJECTED from %s: conn=%s, id_tag=%s (Authorize yok veya Invalid)",
                self.id,
                connector_id,
                id_tag,
            )

            # transaction_id=0 dönüyoruz -> senaryo tarafında zaten
            # 0/None kontrolü yapıp oturumu başlatmazsın
            return call_result.StartTransaction(
                transaction_id=0,
                id_tag_info={"status": AuthorizationStatus.invalid},
            )

        # 2) IDTag yetkili -> normal akış
        tx_id = self._next_tx_id
        self._next_tx_id += 1

        self._connectors.setdefault(connector_id, {})
        self._connectors[connector_id]["status"] = "Charging"
        self._connectors[connector_id]["last_tx_id"] = tx_id

        payload = {
            "cp_id": self.id,
            "connector_id": connector_id,
            "id_tag": id_tag,
            "meter_start": meter_start,
            "timestamp": timestamp,
            "transaction_id": tx_id,
            "authorized": True,
            "payload": kwargs,
        }
        self._fire_event("StartTransaction", payload)

        logger.info(
            "[CSMS] StartTransaction from %s: conn=%s, tx_id=%s, id_tag=%s",
            self.id,
            connector_id,
            tx_id,
            id_tag,
        )

        return call_result.StartTransaction(
            transaction_id=tx_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )
    
    @on("MeterValues")
    async def on_meter_values(
        self,
        connector_id: int,
        meter_value,
        transaction_id: Optional[int] = None,
        **kwargs,
    ):
        """
        Şarj sırasında akan sayaç verileri (Voltage, Current.Import, Power.Active.Import vs.)
        """
        payload = {
            "cp_id": self.id,
            "connector_id": connector_id,
            "meter_value": meter_value,
            "transaction_id": transaction_id,
            "payload": kwargs,
        }
        self._fire_event("MeterValues", payload)

        logger.debug(
            "[CSMS] MeterValues from %s: conn=%s, tx_id=%s, raw=%s",
            self.id,
            connector_id,
            transaction_id,
            meter_value,
        )

        return call_result.MeterValues()

    @on("StopTransaction")
    async def on_stop_transaction(
        self,
        transaction_id: int,
        meter_stop: int,
        timestamp: str,
        **kwargs,
    ):
        """
        Şarj oturumu bitirme çağrısı.
        """
        payload = {
            "cp_id": self.id,
            "transaction_id": transaction_id,
            "meter_stop": meter_stop,
            "timestamp": timestamp,
            "payload": kwargs,
        }
        self._fire_event("StopTransaction", payload)

        # Basitçe: transaction bittiğinde connector'u Available yapabiliriz.
        for conn_id, info in self._connectors.items():
            if info.get("last_tx_id") == transaction_id:
                info["status"] = "Available"

        logger.info(
            "[CSMS] StopTransaction from %s: tx_id=%s, meter_stop=%s",
            self.id,
            transaction_id,
            meter_stop,
        )

        return call_result.StopTransaction()


class CentralSystem:
    """
    Tüm CSMS server'ını yöneten sınıf.

    ScenarioBase tarafından şöyle kullanılır:
        csms = CentralSystem()
        csms.event_callback = event_callback
        asyncio.create_task(csms.start())
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9000) -> None:
        self.host = host
        self.port = port

        self.event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None

        self._server: Optional[websockets.server.Serve] = None
        self._active_cps: Dict[str, CSMSChargePoint] = {}

    async def _heartbeat_watchdog(self) -> None:
        """
        Belirli aralıklarla tüm CP'lerin heartbeat durumunu kontrol eder.
        Uzun süre heartbeat gelmeyen CP'leri OFFLINE kabul eder.
        """
        CHECK_INTERVAL = 5  # kaç saniyede bir kontrol edelim

        while True:
            await asyncio.sleep(CHECK_INTERVAL)

            now = datetime.utcnow()

            for cp_id, cp in list(self._active_cps.items()):
                last = getattr(cp, "_last_heartbeat", None)
                hb_int = getattr(cp, "_heartbeat_interval", 10)

                if last is None:
                    continue

                diff = (now - last).total_seconds()
                offline_threshold = hb_int * 3

                if diff > offline_threshold:
                    logger.warning(
                        "[CSMS] CP OFFLINE kabul edildi: cp_id=%s, last_heartbeat=%s, diff=%.1f sn",
                        cp_id,
                        last.isoformat(),
                        diff,
                    )

                    self._handle_event(
                        "CPOffline",
                        {
                            "cp_id": cp_id,
                            "last_heartbeat": last.isoformat(),
                            "diff_seconds": diff,
                        },
                    )

                    for conn_id, info in getattr(cp, "_connectors", {}).items():
                        info["status"] = "Unavailable"

                    try:
                        await cp.connection.close()
                    except Exception:
                        pass

                    self._active_cps.pop(cp_id, None)

    async def _on_connect(self, ws: WebSocketServerProtocol, path: str) -> None:
        """
        WebSocket bağlantılarını kabul eden handler.
        """
        cp_id = path.lstrip("/") or f"CP_{len(self._active_cps) + 1:03d}"

        logger.info("[CSMS] Yeni bağlantı: cp_id=%s, path=%s", cp_id, path)

        cp = CSMSChargePoint(
            charge_point_id=cp_id,
            connection=ws,
            event_callback=self._handle_event,
        )
        self._active_cps[cp_id] = cp

        try:
            await cp.start()
        except Exception:
            logger.exception("[CSMS] ChargePoint start() hata verdi (cp_id=%s)", cp_id)
        finally:
            logger.info("[CSMS] Bağlantı kapandı: cp_id=%s", cp_id)
            self._active_cps.pop(cp_id, None)

    def _handle_event(self, message_type: str, event: Dict[str, Any]) -> None:
        """
        Dışarıya event fırlatma (ScenarioBase vs. için).
        """
        if self.event_callback:
            try:
                self.event_callback(message_type, event)
            except Exception:
                logger.exception("[CSMS] event_callback hata verdi")

    async def start(self) -> None:
        """
        CSMS WebSocket server'ını başlatır. Bu fonksiyon await edildiğinde
        server dinlemeye başlar ve asyncio event loop var oldukça ayakta kalır.
        """
        logger.info("[CSMS] Server starting at %s:%s", self.host, self.port)

        self._server = await websockets.serve(
            ws_handler=self._on_connect,
            host=self.host,
            port=self.port,
        )

        logger.info("[CSMS] Server started at ws://%s:%s", self.host, self.port)

        watchdog_task = asyncio.create_task(self._heartbeat_watchdog())

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info("[CSMS] Server task cancelled, shutting down...")
        finally:
            watchdog_task.cancel()
            await self.stop()

    async def stop(self) -> None:
        """
        CSMS WebSocket server'ını durdurur.
        """
        logger.info("[CSMS] Server stopping...")

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for cp_id, cp in list(self._active_cps.items()):
            logger.info("[CSMS] Closing connection to cp=%s", cp_id)
            try:
                await cp.connection.close()
            except Exception:
                pass
        self._active_cps.clear()

        logger.info("[CSMS] Server stopped.")


# Manuel test için basit bir main
async def _run_forever():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    csms = CentralSystem()

    try:
        await csms.start()
    except asyncio.CancelledError:
        logger.info("[CSMS-MAIN] CancelledError alındı.")
    except KeyboardInterrupt:
        logger.info("[CSMS-MAIN] KeyboardInterrupt alındı.")
    finally:
        await csms.stop()


if __name__ == "__main__":
    try:
        asyncio.run(_run_forever())
    except KeyboardInterrupt:
        print("\n[CSMS-MAIN] CSMS server kullanıcı tarafından durduruldu.")
