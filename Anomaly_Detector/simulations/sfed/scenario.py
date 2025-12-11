import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class SFEDScenarioConfig(ScenarioConfig):
    """
    SFED (Stealthy Federated Energy Drift) senaryosu için konfigürasyon.
    
    Bu senaryo, enerji ölçüm verilerinin çok küçük oranlarda (%0.5-2) manipüle
    edilmesi ve bu manipülasyonların zaman içinde koordineli biçimde yürütülmesi
    durumunu simüle eder.
    """
    base_power_kw: float = 7.0  # Normal şarj gücü
    drift_percentage_min: float = 0.005  # %0.5 minimum drift
    drift_percentage_max: float = 0.02   # %2 maksimum drift
    time_offset_min: int = 5   # Minimum zaman sapması (saniye)
    time_offset_max: int = 30  # Maksimum zaman sapması (saniye)
    voltage_v: float = 400.0  # Şebeke voltajı
    battery_capacity_kwh: float = 60.0  # Batarya kapasitesi


class SFEDScenario(ScenarioBase):
    """
    SFED (Stealthy Federated Energy Drift) - Gizli Enerji Kayması Anomalisi.
    
    Bu senaryo, şarj istasyonlarının enerji ölçüm verilerini çok küçük oranlarda
    manipüle ederek ve bu manipülasyonları zaman içinde koordineli biçimde yürüten
    sinsi bir saldırıyı simüle eder.
    
    Senaryo açıklaması:
    - Saldırgan, bir grup şarj istasyonunda meter verilerini %0.5-2 oranında
      yukarı yönlü sapmalarla değiştirir
    - Aynı anda bazı istasyonlarda zaman senkronizasyonu (timestamp) birkaç
      saniye kaydırılarak kayıtların korelasyonu bozulur
    - Bu küçük farklar tekil bazda olağan dalgalanma gibi görünür
    - Ancak sistem genelinde kümülatif olarak ciddi enerji farklarına yol açar
    
    Tehdit sınıflandırması (STRIDE):
    - Spoofing: Ölçüm sonuçlarının "meşru" görünümlü biçimde manipülasyonu
    - Tampering: Meter Values mesajlarının küçük offset'lerle değiştirilmesi
    - Repudiation: Zaman sapması nedeniyle log tutarsızlığı
    - Information Disclosure: Enerji tüketim alışkanlıklarının analizi
    
    Modlar:
    - normal: Normal şarj akışı, doğru ölçüm değerleri
    - attack: Küçük enerji drift'leri (%0.5-2) + zaman sapması (5-30 saniye)
    """

    async def run_for_all_charge_points(
        self,
        cps: List[SimulatedChargePoint],
        mode: str,
        duration: int,
    ) -> None:
        """
        Tüm charge point'ler için senaryo akışını çalıştırır.
        """
        # Boş liste kontrolü
        if not cps:
            print("[WARNING] run_for_all_charge_points boş liste ile çağrıldı!")
            return
        
        connector_id = 1
        tx_ids: Dict[str, int] = {}
        
        # Config değerlerini al
        base_power_kw = self.config.base_power_kw
        voltage_v = self.config.voltage_v
        battery_capacity_kwh = self.config.battery_capacity_kwh
        
        # Attack modunda her CP için sabit drift oranı ve zaman sapması
        cp_drift_ratios: Dict[str, float] = {}
        cp_time_offsets: Dict[str, int] = {}
        
        if mode == "attack":
            for cp in cps:
                # Her CP için rastgele bir drift oranı (%0.5-2 arası)
                drift_ratio = random.uniform(
                    self.config.drift_percentage_min,
                    self.config.drift_percentage_max
                )
                cp_drift_ratios[cp.id] = drift_ratio
                
                # Her CP için rastgele bir zaman sapması (5-30 saniye)
                time_offset = random.randint(
                    self.config.time_offset_min,
                    self.config.time_offset_max
                )
                cp_time_offsets[cp.id] = time_offset
            
            print(f"[ATTACK] SFED saldırısı başlatılıyor...")
            print(f"[ATTACK] Her CP için drift oranları ve zaman sapmaları atandı:")
            for cp_id, drift in cp_drift_ratios.items():
                time_off = cp_time_offsets[cp_id]
                print(f"[ATTACK] {cp_id}: drift={drift*100:.2f}%, time_offset={time_off}s")

        # -----------------------------
        # 1) CP → CS: StatusNotification(Available)
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için StatusNotification(Available) gönderiliyor...")
        for cp in cps:
            try:
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Available",
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"[ERROR] {cp.id} StatusNotification hatası: {e}")
                continue

        # -----------------------------
        # 2) Authorize → Accepted mi?
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için Authorize işlemi başlatılıyor...")
        for cp in cps:
            try:
                id_tag = "YUNUS_TAG"
                status = await cp.send_authorize(id_tag)

                if status != "Accepted":
                    print(f"[!] {cp.id}: IDTag reddedildi → Şarj oturumu başlamayacak.")
                    continue

                # "Preparing" durumuna geç
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Preparing",
                )
                await asyncio.sleep(0.1)

                # -----------------------------
                # 3) StartTransaction
                # -----------------------------
                start_res = await cp.send_start_transaction(
                    connector_id=connector_id,
                    id_tag=id_tag,
                    meter_start=0,
                )
                tx_ids[cp.id] = start_res.transaction_id

                # "Charging" durumuna geç
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Charging",
                )
            except Exception as e:
                print(f"[ERROR] {cp.id} Authorize/StartTransaction hatası: {e}")
                continue
        
        print(f"[INFO] {len(tx_ids)} transaction başlatıldı.")

        # -----------------------------
        # 4) MeterValues DÖNGÜSÜ
        # -----------------------------
        soc_state: Dict[str, float] = {cp.id: 20.0 for cp in cps}
        
        print(f"[INFO] MeterValues döngüsü başlatılıyor (duration={duration})...")
        
        for step in range(1, duration + 1):
            for cp in cps:
                try:
                    tx_id = tx_ids.get(cp.id)
                    if tx_id is None:
                        # Transaction yoksa veri gönderme
                        continue
                    
                    # Normal güç değeri (küçük jitter ile)
                    if mode == "normal":
                        # Normal mod: küçük jitter ile sabit güç
                        power_kw = base_power_kw + random.uniform(-0.3, 0.3)
                    else:
                        # Attack mod: küçük drift ekle
                        drift_ratio = cp_drift_ratios.get(cp.id, 0.0)
                        base_power = base_power_kw + random.uniform(-0.2, 0.2)
                        
                        # Drift uygula (yukarı yönlü sapma)
                        power_kw = base_power * (1 + drift_ratio)
                        
                        # Zaman sapması (bu sadece log'da gösterilmek için)
                        # Gerçek timestamp manipülasyonu send_meter_values'da yapılamaz
                        # Bu yüzden sadece güç drift'ini uyguluyoruz
                    
                    # P = V * I → I = P / V
                    current_a = (power_kw * 1000) / voltage_v
                    
                    # SoC HESABI
                    dt_hours = 1.0 / 3600.0
                    energy_kwh = max(power_kw, 0.0) * dt_hours
                    delta_soc = (energy_kwh / battery_capacity_kwh) * 100.0
                    
                    soc = soc_state.get(cp.id, 20.0)
                    soc = min(100.0, soc + delta_soc)
                    soc_state[cp.id] = soc

                    # MeterValues gönder
                    await cp.send_meter_values(
                        connector_id=connector_id,
                        power_kw=power_kw,
                        current_a=current_a,
                        voltage_v=voltage_v,
                        transaction_id=tx_id,
                        soc_percent=soc,
                    )
                except Exception as e:
                    print(f"[ERROR] {cp.id} MeterValues hatası (step {step}): {e}")
                    continue

            # Her adım arasında 1 saniye bekle
            await asyncio.sleep(1)

        # -----------------------------
        # 5) StopTransaction + Finishing + Available
        # -----------------------------
        print(f"[INFO] Simülasyon sonu: Transaction'lar durduruluyor...")
        for cp in cps:
            try:
                tx_id = tx_ids.get(cp.id)
                if not tx_id:
                    continue

                # Finishing
                await cp.send_status_notification(
                    connector_id=connector_id,
                    status="Finishing",
                )

                await cp.send_stop_transaction(
                    transaction_id=tx_id,
                    meter_stop=0,
                )
            except Exception as e:
                print(f"[ERROR] {cp.id} StopTransaction hatası: {e}")
                continue

            # tekrar Available
            await cp.send_status_notification(
                connector_id=connector_id,
                status="Available",
            )
        
        if mode == "attack":
            print(f"[ATTACK] SFED saldırısı tamamlandı.")
            print(f"[ATTACK] Sonuç: Küçük enerji drift'leri sistem genelinde kümülatif etki yarattı.")

    # -----------------------------
    # LABEL DÖNÜŞÜ
    # -----------------------------
    def get_label_for_event(self, event: Dict[str, Any], mode: str) -> str:
        """
        Her event için anomaly label'ı belirler.
        """
        if mode == "normal":
            return "normal"

        # Attack modunda:
        message_type = event.get("message_type") or event.get("ocpp_action")

        # MeterValues event'leri saldırının bir parçası
        # (Küçük drift içeren ölçüm değerleri)
        if message_type == "MeterValues":
            return "sfed_attack"

        # Diğer her şey saldırı bağlamında meta / yardımcı kayıt
        return "attack_meta"


# ----------------------------------------------------------------------
# run_simulation.py tarafından çağrılan giriş noktası
# ----------------------------------------------------------------------
async def run_scenario(
    mode: str,
    duration: int,
    stations: int,
    output_path: str,
    cp_list: list[str] | None = None,
) -> None:
    """
    run_simulation.py'nin beklediği senaryo giriş noktası.
    """
    config = SFEDScenarioConfig(
        name="sfed",
        base_power_kw=7.0,
        drift_percentage_min=0.005,  # %0.5
        drift_percentage_max=0.02,   # %2
        time_offset_min=5,
        time_offset_max=30,
        voltage_v=400.0,
        battery_capacity_kwh=60.0,
    )
    scenario = SFEDScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )

