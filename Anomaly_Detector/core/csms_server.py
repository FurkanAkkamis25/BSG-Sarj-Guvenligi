# core/csms_server.py
import asyncio
import logging
from datetime import datetime
from typing import Callable, Dict, Optional, Any

import websockets
from websockets.server import WebSocketServerProtocol

from ocpp.v16 import call_result
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.routing import on

logger = logging.getLogger(__name__)


class CSMSChargePoint(OcppChargePoint):
    """
    Merkez sistem tarafındaki ChargePoint implementasyonu.
    BootNotification, Authorize, StartTransaction, MeterValues, StopTransaction
    çağrılarını karşılar.
    """

    def __init__(
        self,
        charge_point_id: str,
        connection: WebSocketServerProtocol,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        meter_log_callback: Optional[Callable[[str, int, Any], None]] = None,
        
    ) -> None:
        super().__init__(charge_point_id, connection)
        self.event_callback = event_callback
        self.meter_log_callback = meter_log_callback
        self._next_tx_id = 1


    def _fire_event(self, name: str, payload: Dict[str, Any]) -> None:
        """İsteğe bağlı event callback'i çağırmak için yardımcı fonksiyon."""
        if not self.event_callback:
            return
        try:
            self.event_callback(name, payload)
        except Exception as exc:  # sadece logla, akışı bozma
            logger.exception("[CSMS] Event callback error (%s): %s", name, exc)

    # ------------------------------------------------------------------ #
    #  OCPP Handler'lar
    # ------------------------------------------------------------------ #

    @on("BootNotification")
    async def on_boot_notification(
        self,
        charge_point_model: str,
        charge_point_vendor: str,
        **kwargs,
    ):
        logger.info(
            "[CSMS] BootNotification from %s | vendor=%s model=%s payload=%s",
            self.id,
            charge_point_vendor,
            charge_point_model,
            kwargs,
        )

        self._fire_event(
            "BootNotification",
            {
                "cp_id": self.id,
                "vendor": charge_point_vendor,
                "model": charge_point_model,
                "payload": kwargs,
            },
        )

        # OCPP örnekleri böyle kullanıyor: call_result.BootNotification(...)
        return call_result.BootNotification(
            current_time=datetime.utcnow().isoformat() + "Z",
            interval=10,
            status="Accepted",
        )

    @on("Authorize")
    async def on_authorize(
        self,
        id_tag: str,
        **kwargs,
    ):
        logger.info(
            "[CSMS] Authorize from %s | id_tag=%s",
            self.id,
            id_tag,
        )

        self._fire_event(
            "Authorize",
            {
                "cp_id": self.id,
                "id_tag": id_tag,
                "payload": kwargs,
            },
        )

        return call_result.Authorize(
            id_tag_info={"status": "Accepted"}
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
        # Her yeni oturumda artan transaction id
        tx_id = self._next_tx_id
        self._next_tx_id += 1

        logger.info(
            "[CSMS] StartTransaction from %s | connector=%s id_tag=%s "
            "tx_id=%s meter_start=%s",
            self.id,
            connector_id,
            id_tag,
            tx_id,
            meter_start,
        )

        self._fire_event(
            "StartTransaction",
            {
                "cp_id": self.id,
                "connector_id": connector_id,
                "id_tag": id_tag,
                "tx_id": tx_id,
                "meter_start": meter_start,
                "timestamp": timestamp,
                "payload": kwargs,
            },
        )

        return call_result.StartTransaction(
            transaction_id=tx_id,
            id_tag_info={"status": "Accepted"},
        )

    @on("MeterValues")
    async def on_meter_values(
        self,
        connector_id: int,
        meter_value,
        transaction_id: Optional[int] = None,
        **kwargs,
    ):
        # meter_value: [{'timestamp': ..., 'sampled_value': [...]}]
        logger.info(
            "[CSMS] MeterValues from %s | connector=%s payload=%s",
            self.id,
            connector_id,
            meter_value,
        )

        # Senaryodan gelen callback varsa çağır:
        if self.meter_log_callback:
            try:
                self.meter_log_callback(self.id, connector_id, meter_value)
            except Exception as exc:
                logger.exception(
                    "[CSMS] meter_log_callback error for %s: %s",
                    self.id,
                    exc,
                )

        self._fire_event(
            "MeterValues",
            {
                "cp_id": self.id,
                "connector_id": connector_id,
                "meter_value": meter_value,
                "transaction_id": transaction_id,
                "payload": kwargs,
            },
        )

        # Payload class: call_result.MeterValues
        return call_result.MeterValues()

    @on("StopTransaction")
    async def on_stop_transaction(
        self,
        transaction_id: int,
        meter_stop: int,
        timestamp: str,
        **kwargs,
    ):
        logger.info(
            "[CSMS] StopTransaction from %s | tx_id=%s meter_stop=%s",
            self.id,
            transaction_id,
            meter_stop,
        )

        self._fire_event(
            "StopTransaction",
            {
                "cp_id": self.id,
                "tx_id": transaction_id,
                "meter_stop": meter_stop,
                "timestamp": timestamp,
                "payload": kwargs,
            },
        )

        return call_result.StopTransaction()


class CentralSystem:
    """
    CSMS sunucusu.
    - start(): websocket server'ı başlatır
    - stop(): websocket server'ı durdurur
    - meter_log_callback: dışarıdan senaryonun set ettiği callback
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9000) -> None:
        self.host = host
        self.port = port

        self._server: Optional[websockets.server.Serve] = None
        self.charge_points: Dict[str, CSMSChargePoint] = {}

        # Dışarıdan senaryo tarafından set edilebilir:
        self.event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self.meter_log_callback: Optional[Callable[[str, int, Any], None]] = None

    # ------------------------------------------------------------------ #
    #  Server lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """
        WebSocket server'ı başlatır.
        run_simulation / scenario_base bunu asyncio.create_task ile çağırıyor.
        """
        logging.info(
            "[CSMS] Starting server at ws://%s:%s", self.host, self.port
        )

        # websockets.serve ile server oluştur
        self._server = await websockets.serve(
            self._on_connect,
            self.host,
            self.port,
            subprotocols=["ocpp1.6"],
        )

    async def _on_connect(
        self,
        websocket: WebSocketServerProtocol,
        path: str,
    ) -> None:
        """
        Her yeni bağlantıda çağrılır. Path içinden CP_ID alınıp
        CSMSChargePoint oluşturulur ve cp.start() çalıştırılır.
        """
        cp_id = path.strip("/") or "UNKNOWN_CP"
        logging.info("[CSMS] Charge point connected: %s", cp_id)

        cp = CSMSChargePoint(
            cp_id,
            websocket,
            event_callback=self.event_callback,
            meter_log_callback=self.meter_log_callback,
        )
        self.charge_points[cp_id] = cp

        try:
            await cp.start()
        except websockets.exceptions.ConnectionClosedOK:
            # Normal kapanış (server shutdown vs.) hata değil.
            logging.info("[CSMS] Connection closed normally: %s", cp_id)
        except Exception as exc:
            logging.exception(
                "[CSMS] Error in ChargePoint %s: %s", cp_id, exc
            )
        finally:
            # Bağlantı sonlandığında listeden sil
            self.charge_points.pop(cp_id, None)

    async def stop(self) -> None:
        """
        Server'ı durdurur. scenario_base.run en sonda bunu çağırıyor.
        """
        if self._server is None:
            return

        logging.info("[CSMS] Stopping server...")
        self._server.close()
        try:
            await self._server.wait_closed()
        except asyncio.CancelledError:
            # Event loop kapanırken CancelledError gelebilir, önemsemiyoruz.
            pass
        logging.info("[CSMS] Server stopped.")
