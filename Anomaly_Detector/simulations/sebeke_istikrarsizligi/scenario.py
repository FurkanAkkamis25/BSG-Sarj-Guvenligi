import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Any, List

from simulations.base.scenario_base import ScenarioBase, ScenarioConfig
from core.charge_point import SimulatedChargePoint


@dataclass
class SebekeIstikrarsizligiScenarioConfig(ScenarioConfig):
    """
    Merkezi Şarj Yönetimi Üzerinden Elektrik Şebekesi İstikrarsızlığı senaryosu için konfigürasyon.
    """
    base_power_kw: float = 7.0  # Normal şarj gücü
    attack_trigger_ratio: float = 0.6  # Duration'un %60'ında saldırı tetiklenir
    voltage_v: float = 400.0  # Şebeke voltajı
    battery_capacity_kwh: float = 60.0  # Batarya kapasitesi (SoC hesaplaması için)


class SebekeIstikrarsizligiScenario(ScenarioBase):
    """
    Merkezi Şarj Yönetimi Üzerinden Elektrik Şebekesi İstikrarsızlığı (MaDEVIoT) senaryosu.
    
    Bu senaryo, CSMS'in ele geçirilmesi ve toplu RemoteStopTransaction komutlarının
    gönderilmesi sonucu şebeke frekansında ani sapmaya neden olan saldırıyı simüle eder.
    
    - normal modda: Normal şarj akışı, tüm süre boyunca devam eder
    - attack modda: Normal şarj başlar, kritik anda (duration'un %60'ında) 
      tüm transaction'lar aniden durdurulur, bu toplu RemoteStopTransaction'ı simüle eder
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
        attack_trigger_ratio = self.config.attack_trigger_ratio

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
        #    FAILED ise şarj BAŞLAMAYACAK
        # -----------------------------
        print(f"[INFO] {len(cps)} CP için Authorize işlemi başlatılıyor...")
        for cp in cps:
            try:
                # CSMS tarafındaki VALID_TAGS ile uyumlu IDTag
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
        # SoC durumu takibi
        soc_state: Dict[str, float] = {cp.id: 20.0 for cp in cps}
        
        # Attack modunda saldırının tetikleneceği adım
        attack_trigger_step = int(duration * attack_trigger_ratio) if mode == "attack" else duration + 1
        attack_triggered = False

        print(f"[INFO] MeterValues döngüsü başlatılıyor (duration={duration}, attack_trigger_step={attack_trigger_step if mode == 'attack' else 'N/A'})...")
        
        for step in range(1, duration + 1):
            # Attack modunda: Kritik anda tüm transaction'ları durdur
            if mode == "attack" and step >= attack_trigger_step and not attack_triggered:
                attack_triggered = True
                print(f"[ATTACK] Adım {step}: Toplu RemoteStopTransaction simülasyonu başlatılıyor...")
                
                # Tüm aktif transaction'ları aniden durdur
                stopped_count = 0
                for cp in cps:
                    try:
                        tx_id = tx_ids.get(cp.id)
                        if not tx_id:
                            continue
                        
                        # Finishing durumuna geç
                        await cp.send_status_notification(
                            connector_id=connector_id,
                            status="Finishing",
                        )
                        
                        # StopTransaction gönder (CSMS'in RemoteStopTransaction göndermesini simüle eder)
                        await cp.send_stop_transaction(
                            transaction_id=tx_id,
                            meter_stop=0,
                        )
                        
                        # Available durumuna geç
                        await cp.send_status_notification(
                            connector_id=connector_id,
                            status="Available",
                        )
                        
                        # Transaction ID'yi temizle (artık aktif değil)
                        tx_ids[cp.id] = None
                        stopped_count += 1
                    except Exception as e:
                        print(f"[ERROR] {cp.id} StopTransaction hatası: {e}")
                        continue
                
                print(f"[ATTACK] {stopped_count} transaction durduruldu.")
                print(f"[ATTACK] Şebeke yükünde ani düşüş simüle edildi (frekans sapması riski).")

            # Her CP için MeterValues gönder
            for cp in cps:
                try:
                    tx_id = tx_ids.get(cp.id)
                    
                    # Attack modunda ve saldırı tetiklendiyse, güç 0 olmalı
                    if mode == "attack" and attack_triggered:
                        # Saldırı sonrası: Güç 0 (şebeke yükünde ani düşüş)
                        power_kw = 0.0
                        current_a = 0.0
                        # SoC değişmez (şarj durdu)
                        soc = soc_state.get(cp.id, 20.0)
                    elif tx_id is None:
                        # Transaction yoksa veri gönderme
                        continue
                    else:
                        # Normal mod veya saldırı öncesi: Normal şarj
                        if mode == "normal":
                            # Normal mod: küçük jitter ile sabit güç
                            power_kw = base_power_kw + random.uniform(-0.3, 0.3)
                        else:
                            # Attack mod (saldırı öncesi): Normal şarj devam ediyor
                            power_kw = base_power_kw + random.uniform(-0.2, 0.2)
                        
                        # P = V * I → I = P / V
                        current_a = (power_kw * 1000) / voltage_v
                        
                        # SoC HESABI
                        dt_hours = 1.0 / 3600.0
                        energy_kwh = max(power_kw, 0.0) * dt_hours
                        delta_soc = (energy_kwh / battery_capacity_kwh) * 100.0
                        
                        soc = soc_state.get(cp.id, 20.0)
                        soc = min(100.0, soc + delta_soc)
                        soc_state[cp.id] = soc

                    # MeterValues gönder (sadece aktif transaction varsa)
                    if tx_id is not None or (mode == "attack" and attack_triggered):
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

            # Her adım arasında 1 saniye bekleyelim
            await asyncio.sleep(1)

        # -----------------------------
        # 5) StopTransaction + Finishing + Available (Normal mod veya saldırı öncesi durdurulmamışsa)
        # -----------------------------
        print(f"[INFO] Simülasyon sonu: Kalan transaction'lar durduruluyor...")
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

        # StopTransaction event'leri saldırının bir parçası
        if message_type == "StopTransaction":
            return "mass_transaction_termination_attack"
        
        # Saldırı sonrası MeterValues (güç=0) saldırı etkisini gösterir
        if message_type == "MeterValues":
            # MeterValues içinde güç değeri 0 ise saldırı sonrası
            meter_value = event.get("meter_value", [])
            power_kw = event.get("power_kw")
            if power_kw is not None and power_kw == 0.0:
                return "mass_transaction_termination_attack"
            # Saldırı öncesi normal MeterValues
            return "attack_meta"

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
    config = SebekeIstikrarsizligiScenarioConfig(
        name="sebeke_istikrarsizligi",
        base_power_kw=7.0,
        attack_trigger_ratio=0.6,
        voltage_v=400.0,
        battery_capacity_kwh=60.0,
    )
    scenario = SebekeIstikrarsizligiScenario(config=config)

    await scenario.run(
        mode=mode,
        duration=duration,
        stations=stations,
        output_path=output_path,
        cp_list=cp_list,
    )

