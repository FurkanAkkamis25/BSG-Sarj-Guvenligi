import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

# Başarılı import yapısı
from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class ReverseChargingConfig(ScenarioConfig):
    """
    Tersine Şarj (Reverse Charging / Relay Attack) Konfigürasyonu.
    """
    normal_power_kw: float = 11.0   # Normal şarj hızı
    attack_power_kw: float = -22.0  # Saldırı anında ters akış (Hızlı boşaltma)
    relay_latency_ms: float = 800.0 # Relay cihazının yarattığı gecikme (ms)


class ReverseChargingScenario(ScenarioBase):
    """
    EVExchange - Tersine Şarj Etme Anomali Senaryosu.
    
    Kaynak: EVExchange: A Relay Attack on Electric Vehicle Charging System [ArXiv:2203.0526]
    
    Normal Mod:
    - Araç standart pozitif güçle şarj olur.
    - Gecikme süreleri düşüktür.
    
    Attack Modu:
    - Relay cihazı devreye girer (Yüksek Gecikme).
    - Enerji akışı tersine döner (Negative Power / V2G).
    - Batarya dolmak yerine boşalır.
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
        
        # Config
        normal_power = self.config.normal_power_kw
        attack_power = self.config.attack_power_kw
        relay_delay = self.config.relay_latency_ms / 1000.0 # saniyeye çevir

        # -----------------------------
        # 1. Başlangıç (Boot & Status)
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için Tersine Şarj (Relay Attack) Senaryosu Başlıyor...")
        for cp in cps:
            await cp.send_status_notification(connector_id=connector_id, status="Available")

        # -----------------------------
        # 2. Yetkilendirme ve Başlatma
        # -----------------------------
        for cp in cps:
            try:
                # Geçerli Kart Kullanımı
                id_tag = "YUNUS_TAG"
                status = await cp.send_authorize(id_tag)

                if status != "Accepted":
                    print(f"[!] {cp.id}: Yetki reddedildi. Şarj başlamıyor.")
                    continue

                await cp.send_status_notification(connector_id=connector_id, status="Preparing")
                
                start_res = await cp.send_start_transaction(
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=0
                )
                tx_ids[cp.id] = start_res.transaction_id
                
                await cp.send_status_notification(connector_id=connector_id, status="Charging")
                print(f"[INFO] {cp.id} İşlem Başladı. TX ID: {start_res.transaction_id}")
                
            except Exception as e:
                print(f"[ERROR] {cp.id} Start hatası: {e}")

        # -----------------------------
        # 3. MeterValues Döngüsü (Anomali Burada)
        # -----------------------------
        # Araç %50 şarjla başlasın ki tersine akışta düşebilsin
        current_soc_map = {cp.id: 50.0 for cp in cps}
        
        print(f"[INFO] Veri akışı başlıyor ({duration} adım)...")
        
        for step in range(1, duration + 1):
            for cp in cps:
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    continue

                voltage = 230.0 + random.uniform(-2, 2)
                
                # --- SENARYO MANTIĞI ---
                if mode == "attack":
                    # Saldırı Modu: Tersine Şarj ve Gecikme
                    # Güç negatife döner (Araçtan şebekeye)
                    power_kw = attack_power + random.uniform(-1.0, 1.0)
                    
                    # Relay Gecikmesi (Distance Bounding Atlatma Denemesi)
                    latency = random.uniform(relay_delay * 0.8, relay_delay * 1.2)
                    await asyncio.sleep(latency)
                else:
                    # Normal Mod
                    power_kw = normal_power + random.uniform(-0.5, 0.5)
                    await asyncio.sleep(0.05) # Normal ağ gecikmesi

                # Akım Hesabı (P = V * I -> I = P / V * 1000)
                current_a = (power_kw * 1000) / voltage

                # SoC Hesaplama (Ters akışta SoC düşmeli)
                soc = current_soc_map.get(cp.id, 50.0)
                # Power negatifse soc azalır, pozitifse artar
                soc_change = (power_kw / 60.0) * 0.5 
                soc = max(0.0, min(100.0, soc + soc_change))
                current_soc_map[cp.id] = soc

                try:
                    await cp.send_meter_values(
                        connector_id=connector_id,
                        power_kw=power_kw,
                        current_a=current_a,
                        voltage_v=voltage,
                        transaction_id=tx_id,
                        soc_percent=soc
                    )
                except Exception as e:
                    print(f"[ERROR] MeterValues gönderilemedi: {e}")

            await asyncio.sleep(1) # Global döngü hızı

        # -----------------------------
        # 4. Bitiş
        # -----------------------------
        print(f"[INFO] Senaryo tamamlandı.")
        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if tx_id:
                await cp.send_stop_transaction(transaction_id=tx_id, meter_stop=100)
                await cp.send_status_notification(connector_id=connector_id, status="Available")

    # --- ETİKETLEME (LABELING) ---
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        if mode == "normal":
            return "normal"
        
        msg_type = event.get("message_type") or event.get("ocpp_action")
        
        # MeterValues içinde Negatif Güç (Ters Akış) varsa kesin anomalidir
        if msg_type == "MeterValues":
            power_val = event.get("power_kw", 0)
            if power_val < 0:
                return "reverse_charging_anomaly"
            # Röle saldırısı sırasındaki gecikmeli mesajlar
            return "relay_attack_latency"
            
        return "attack_meta"


# --- ÇALIŞTIRICI FONKSİYON ---
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    
    config = ReverseChargingConfig(
        name="reverse_charging",
        normal_power_kw=11.0,
        attack_power_kw=-22.0, # Negatif güç = Ters akış
        relay_latency_ms=800.0
    )
    
    scenario = ReverseChargingScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )