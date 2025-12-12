# core/charge_point.py
import ssl
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import websockets
from websockets.client import WebSocketClientProtocol

from ocpp.v16 import ChargePoint as CpBase
from ocpp.v16 import call, call_result
from ocpp.v16.enums import RegistrationStatus

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """
    OCPP mesajları için UTC ISO-8601 timestamp üretir (microsecond'suz).
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SimulatedChargePoint(CpBase):
    """
    OCPP 1.6J tabanlı, simüle edilmiş bir şarj istasyonu (Charge Point).
    Senaryo kodu bu sınıfı kullanarak:
        - BootNotification
        - Heartbeat
        - StatusNotification
        - Authorize
        - StartTransaction
        - MeterValues
        - StopTransaction
    mesajlarını gönderir.
    """

    def __init__(self, cp_id: str, connection: WebSocketClientProtocol) -> None:
        super().__init__(cp_id, connection)
        self.id = cp_id

        self._heartbeat_interval: int = 10
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._boot_accepted: bool = False

    # ------------------------------------------------------------------
    # BOOT + HEARTBEAT
    # ------------------------------------------------------------------
    async def send_boot_notification(
        self,
        model: str = "SimulatedModel",
        vendor: str = "AegisChargeSim",
    ) -> None:
        """
        CSMS'e BootNotification gönderir, dönen interval'e göre heartbeat aralığını ayarlar.
        """
        req = call.BootNotification(
            charge_point_model=model,
            charge_point_vendor=vendor,
        )

        res: call_result.BootNotification = await self.call(req)

        self._boot_accepted = res.status == RegistrationStatus.accepted
        if getattr(res, "interval", None):
            try:
                self._heartbeat_interval = int(res.interval)
            except Exception:
                logger.warning(
                    "[%s] BootNotification interval parse edilemedi: %s",
                    self.id,
                    res.interval,
                )

        logger.info(
            "[%s] BootNotification result: status=%s, interval=%s",
            self.id,
            res.status,
            self._heartbeat_interval,
        )

    async def _heartbeat_loop(self) -> None:
        """
        BootNotification'dan gelen interval'e göre periyodik Heartbeat gönderir.
        """
        if not self._boot_accepted:
            logger.warning(
                "[%s] BootNotification ACCEPTED değil, heartbeat loop yine de başlıyor.",
                self.id,
            )

        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                try:
                    req = call.Heartbeat()
                    res: call_result.Heartbeat = await self.call(req)
                    logger.info(
                        "[%s] Heartbeat -> currentTime=%s",
                        self.id,
                        getattr(res, "current_time", None),
                    )
                except Exception as exc:
                    logger.warning(
                        "[%s] Heartbeat gönderilirken hata: %s",
                        self.id,
                        exc,
                    )
                    break
        except asyncio.CancelledError:
            logger.info("[%s] Heartbeat loop cancelled.", self.id)

    async def start_heartbeat_loop(self) -> None:
        """
        Heartbeat loop'unu arka planda çalıştırır.
        """
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    # ------------------------------------------------------------------
    # STATUS NOTIFICATION
    # ------------------------------------------------------------------
    async def send_status_notification(
        self,
        connector_id: int = 1,
        status: str = "Available",
        error_code: str = "NoError",
    ) -> None:
        """
        Connector durumunu CSMS'e bildirir.
        Örn: Available, Preparing, Charging, Finishing, Faulted...
        """
        req = call.StatusNotification(
            connector_id=connector_id,
            error_code=error_code,
            status=status,
            timestamp=_utc_now_iso(),
        )
        await self.call(req)
        logger.info(
            "[%s] StatusNotification -> conn=%s, status=%s, error=%s",
            self.id,
            connector_id,
            status,
            error_code,
        )

    # ------------------------------------------------------------------
    # AUTHORIZE
    # ------------------------------------------------------------------
    async def send_authorize(self, id_tag: str) -> str:
        """
        Kullanıcı kartını doğrulamak için Authorize çağrısı yapar.
        Dönüş:
            - "Accepted"
            - "Invalid"
            - vs. (CSMS ne dönerse)
        """
        req = call.Authorize(id_tag=id_tag)
        res: call_result.Authorize = await self.call(req)

        status = res.id_tag_info.get("status", "Invalid")
        logger.info(
            "[%s] Authorize -> idTag=%s, status=%s",
            self.id,
            id_tag,
            status,
        )

        return status

    # ------------------------------------------------------------------
    # START / STOP TRANSACTION
    # ------------------------------------------------------------------
    async def send_start_transaction(
        self,
        connector_id: int,
        id_tag: str,
        meter_start: int = 0,
    ) -> call_result.StartTransaction:
        """
        Şarj oturumunu başlatır. CSMS transaction_id üretir.
        Senaryo, dönen StartTransactionResult içinden transaction_id'yi alır.
        """
        req = call.StartTransaction(
            connector_id=connector_id,
            id_tag=id_tag,
            meter_start=meter_start,
            timestamp=_utc_now_iso(),
        )

        res: call_result.StartTransaction = await self.call(req)

        logger.info(
            "[%s] StartTransaction -> conn=%s, tx_id=%s, id_tag=%s",
            self.id,
            connector_id,
            res.transaction_id,
            id_tag,
        )

        return res

    async def send_stop_transaction(
        self,
        transaction_id: int,
        meter_stop: int = 0,
    ) -> None:
        """
        Şarj oturumunu sonlandırır.
        """
        req = call.StopTransaction(
            transaction_id=transaction_id,
            meter_stop=meter_stop,
            timestamp=_utc_now_iso(),
        )

        await self.call(req)

        logger.info(
            "[%s] StopTransaction -> tx_id=%s, meter_stop=%s",
            self.id,
            transaction_id,
            meter_stop,
        )

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
        soc_percent: Optional[float] = None,
    ) -> None:
        """
        Sayaç verilerini (Voltage, Current.Import, Power.Active.Import, SoC) CSMS'e gönderir.
        Senaryo, bu fonksiyonu sadece aktif transaction varken çağırmalıdır.
        """

        sampled_values = [
            {
                "value": f"{voltage_v:.2f}",
                "measurand": "Voltage",
                "unit": "V",
            },
            {
                "value": f"{current_a:.3f}",
                "measurand": "Current.Import",
                "unit": "A",
            },
            {
                "value": f"{power_kw:.3f}",
                "measurand": "Power.Active.Import",
                "unit": "kW",
            },
        ]

        # Eğer senaryo SoC gönderiyorsa onu da sampledValue içine ekle
        if soc_percent is not None:
            sampled_values.append(
                {
                    "value": f"{soc_percent:.2f}",
                    "measurand": "SoC",
                    "unit": "Percent",
                }
            )

        meter_value = [
            {
                "timestamp": _utc_now_iso(),
                "sampledValue": sampled_values,
            }
        ]

        req = call.MeterValues(
            connector_id=connector_id,
            meter_value=meter_value,
            transaction_id=transaction_id,
        )

        await self.call(req)

        logger.debug(
            "[%s] MeterValues -> conn=%s, tx_id=%s, P=%.3f kW, I=%.3f A, V=%.2f V, SoC=%s",
            self.id,
            connector_id,
            transaction_id,
            power_kw,
            current_a,
            voltage_v,
            f"{soc_percent:.2f}%" if soc_percent is not None else "N/A",
        )


# ----------------------------------------------------------------------
# DIŞARIDAN KULLANILACAK HELPER
# ----------------------------------------------------------------------
async def connect_charge_point(
    cp_id: str,
    csms_url: str = "ws://localhost:9000",
) -> SimulatedChargePoint:
    """
    Bir WebSocket bağlantısı açar, SimulatedChargePoint oluşturur,
    BootNotification + Heartbeat loop'unu başlatır ve CP nesnesini döner.
    Senaryo, bu fonksiyonu kullanarak CP listesi oluşturur.

    TLS Mantığı:
      - Eğer URL "wss://" ile başlıyorsa veya CP_USE_TLS=1/true ise,
        TLS etkinleştirilir ve ssl context oluşturulur.
      - Aksi halde düz WS (şifresiz) bağlantı kullanılır.
    """
    logger.info("[CP-CONNECT] Connecting cp_id=%s to %s", cp_id, csms_url)

    use_tls_env = os.getenv("CP_USE_TLS", "").lower() in ("1", "true", "yes")
    is_wss = csms_url.startswith("wss://")

    ssl_ctx: Optional[ssl.SSLContext] = None
    if is_wss or use_tls_env:
        try:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            ca_file = os.getenv("CP_CA_FILE")
            if ca_file and os.path.isfile(ca_file):
                ssl_ctx.load_verify_locations(cafile=ca_file)
                logger.info("[CP-CONNECT] Using CA file for TLS verification: %s", ca_file)
            else:
                # Geliştirme ortamı için: self-signed sertifikaları kabul et.
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
                logger.warning(
                    "[CP-CONNECT] No CA file configured; disabling hostname check and certificate verification "
                    "(development mode)."
                )
        except Exception as exc:
            logger.exception("[CP-CONNECT] Failed to initialize TLS context: %s. Falling back to WS.", exc)
            ssl_ctx = None

    if ssl_ctx is not None:
        ws: WebSocketClientProtocol = await websockets.connect(csms_url, ssl=ssl_ctx)
    else:
        ws: WebSocketClientProtocol = await websockets.connect(csms_url)

    cp = SimulatedChargePoint(cp_id, ws)

    # CSMS'ten gelen çağrıları dinleyen loop (RemoteStartTransaction vs.)
    asyncio.create_task(cp.start())

    # BootNotification gönder
    await cp.send_boot_notification()
    logger.info("[%s] Connected to CSMS and BootNotification sent.", cp_id)

    # Heartbeat loop'u başlat
    await cp.start_heartbeat_loop()

    return cp

