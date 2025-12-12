import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

# Senin sistemindeki doğru import yolları
from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class SSRFScenarioConfig(ScenarioConfig):
    """
    SSRF (Sunucu Taraflı İstek Sahteciliği) senaryosu konfigürasyonu.
    """
    base_voltage: float = 230.0
    base_current: float = 32.0
    latency_multiplier: float = 2.0  # Saldırı anında gecikme çarpanı


class SSRFScenario(ScenarioBase):
    """
    SSRF (Server-Side Request Forgery) Saldırı Senaryosu.
    """

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        
        if not cps:
            return

        connector_id = 1
        tx_ids: Dict[str, int] = {}
        
        # Config değerleri
        base_voltage = self.config.base_voltage
        base_current = self.config.base_current
        latency_multiplier = self.config.latency_multiplier

        # -----------------------------
        # 1) CP → CS: StatusNotification(Available)
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için SSRF Senaryosu Başlıyor...")
        for cp in cps:
            await cp.send_status_notification(connector_id=connector_id, status="Available")
            await asyncio.sleep(0.1)

        # -----------------------------
        # 2) Authorize & StartTransaction
        # -----------------------------
        for cp in cps:
            try:
                # DÜZELTME: CSMS'in tanıdığı geçerli ID Tag'i kullanıyoruz.
                id_tag = "YUNUS_TAG"
                
                status = await cp.send_authorize(id_tag)

                if status != "Accepted":
                    print(f"[!] {cp.id}: Yetki reddedildi (ID: {id_tag}). Şarj başlamayacak.")
                    continue

                await cp.send_status_notification(connector_id=connector_id, status="Preparing")
                
                # Şarjı başlat
                start_res = await cp.send_start_transaction(
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=0
                )
                tx_ids[cp.id] = start_res.transaction_id

                await cp.send_status_notification(connector_id=connector_id, status="Charging")
                print(f"[INFO] {cp.id} Şarj başladı. Transaction ID: {start_res.transaction_id}")
                
            except Exception as e:
                print(f"[ERROR] {cp.id} Başlatma hatası: {e}")
                continue

        # -----------------------------
        # 3) MeterValues DÖNGÜSÜ (Anomali Burada)
        # -----------------------------
        current_soc_map = {cp.id: 20.0 for cp in cps} 
        
        print(f"[INFO] Döngü ve Veri Üretimi başlıyor ({duration} adım)...")
        
        for step in range(1, duration + 1):
            for cp in cps:
                tx_id = tx_ids.get(cp.id)
                
                # Eğer transaction başlamadıysa veri gönderme
                if not tx_id:
                    continue

                # --- ANOMALİ MANTIĞI ---
                if mode == "attack":
                    # SSRF Saldırısı: Sunucu iç ağ taraması yapıyor, yanıtlar gecikiyor.
                    # Voltajda işlemci yüküne bağlı titreme (jitter) artıyor
                    voltage = base_voltage + random.uniform(-5.0, 5.0)
                    
                    # Gecikme simülasyonu (Ağ/Sunucu yavaşlaması)
                    await asyncio.sleep(random.uniform(0.1, 0.4) * latency_multiplier)
                else:
                    # Normal Mod
                    voltage = base_voltage + random.uniform(-1.0, 1.0)
                    await asyncio.sleep(0.05) 

                current = base_current + random.uniform(-0.5, 0.5)
                power_kw = (voltage * current) / 1000.0

                # SoC Güncelleme
                soc = current_soc_map.get(cp.id, 20.0)
                if soc < 100.0:
                    soc += (power_kw / 60.0) * 0.5
                current_soc_map[cp.id] = soc

                try:
                    await cp.send_meter_values(
                        connector_id=connector_id,
                        power_kw=power_kw,
                        current_a=current,
                        voltage_v=voltage,
                        transaction_id=tx_id,
                        soc_percent=soc
                    )
                except Exception as e:
                    print(f"[ERROR] {cp.id} MeterValues hatası: {e}")

            # Global döngü hızı
            await asyncio.sleep(1)

        # -----------------------------
        # 4) Bitiş (StopTransaction)
        # -----------------------------
        print(f"[INFO] Senaryo tamamlandı, işlemler durduruluyor.")
        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if tx_id:
                await cp.send_status_notification(connector_id=connector_id, status="Finishing")
                await cp.send_stop_transaction(transaction_id=tx_id, meter_stop=100)
                await cp.send_status_notification(connector_id=connector_id, status="Available")

    # --- LABEL FONKSİYONU ---
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        CSV 'Label' sütunu için etiketleme mantığı.
        """
        if mode == "normal":
            return "normal"
        
        # Attack modundaysak
        msg_type = event.get("message_type") or event.get("ocpp_action")
        
        # SSRF saldırısı MeterValues sırasında voltaj oynamalarıyla kendini belli eder
        if msg_type == "MeterValues":
            return "ssrf_attack_pattern"
        
        return "attack_meta"


# ----------------------------------------------------------------------
# BAĞLANTI NOKTASI
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    
    config = SSRFScenarioConfig(
        name="ssrf_attack",
        base_voltage=230.0,
        latency_multiplier=2.0
    )
    
    scenario = SSRFScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )