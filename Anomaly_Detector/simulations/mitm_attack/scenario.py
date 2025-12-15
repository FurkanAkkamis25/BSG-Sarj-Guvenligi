import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

# Standart Importlar
from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class MitmAttackConfig(ScenarioConfig):
    """
    MITM (Man-in-the-Middle) ve RFID Cloning Senaryo Ayarları.
    """
    normal_latency: float = 0.05       # Normal ağ gecikmesi (saniye)
    mitm_latency_base: float = 1.5     # Saldırgan araya girince oluşan gecikme (saniye)
    manipulation_prob: float = 0.3     # Paket içeriğini bozma ihtimali (%30)


class MitmAttackScenario(ScenarioBase):
    """
    Senaryo: MITM (Ortadaki Adam) ve Veri Manipülasyonu
    
    Commit Detayı: "Added MITM simulation code... protocol manipulation... packet traces."
    
    İşleyiş:
    1. Saldırgan, kopyalanmış bir RFID kart (Cloned Tag) ile oturum açar veya mevcut oturuma sızar.
    2. Araç ile İstasyon arasındaki trafiği dinler (Sniffing) -> Bu gecikme (Latency) yaratır.
    3. Faturalandırmayı bozmak için 'MeterValues' verilerini değiştirir (Packet Manipulation).
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
        normal_latency = self.config.normal_latency
        mitm_latency_base = self.config.mitm_latency_base
        manipulation_prob = self.config.manipulation_prob

        # -----------------------------
        # 1. Başlangıç
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için MITM & Packet Manipulation Senaryosu Başlıyor...")
        for cp in cps:
            await cp.send_status_notification(connector_id=connector_id, status="Available")

        # -----------------------------
        # 2. Yetkilendirme (RFID Cloning Simülasyonu)
        # -----------------------------
        for cp in cps:
            try:
                # Normal modda orijinal kart, Attack modunda 'kopyalanmış' gibi davranan geçerli kart
                # Not: CSMS tarafında 'YUNUS_TAG' geçerli olduğu için saldırganın bunu kopyaladığını varsayıyoruz.
                id_tag = "YUNUS_TAG" 
                
                print(f"[INFO] {cp.id}: Kimlik Doğrulama İsteği ({id_tag})...")
                status = await cp.send_authorize(id_tag)

                if status != "Accepted":
                    print(f"[!] {cp.id}: Yetki reddedildi.")
                    continue

                await cp.send_status_notification(connector_id=connector_id, status="Preparing")
                
                # Şarj Başlatma
                start_res = await cp.send_start_transaction(
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=0
                )
                tx_ids[cp.id] = start_res.transaction_id
                
                await cp.send_status_notification(connector_id=connector_id, status="Charging")
                print(f"[INFO] {cp.id} Transaction Başladı (ID: {start_res.transaction_id})")
                
            except Exception as e:
                print(f"[ERROR] {cp.id} Start hatası: {e}")

        # -----------------------------
        # 3. Veri Akışı ve Manipülasyon
        # -----------------------------
        current_soc_map = {cp.id: 20.0 for cp in cps}
        
        print(f"[INFO] Veri akışı ve Paket Manipülasyonu başlıyor ({duration} adım)...")
        
        for step in range(1, duration + 1):
            for cp in cps:
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    continue

                # Temel Değerler
                real_voltage = 230.0 + random.uniform(-1, 1)
                real_current = 32.0 + random.uniform(-0.5, 0.5)
                real_power = (real_voltage * real_current) / 1000.0
                
                # --- ANOMALİ MANTIĞI ---
                is_manipulated = False
                
                if mode == "attack":
                    # 1. LATENCY (Gecikme): Saldırgan paketleri yakalayıp analiz ediyor
                    # ARP Poisoning yüzünden paketler dolaylı yoldan gidiyor
                    latency = random.uniform(mitm_latency_base * 0.8, mitm_latency_base * 1.5)
                    await asyncio.sleep(latency)
                    
                    # 2. PACKET MANIPULATION (Veri Bozma)
                    # Saldırgan %30 ihtimalle veriyi değiştiriyor (Faturalandırma dolandırıcılığı)
                    if random.random() < manipulation_prob:
                        is_manipulated = True
                        # Anlamsız değerler gönderiyor (Çok yüksek veya çok düşük)
                        msg_type = random.choice(["spike", "drop"])
                        if msg_type == "spike":
                            real_power = real_power * random.uniform(5.0, 10.0) # 5-10 kat güç
                            real_voltage = real_voltage * 1.5 # Aşırı voltaj gösterimi
                        else:
                            real_power = 0.0 # Güç yokmuş gibi göster
                            
                else:
                    # Normal mod: Düşük gecikme, doğru veri
                    await asyncio.sleep(normal_latency)

                # SoC Hesaplama (Manipülasyon SoC'yi etkilemez, fiziksel şarj devam eder varsayıyoruz)
                # Ancak raporda görünen değer bozulmuş olur.
                soc = current_soc_map.get(cp.id, 20.0)
                if soc < 100.0:
                    soc += 0.05 # Yavaşça artıyor
                current_soc_map[cp.id] = soc

                try:
                    # Eğer manipüle edildiyse, MeterValues içinde bu bozuk değerler gidecek
                    await cp.send_meter_values(
                        connector_id=connector_id,
                        power_kw=real_power,
                        current_a=(real_power * 1000) / real_voltage if real_voltage > 0 else 0,
                        voltage_v=real_voltage,
                        transaction_id=tx_id,
                        soc_percent=soc
                    )
                    
                    if is_manipulated:
                         print(f"[ATTACK] {cp.id}: Paket Manipüle Edildi! Güç: {real_power:.2f} kW")

                except Exception as e:
                    print(f"[ERROR] MeterValues gönderilemedi: {e}")

            # Global döngü (zaten latency ile yavaşlattık)
            if mode != "attack":
                await asyncio.sleep(1)

        # -----------------------------
        # 4. Bitiş
        # -----------------------------
        print(f"[INFO] Senaryo tamamlandı.")
        for cp in cps:
            tx_id = tx_ids.get(cp.id)
            if tx_id:
                await cp.send_status_notification(connector_id=connector_id, status="Finishing")
                await cp.send_stop_transaction(transaction_id=tx_id, meter_stop=100)
                await cp.send_status_notification(connector_id=connector_id, status="Available")

    # --- ETİKETLEME (LABELING) ---
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        if mode == "normal":
            return "normal"
        
        msg_type = event.get("message_type") or event.get("ocpp_action")
        
        if msg_type == "MeterValues":
            # Gelen veriye bakarak etiketleme yapalım
            power_val = event.get("power_kw", 0)
            voltage_val = event.get("voltage_v", 0)
            
            # Eğer güç aşırı yüksekse veya beklenmedik şekilde 0 ise manipülasyon vardır
            if power_val > 50.0 or (power_val == 0.0 and voltage_val > 200):
                 return "mitm_packet_manipulation"
            
            # Değerler normalse bile gecikmeden dolayı 'mitm_interception' etiketi basılabilir
            return "mitm_traffic_interception"
            
        return "attack_meta"


# --- ÇALIŞTIRICI FONKSİYON ---
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    
    config = MitmAttackConfig(
        name="mitm_attack",
        normal_latency=0.05,
        mitm_latency_base=1.5,
        manipulation_prob=0.3
    )
    
    scenario = MitmAttackScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )